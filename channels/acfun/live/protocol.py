"""AcFun 直播 klink 协议 — 帧编解码 + protobuf wire 原语 + 会话编解码器。

按 acfunsdk-ws 携带的 .proto 规范（AcFunDanmu 模式族）干净重实现，不引入该失修包：
- 帧 = ``abcd0001`` 魔数 + u32BE 头长 + u32BE 载荷长 + PacketHeader(protobuf) + AES-128-CBC(PKCS7) 载荷
  （IV 前置 16 字节；注册帧密钥 = ssecurity，其后 = RegisterResponse.sessKey）
- protobuf 全部手写 wire 编解码（varint / length-delimited），字段号与 .proto 一一对应
"""

from __future__ import annotations

import base64
import gzip
import os
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"\xab\xcd\x00\x01"
_HEADER_OFFSET = 12
_IV_SIZE = 16

# PacketHeader.encryptionMode
ENCRYPTION_SERVICE_TOKEN = 1  # 注册帧：密钥 = ssecurity
ENCRYPTION_SESSION_KEY = 2    # 会话帧：密钥 = sessKey

APP_ID_AC = 13  # AcFun 的 klink appId（RegisterResponse 后由服务端回显确认）


# ======================================================================
# protobuf wire 原语
# ======================================================================

def pb_varint(value: int) -> bytes:
    """无符号 varint 编码。"""
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def pb_field_varint(num: int, value: int) -> bytes:
    return pb_varint((num << 3) | 0) + pb_varint(value)


def pb_field_bytes(num: int, data: bytes) -> bytes:
    return pb_varint((num << 3) | 2) + pb_varint(len(data)) + data


def pb_field_str(num: int, text: str) -> bytes:
    return pb_field_bytes(num, text.encode("utf-8"))


def pb_field_message(num: int, encoded: bytes) -> bytes:
    return pb_field_bytes(num, encoded)


@dataclass(frozen=True)
class PbField:
    """解码后的一个 protobuf 字段。"""

    num: int
    wire: int
    value: Union[int, bytes]


def pb_parse(data: bytes) -> List[PbField]:
    """按 wire format 解析字节流（忽略未知字段类型时安全跳过）。"""
    fields: List[PbField] = []
    pos = 0
    size = len(data)
    while pos < size:
        tag, pos = _read_varint(data, pos)
        num, wire = tag >> 3, tag & 0x07
        value: Union[int, bytes]
        if wire == 0:
            value, pos = _read_varint(data, pos)
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            value = data[pos: pos + length]
            pos += length
        elif wire == 1:
            value = data[pos: pos + 8]
            pos += 8
        elif wire == 5:
            value = data[pos: pos + 4]
            pos += 4
        else:
            break  # 分组类型已废弃，剩余按无法解析丢弃
        fields.append(PbField(num, wire, value))
    return fields


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def pb_get_varint(fields: List[PbField], num: int, default: int = 0) -> int:
    for f in fields:
        if f.num == num and f.wire == 0:
            return int(f.value)
    return default


def pb_get_bytes(fields: List[PbField], num: int) -> Optional[bytes]:
    for f in fields:
        if f.num == num and f.wire == 2:
            return bytes(f.value)
    return None


def pb_get_str(fields: List[PbField], num: int, default: str = "") -> str:
    raw = pb_get_bytes(fields, num)
    if raw is None:
        return default
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return default


def pb_get_all_bytes(fields: List[PbField], num: int) -> List[bytes]:
    return [bytes(f.value) for f in fields if f.num == num and f.wire == 2]


# ======================================================================
# AES-128-CBC（PKCS7，IV 前置）
# ======================================================================

def _pkcs7_pad(data: bytes) -> bytes:
    pad_len = 16 - (len(data) % 16)
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise ValueError("AES 载荷长度非法")
    pad_len = data[-1]
    if not 1 <= pad_len <= 16:
        raise ValueError("AES 填充非法")
    return data[:-pad_len]


def aes_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """AES-CBC 加密，返回 IV + 密文。"""
    iv = os.urandom(_IV_SIZE)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return iv + encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def aes_decrypt(key: bytes, data: bytes) -> bytes:
    """AES-CBC 解密（data = IV + 密文）。"""
    if len(data) < _IV_SIZE + 16:
        raise ValueError("AES 载荷过短")
    iv, ciphertext = data[:_IV_SIZE], data[_IV_SIZE:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return _pkcs7_unpad(decryptor.update(ciphertext) + decryptor.finalize())


# ======================================================================
# klink 帧与载荷模型
# ======================================================================

@dataclass
class PacketHeader:
    """Im.basic.PacketHeader（字段号对齐 .proto：appId=1 uid=2 instanceId=3 flags=5
    encodingType=6 decodedPayloadLen=7 encryptionMode=8 tokenInfo=9 seqId=10 kpn=12）。"""

    app_id: int = 0
    uid: int = 0
    instance_id: int = 0
    seq_id: int = 0
    encryption_mode: int = ENCRYPTION_SESSION_KEY
    decoded_payload_len: int = 0
    token_type: int = 1          # TokenInfo.tokenType（1 = kServiceToken）
    token: bytes = b""
    kpn: str = "ACFUN_APP"

    def encode(self) -> bytes:
        token_info = pb_field_varint(1, self.token_type) + pb_field_bytes(2, self.token)
        return b"".join([
            pb_field_varint(1, self.app_id) if self.app_id else b"",
            pb_field_varint(2, self.uid),
            pb_field_varint(3, self.instance_id),
            pb_field_varint(7, self.decoded_payload_len),
            pb_field_varint(8, self.encryption_mode),
            pb_field_message(9, token_info),
            pb_field_varint(10, self.seq_id),
            pb_field_str(12, self.kpn),
        ])

    @classmethod
    def decode(cls, data: bytes) -> "PacketHeader":
        fields = pb_parse(data)
        token_raw = pb_get_bytes(fields, 9)
        token, token_type = b"", 1
        if token_raw is not None:
            inner = pb_parse(token_raw)
            token = pb_get_bytes(inner, 2) or b""
            token_type = pb_get_varint(inner, 1, 1)
        return cls(
            app_id=pb_get_varint(fields, 1),
            uid=pb_get_varint(fields, 2),
            instance_id=pb_get_varint(fields, 3),
            decoded_payload_len=pb_get_varint(fields, 7),
            encryption_mode=pb_get_varint(fields, 8, ENCRYPTION_SESSION_KEY),
            token=token,
            token_type=token_type,
            seq_id=pb_get_varint(fields, 10),
            kpn=pb_get_str(fields, 12, "ACFUN_APP"),
        )


def build_frame(header: PacketHeader, key: bytes, payload_plain: bytes) -> bytes:
    """组帧：魔数 + 长度前缀 + header + AES 载荷。"""
    header_bytes = header.encode()
    encrypted = aes_encrypt(key, payload_plain)
    return MAGIC + struct.pack(">II", len(header_bytes), len(encrypted)) + header_bytes + encrypted


@dataclass
class DownstreamPayload:
    """Im.basic.DownstreamPayload（command=1 seqId=2 errorCode=3 payloadData=4 errorMsg=5）。"""

    command: str = ""
    seq_id: int = 0
    error_code: int = 0
    payload_data: bytes = b""
    error_msg: str = ""
    header: Optional[PacketHeader] = None

    @property
    def is_error(self) -> bool:
        return bool(self.error_code) or bool(self.error_msg)


class ProtocolError(Exception):
    """klink 帧解析/解密失败。"""


def parse_frame(raw: bytes, ssecurity_key: bytes, sess_key: bytes) -> DownstreamPayload:
    """解帧 + 解密 + 解析下行载荷；encryptionMode=0 时按 ErrorMessage 抛出。"""
    if len(raw) < _HEADER_OFFSET or not raw.startswith(MAGIC):
        raise ProtocolError("klink 帧魔数/长度非法")
    header_len, payload_len = struct.unpack(">II", raw[4:_HEADER_OFFSET])
    if len(raw) < _HEADER_OFFSET + header_len + payload_len:
        raise ProtocolError("klink 帧长度前缀超出实际数据")
    header = PacketHeader.decode(raw[_HEADER_OFFSET:_HEADER_OFFSET + header_len])
    encrypted = raw[_HEADER_OFFSET + header_len: _HEADER_OFFSET + header_len + payload_len]
    if header.encryption_mode == ENCRYPTION_SERVICE_TOKEN:
        plain = aes_decrypt(ssecurity_key, encrypted)
    elif header.encryption_mode == ENCRYPTION_SESSION_KEY:
        plain = aes_decrypt(sess_key, encrypted)
    else:
        fields = pb_parse(encrypted)
        raise ProtocolError(f"服务端错误帧: {pb_get_str(fields, 1)[:200]}")
    fields = pb_parse(plain)
    payload = pb_get_bytes(fields, 4) or b""
    return DownstreamPayload(
        command=pb_get_str(fields, 1),
        seq_id=pb_get_varint(fields, 2),
        error_code=pb_get_varint(fields, 3),
        payload_data=payload,
        error_msg=pb_get_str(fields, 5),
        header=header,
    )


# ======================================================================
# 会话编解码器（每连接一个，持单调 seqId 与会话密钥状态）
# ======================================================================

def _register_request_body(uid: int, did: str, instance_id: int) -> bytes:
    """Im.basic.RegisterRequest（appInfo=1 deviceInfo=2 presenceStatus=4
    appActiveStatus=5 instanceId=8 ztCommonInfo=11）。"""
    app_info = pb_field_str(4, "kwai-acfun-live-link") + pb_field_str(5, "2.13.8")
    device_info = (
        pb_field_varint(1, 9)   # platformType = H5_WINDOWS
        + pb_field_str(3, "h5")
        + pb_field_str(5, did)
    )
    zt_common = (
        pb_field_str(1, "ACFUN_APP")
        + pb_field_str(2, "PC_WEB")
        + pb_field_varint(4, uid)
        + pb_field_str(5, did)
    )
    return b"".join([
        pb_field_message(1, app_info),
        pb_field_message(2, device_info),
        pb_field_varint(4, 1),  # kPresenceOnline
        pb_field_varint(5, 1),  # kAppInForeground
        pb_field_varint(8, instance_id),
        pb_field_message(11, zt_common),
    ])


def _upstream(command: str, seq_id: int, payload: bytes, sub_biz: str = "") -> bytes:
    """Im.basic.UpstreamPayload（command=1 seqId=2 retryCount=3 payloadData=4 subBiz=9）。"""
    return b"".join([
        pb_field_str(1, command),
        pb_field_varint(2, seq_id),
        pb_field_varint(3, 1),
        pb_field_bytes(4, payload),
        pb_field_str(9, sub_biz) if sub_biz else b"",
    ])


def _keepalive_body() -> bytes:
    """Basic.KeepAliveRequest（presenceStatus=1 appActiveStatus=2 keepaliveIntervalSec=5）。"""
    return pb_field_varint(1, 1) + pb_field_varint(2, 1) + pb_field_varint(5, 120)


def _client_config_body() -> bytes:
    """Basic.ClientConfigGetRequest（version=1）。"""
    return pb_field_varint(1, 1)


def _cs_cmd_body(cmd_type: str, payload: bytes, live_id: str, ticket: str) -> bytes:
    """Global.ZtLiveInteractive.CsCmd（cmdType=1 payload=2 ticket=3 liveId=4）。"""
    return (
        pb_field_str(1, cmd_type)
        + pb_field_bytes(2, payload)
        + pb_field_str(3, ticket)
        + pb_field_str(4, live_id)
    )


def enter_room_body(enter_room_attach: str, is_author: bool,
                    reconnect_count: int = 0, last_error: int = 0) -> bytes:
    """ZtLiveCsEnterRoom（isAuthor=1 reconnectCount=2 lastErrorCode=3
    enterRoomAttach=4 clientLiveSdkVersion=5）。"""
    return b"".join([
        pb_field_varint(1, 1 if is_author else 0),
        pb_field_varint(2, reconnect_count),
        pb_field_varint(3, last_error),
        pb_field_str(4, enter_room_attach),
        pb_field_str(5, "kwai-acfun-live-link"),
    ])


def heartbeat_body(client_ts_ms: int, sequence: int) -> bytes:
    """ZtLiveCsHeartbeat（clientTimestampMs=1 sequence=2）。"""
    return pb_field_varint(1, client_ts_ms) + pb_field_varint(2, sequence)


def user_exit_body() -> bytes:
    """ZtLiveCsUserExit（空消息）。"""
    return b""


def parse_cs_cmd_ack(payload: bytes) -> Tuple[str, int, str, bytes]:
    """解析 ZtLiveCsCmdAck → (cmdAckType, errorCode, errorMsg, payload)。"""
    fields = pb_parse(payload)
    return (
        pb_get_str(fields, 1),
        pb_get_varint(fields, 2),
        pb_get_str(fields, 3),
        pb_get_bytes(fields, 4) or b"",
    )


def parse_register_response(payload: bytes) -> Tuple[bytes, int]:
    """解析 Basic.RegisterResponse → (sessKey, instanceId)。"""
    fields = pb_parse(payload)
    return pb_get_bytes(fields, 2) or b"", pb_get_varint(fields, 3)


def parse_enter_room_ack(payload: bytes) -> int:
    """解析 ZtLiveCsEnterRoomAck → heartbeatIntervalMs。"""
    return pb_get_varint(pb_parse(payload), 1)


class KlinkCodec:
    """单连接的 klink 编解码器：维护 seqId 单调计数、appId/instanceId 与会话密钥。"""

    def __init__(self, uid: int, did: str, ssecurity: str, service_token: str) -> None:
        self._uid = uid
        self._did = did
        self._ssecurity_key = base64.standard_b64decode(ssecurity)
        self._service_token = service_token.encode("utf-8")
        self._sess_key: bytes = b""
        self._app_id = 0
        self._instance_id = 0
        self._seq = 0

    @property
    def has_session(self) -> bool:
        return bool(self._sess_key)

    def adopt_session(self, sess_key: bytes, instance_id: int, app_id: int) -> None:
        self._sess_key = sess_key
        self._instance_id = instance_id
        self._app_id = app_id or APP_ID_AC

    def _frame(self, command: str, payload: bytes, sub_biz: str = "") -> bytes:
        """组上行帧：注册前用 ssecurity（mode 1），注册后用 sessKey（mode 2）。"""
        self._seq += 1
        plain = _upstream(command, self._seq, payload, sub_biz)
        if self._sess_key:
            key, mode = self._sess_key, ENCRYPTION_SESSION_KEY
        else:
            key, mode = self._ssecurity_key, ENCRYPTION_SERVICE_TOKEN
        header = PacketHeader(
            app_id=self._app_id,
            uid=self._uid,
            instance_id=self._instance_id,
            seq_id=self._seq,
            encryption_mode=mode,
            decoded_payload_len=len(plain),
            token_type=1,
            token=self._service_token,
        )
        return build_frame(header, key, plain)

    def register(self) -> bytes:
        return self._frame("Basic.Register", _register_request_body(self._uid, self._did, self._instance_id))

    def client_config_get(self) -> bytes:
        return self._frame("Basic.ClientConfigGet", _client_config_body())

    def keepalive(self) -> bytes:
        return self._frame("Basic.KeepAlive", _keepalive_body())

    def unregister(self) -> bytes:
        return self._frame("Basic.Unregister", b"")

    def push_reply(self) -> bytes:
        return self._frame("Push.ZtLiveInteractive.Message", b"", "mainApp")

    def cs_cmd(self, cmd_type: str, payload: bytes, live_id: str, ticket: str) -> bytes:
        return self._frame(
            "Global.ZtLiveInteractive.CsCmd", _cs_cmd_body(cmd_type, payload, live_id, ticket), "mainApp",
        )

    def decode(self, raw: bytes) -> DownstreamPayload:
        return parse_frame(raw, self._ssecurity_key, self._sess_key or self._ssecurity_key)


# ======================================================================
# ZtLiveSc 下行消息（Push.ZtLiveInteractive.Message 载荷）
# ======================================================================

@dataclass
class ScSignal:
    """解包后的一个信号条目（action 类允许批量 payload）。"""

    message_type: str
    signal_type: str
    payloads: List[bytes] = field(default_factory=list)
    live_id: str = ""
    server_ts_ms: int = 0


def decode_sc_message(payload: bytes) -> List[ScSignal]:
    """解包 ZtLiveScMessage → 信号条目列表（含 gzip 解压与三类信号束展开）。

    messageType：ZtLiveScActionSignal / ZtLiveScStateSignal / ZtLiveScNotifySignal /
    ZtLiveScStatusChanged / ZtLiveScTicketInvalid（后两者单载荷直接产出）。
    """
    fields = pb_parse(payload)
    message_type = pb_get_str(fields, 1)
    compression = pb_get_varint(fields, 2)  # 0 UNKNOWN / 1 NONE / 2 GZIP
    inner = pb_get_bytes(fields, 3) or b""
    if compression == 2:
        try:
            inner = gzip.decompress(inner)
        except OSError as exc:
            raise ProtocolError(f"ZtLiveSc gzip 解压失败: {exc}") from exc
    live_id = pb_get_str(fields, 4)
    server_ts = pb_get_varint(fields, 6)

    if message_type in ("ZtLiveScStatusChanged", "ZtLiveScTicketInvalid"):
        return [ScSignal(message_type, message_type, [inner], live_id, server_ts)]

    # 三类信号束：items 在束载荷（解压后）的 field 1
    bundle_fields = pb_parse(inner)
    items_raw = pb_get_all_bytes(bundle_fields, 1)
    signals: List[ScSignal] = []
    for item_raw in items_raw:
        item = pb_parse(item_raw)
        signal_type = pb_get_str(item, 1)
        if message_type == "ZtLiveScActionSignal":
            payloads = pb_get_all_bytes(item, 2)  # action 信号 payload 为 repeated
        else:
            single = pb_get_bytes(item, 2)
            payloads = [single] if single is not None else []
        if signal_type:
            signals.append(ScSignal(message_type, signal_type, payloads, live_id, server_ts))
    return signals
