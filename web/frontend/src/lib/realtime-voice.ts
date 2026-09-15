/** 实时语音对话客户端 — 全双工语音通道（麦克风采集 + 播放 + 状态事件）。
 *
 * 通道：/api/chat/ws（voice_start mode=realtime）。
 * 上行：AudioWorklet 采集（16k 单声道 PCM16，二进制音频帧 ~60ms/帧）；
 * 下行：二进制音频帧（48k PCM16，播放队列按时钟调度）+ JSON 事件
 * （rt_state/rt_partial/rt_final/audio_done/delta/reply/status）。
 */

export type RtState = "listening" | "thinking" | "speaking";

export interface RtEvent {
  type: string;
  [key: string]: unknown;
}

export interface RealtimeVoiceCallbacks {
  onState?: (state: RtState, turnId: number) => void;
  onPartial?: (text: string) => void;
  onFinal?: (text: string, role?: string) => void;
  /** 上行输入电平（0..1，麦克风 RMS；约每帧回调一次） */
  onLevel?: (level: number) => void;
  onAudioDone?: (turnId: number, interrupted: boolean) => void;
  onError?: (message: string) => void;
  onClose?: () => void;
}

const MAGIC = [0x4e, 0x45, 0x4b, 0x4f]; // 音频帧魔数（与后端协议约定）
const CAPTURE_RATE = 16000;
const FRAME_MS = 60;

/** 音频采集工作单元源码（内联 Blob 加载，免独立文件）。 */
const WORKLET_SRC = `
class PcmCapture extends AudioWorkletProcessor {
  constructor() { super(); this._buf = []; this._n = 0; this._chunk = ${Math.floor((CAPTURE_RATE * FRAME_MS) / 1000)}; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      this._buf.push(ch.slice(0)); this._n += ch.length;
      while (this._n >= this._chunk) {
        const out = new Float32Array(this._chunk);
        let o = 0;
        while (o < this._chunk) {
          const head = this._buf[0];
          const take = Math.min(head.length, this._chunk - o);
          out.set(head.subarray(0, take), o); o += take;
          this._buf[0] = head.subarray(take);
          if (this._buf[0].length === 0) this._buf.shift();
        }
        this._n -= this._chunk;
        this.port.postMessage(out, [out.buffer]);
      }
    }
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
`;

function encodeFrame(pcm: ArrayBuffer, sampleRate: number): ArrayBuffer {
  const header = new ArrayBuffer(8);
  const view = new DataView(header);
  MAGIC.forEach((b, i) => view.setUint8(i, b));
  view.setUint32(4, sampleRate, true);
  const out = new Uint8Array(8 + pcm.byteLength);
  out.set(new Uint8Array(header), 0);
  out.set(new Uint8Array(pcm), 8);
  return out.buffer;
}

function decodeFrame(data: ArrayBuffer): { rate: number; pcm: ArrayBuffer } | null {
  if (data.byteLength < 8) return null;
  const view = new DataView(data);
  for (let i = 0; i < 4; i++) if (view.getUint8(i) !== MAGIC[i]) return null;
  return { rate: view.getUint32(4, true), pcm: data.slice(8) };
}

function floatToPcm16(samples: Float32Array): ArrayBuffer {
  const buf = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i] ?? 0));
    view.setInt16(i * 2, s < 0 ? s * 32768 : s * 32767, true);
  }
  return buf;
}

export interface RealtimeVoiceOptions {
  userId?: string;
  userName?: string;
  chatId?: string;
}

export class RealtimeVoiceClient {
  private ws: WebSocket | null = null;
  private captureCtx: AudioContext | null = null;
  private playbackCtx: AudioContext | null = null;
  private gainNode: GainNode | null = null;
  private _volume = 1;
  private stream: MediaStream | null = null;
  private playTime = 0;
  private active = false;

  constructor(
    private cb: RealtimeVoiceCallbacks,
    private opts: RealtimeVoiceOptions = {},
  ) {}

  get isActive(): boolean {
    return this.active;
  }

  async start(): Promise<void> {
    if (this.active) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });

    const proto = location.protocol === "https:" ? "wss" : "ws";
    const params = new URLSearchParams();
    if (this.opts.userId) params.set("user_id", this.opts.userId);
    if (this.opts.userName) params.set("user_name", this.opts.userName);
    if (this.opts.chatId) params.set("chat_id", this.opts.chatId);
    const qs = params.toString();
    const ws = new WebSocket(
      `${proto}://${location.host}/api/chat/ws${qs ? `?${qs}` : ""}`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = (ev) => this.handleMessage(ev);
    ws.onclose = () => {
      this.stopCapture();
      if (this.active) {
        this.active = false;
        this.cb.onClose?.();
      }
    };

    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("语音通道连接失败"));
    });
    ws.send(JSON.stringify({
      action: "voice_start",
      mode: "realtime",
      sample_rate: CAPTURE_RATE,
      input_type: "audio",
      user_id: this.opts.userId,
      user_name: this.opts.userName,
      chat_id: this.opts.chatId,
    }));
    // 握手：等待 voice_ack（拒绝/错误帧即启动失败，不进入通话态）
    const error = await this.waitForAck(ws);
    if (error) {
      this.stopCapture();
      ws.close();
      this.ws = null;
      throw new Error(error);
    }
    await this.startCapture();
    this.active = true;
  }

  private waitForAck(ws: WebSocket): Promise<string | null> {
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve("语音服务无响应（超时）"), 8000);
      const prev = ws.onmessage;
      ws.onmessage = (ev) => {
        if (!(ev.data instanceof ArrayBuffer)) {
          try {
            const msg = JSON.parse(String(ev.data));
            if (msg.type === "voice_ack") {
              clearTimeout(timer);
              ws.onmessage = prev;
              resolve(msg.active === false ? "语音会话开启失败" : null);
              return;
            }
            if (msg.type === "status") {
              clearTimeout(timer);
              ws.onmessage = prev;
              resolve(String(msg.message?.details ?? "语音会话被拒绝"));
              return;
            }
          } catch { /* 非 JSON 帧交给主处理器 */ }
        }
        prev?.call(ws, ev);
      };
    });
  }

  private async startCapture(): Promise<void> {
    if (!this.stream) return;
    this.captureCtx = new AudioContext({ sampleRate: CAPTURE_RATE });
    const blob = new Blob([WORKLET_SRC], { type: "application/javascript" });
    await this.captureCtx.audioWorklet.addModule(URL.createObjectURL(blob));
    const source = this.captureCtx.createMediaStreamSource(this.stream);
    const node = new AudioWorkletNode(this.captureCtx, "pcm-capture");
    node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(encodeFrame(floatToPcm16(ev.data), CAPTURE_RATE));
      }
      const samples = ev.data ?? new Float32Array(0);
      let sum = 0;
      for (let i = 0; i < samples.length; i++) { const v = samples[i] ?? 0; sum += v * v; }
      this.cb.onLevel?.(Math.min(1, Math.sqrt(sum / Math.max(1, samples.length)) * 4));
    };
    source.connect(node);
    node.connect(this.captureCtx.destination);
  }

  private stopCapture(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    void this.captureCtx?.close().catch(() => undefined);
    this.captureCtx = null;
  }

  private handleMessage(ev: MessageEvent): void {
    if (ev.data instanceof ArrayBuffer) {
      const frame = decodeFrame(ev.data);
      if (frame) void this.playPcm(frame.pcm, frame.rate);
      return;
    }
    let msg: RtEvent;
    try {
      msg = JSON.parse(String(ev.data)) as RtEvent;
    } catch {
      return;
    }
    switch (msg.type) {
      case "rt_state":
        this.cb.onState?.(msg.state as RtState, Number(msg.turn_id ?? 0));
        break;
      case "rt_partial":
        this.cb.onPartial?.(String(msg.text ?? ""));
        break;
      case "rt_final":
        this.cb.onFinal?.(String(msg.text ?? ""), msg.role ? String(msg.role) : undefined);
        break;
      case "audio_done":
        this.cb.onAudioDone?.(Number(msg.turn_id ?? 0), Boolean(msg.interrupted));
        break;
      case "reply":
        // 思维回复定稿（与语音同轮）：进轮次日志
        this.cb.onFinal?.(String(msg.content ?? ""), "assistant");
        break;
      case "rt_error":
        this.cb.onError?.(String(msg.message ?? "语音通道异常"));
        break;
      case "status":
        this.cb.onError?.(String((msg.message as { details?: string })?.details ?? "通道错误"));
        break;
    }
  }

  /** 输出音量（0..1）；对播放链 gain 节点实时生效 */
  setOutputVolume(v: number): void {
    this._volume = Math.max(0, Math.min(1, v));
    if (this.gainNode) this.gainNode.gain.value = this._volume;
  }

  private async playPcm(pcm: ArrayBuffer, rate: number): Promise<void> {
    if (!this.playbackCtx) {
      this.playbackCtx = new AudioContext({ sampleRate: rate });
      this.gainNode = this.playbackCtx.createGain();
      this.gainNode.gain.value = this._volume;
      this.gainNode.connect(this.playbackCtx.destination);
      this.playTime = 0;
    }
    const ctx = this.playbackCtx;
    const count = pcm.byteLength / 2;
    const view = new DataView(pcm);
    const samples = new Float32Array(count);
    for (let i = 0; i < count; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
    const buffer = ctx.createBuffer(1, count, rate);
    buffer.copyToChannel(samples, 0);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gainNode ?? ctx.destination);
    const now = ctx.currentTime;
    this.playTime = Math.max(this.playTime, now + 0.04);
    source.start(this.playTime);
    this.playTime += count / rate;
  }

  stop(): void {
    this.active = false;
    try {
      this.ws?.send(JSON.stringify({ action: "voice_end" }));
    } catch {
      /* 连接已关闭 */
    }
    this.ws?.close();
    this.ws = null;
    this.stopCapture();
    void this.playbackCtx?.close().catch(() => undefined);
    this.playbackCtx = null;
    this.gainNode = null;
  }
}
