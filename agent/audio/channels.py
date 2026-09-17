"""声纹信道归一：来源/设备标识 → 规范信道分类。

同一人声经不同软件/设备/线路提取的嵌入存在信道漂移（采样率、编码
压缩、麦克风频响、AGC/降噪各不相同）。样本与匹配按信道标注后，
匹配引擎可用同信道模板补偿漂移——"知道渠道就和对应渠道模板比"。
"""

from __future__ import annotations

_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("voip", ("realtime", "voip", "call", "webrtc", "通话")),
    ("phone", ("phone", "tel", "电话", "手机")),
    ("chat", ("wechat", "weixin", "微信", "qq", "telegram", "discord")),
    ("web", ("web", "upload", "webui", "browser", "浏览器")),
    ("enroll", ("enroll", "import", "register", "注册")),
    ("mic", ("mic", "recorder", "record", "sync", "录音", "麦克风")),
)


def normalize_channel(raw: str, default: str = "mic") -> str:
    """归一信道标识：关键字命中返回 canonical 信道，否则回落 default。"""
    text = (raw or "").strip().lower()
    if not text:
        return default
    for channel, keywords in _PATTERNS:
        if any(k in text for k in keywords):
            return channel
    return default
