"""TOTP 验证器：pyotp 封装（RFC 6238，兼容 Google Authenticator / Authy）。

支持裸 base32 secret 与 otpauth:// URI 两种输入形态。
"""

from __future__ import annotations

import time
from typing import Dict

import pyotp


class TotpError(Exception):
    """TOTP secret 无效。"""


def _parse(secret: str) -> pyotp.TOTP:
    text = (secret or "").strip()
    if not text:
        raise TotpError("TOTP secret 为空")
    try:
        if text.lower().startswith("otpauth://"):
            parsed = pyotp.parse_uri(text)
            if not isinstance(parsed, pyotp.TOTP):
                raise TotpError("仅支持 TOTP（不支持 HOTP）")
            return parsed
        return pyotp.TOTP(text.replace(" ", "").upper())
    except TotpError:
        raise
    except Exception as exc:
        raise TotpError(f"TOTP secret 无效: {exc}") from exc


def validate_secret(secret: str) -> bool:
    """校验 secret 是否可用于生成验证码。"""
    try:
        _parse(secret).now()
        return True
    except Exception:
        return False


def current_code(secret: str) -> Dict[str, object]:
    """生成当前验证码 + 剩余有效秒数。"""
    totp = _parse(secret)
    interval = int(totp.interval)
    remaining = interval - int(time.time()) % interval
    return {"code": totp.now(), "period": interval, "remaining": remaining}
