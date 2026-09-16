"""频道实时语音协议 — 客户端接入的对外契约（单一定义点）。

具备 REALTIME_VOICE 能力的频道（如 webui）承载全双工语音通话：
- 传输：`/api/chat/ws`（JSON 控制 + 二进制 PCM 帧，同连接复用文字会话）
- 上行帧：`core.audio_frames.py` 的帧格式（magic 4B + LE uint32 采样率 +
  PCM16 单声道，~60ms/帧，采样率白名单 {16000, 24000, 48000}）
- 控制流：voice_start（mode=realtime，携带 user_id/user_name/chat_id/
  sample_rate）→ voice_ack（active/rejected）→ 通话事件流 → voice_end
- 会话续命：连接断开后服务端保留会话 realtime_reconnect_grace_seconds
  （默认 5s）——同一用户（同 adapter+user_id）在窗口内重新 voice_start 即
  重挂：轮次令牌、播放队列（掉线期间生产的音频接续播放）、挂起回复全部
  保留，chat_id 变化时挂起回复随迁到新会话 scope；重挂以下行 rt_state
  （resumed=true）确认，超窗自动收线。采样率变化不支持重挂（端点检测与
  预处理链按率构建），走全新会话
- 下行帧：同为二进制 PCM 帧（magic + 采样率 + PCM16，播放率默认 48k）

下行 JSON 事件（通话生命周期内）：

=====================  ==============================  =======================
事件                   载荷                           说明
=====================  ==============================  =======================
rt_state              state/turn_id                  会话状态机迁移
                                                     （listening/thinking/
                                                     speaking）
rt_partial            text/turn_id                   流式转写增量（说到一半
                                                     即显）
rt_final              text/turn_id[/discarded]       转写定稿（discarded=空
                                                     转写收帧）
audio_done            turn_id/interrupted            一轮播报完成（打断形态
                                                     带 interrupted）
rt_error              level/message                  通道异常与降级警告
voice_ack             ok/active                      启停握手回执
=====================  ==============================  =======================

对话数据划分：通话轮的用户转写与 AI 回复均写入该频道会话的对话历史
（与其他频道消息同桶），消息携带语音形态标签——历史检索、便签蒸馏与
上下文构建对语音轮与文字轮一视同仁。

接入方式：任何客户端按上述协议连 `/api/chat/ws` 即可发起通话；
能力探测经 `GET /api/adapters`（capabilities 含 realtimeVoice）。
"""

from __future__ import annotations

# 控制流 action 名（chat_ws JSON 协议）
ACTION_VOICE_START = "voice_start"
ACTION_VOICE_END = "voice_end"
MODE_REALTIME = "realtime"

# 下行事件名（与前端 lib/realtime-voice.ts 的 RtEvent 契约一致）
EVENT_RT_STATE = "rt_state"
EVENT_RT_PARTIAL = "rt_partial"
EVENT_RT_FINAL = "rt_final"
EVENT_AUDIO_DONE = "audio_done"
EVENT_RT_ERROR = "rt_error"

# 会话状态（rt_state.state 取值）
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"

__all__ = [
    "ACTION_VOICE_START", "ACTION_VOICE_END", "MODE_REALTIME",
    "EVENT_RT_STATE", "EVENT_RT_PARTIAL", "EVENT_RT_FINAL",
    "EVENT_AUDIO_DONE", "EVENT_RT_ERROR",
    "STATE_LISTENING", "STATE_THINKING", "STATE_SPEAKING",
]
