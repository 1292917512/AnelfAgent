"""密码本加密核心：Argon2id KDF + AES-256-GCM 字段级加密（KEK/DEK 两级密钥）。

密钥层级：
    主密码（仅内存，永不落盘）
      └─ Argon2id(salt) → KEK（32B）
           └─ AES-256-GCM 包裹/解裹 → DEK（建库时随机生成 32B）
                └─ 逐字段加密 password / totp_secret / notes

改主密码 = 仅用新 KEK 重包 DEK，条目不重加密。
密文格式：base64(nonce(12B) ‖ ciphertext‖tag)，AAD 绑定条目 id 防密文跨条目调换。
"""

from __future__ import annotations

import base64
import secrets

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Argon2id 默认参数（对齐 Bitwarden 默认档：64MiB / 3 轮 / 4 并行）
DEFAULT_TIME_COST = 3
DEFAULT_MEMORY_COST = 65536  # KiB
DEFAULT_PARALLELISM = 4

KEK_LENGTH = 32
DEK_LENGTH = 32
_NONCE_LENGTH = 12
_SALT_LENGTH = 16

_VERIFIER_PLAINTEXT = b"anelf-vault-verify-v1"


class VaultCryptoError(Exception):
    """加解密失败（主密码错误或数据损坏）。"""


def generate_salt() -> bytes:
    return secrets.token_bytes(_SALT_LENGTH)


def generate_dek() -> bytes:
    return secrets.token_bytes(DEK_LENGTH)


def derive_kek(
    password: str,
    salt: bytes,
    *,
    time_cost: int = DEFAULT_TIME_COST,
    memory_cost: int = DEFAULT_MEMORY_COST,
    parallelism: int = DEFAULT_PARALLELISM,
) -> bytes:
    """从主密码派生 KEK（Argon2id）。"""
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
        hash_len=KEK_LENGTH,
        type=Type.ID,
    )


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    try:
        return base64.b64decode(text.encode("ascii"))
    except Exception as exc:
        raise VaultCryptoError("密文编码损坏") from exc


def _gcm_encrypt(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = secrets.token_bytes(_NONCE_LENGTH)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _gcm_decrypt(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) <= _NONCE_LENGTH:
        raise VaultCryptoError("密文长度异常")
    nonce, ct = blob[:_NONCE_LENGTH], blob[_NONCE_LENGTH:]
    try:
        return AESGCM(key).decrypt(nonce, ct, aad)
    except Exception as exc:
        raise VaultCryptoError("解密失败（主密码错误或数据损坏）") from exc


def encrypt_bytes(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    """AES-256-GCM 加密字节串，返回 nonce‖ciphertext（供加密备份等场景）。"""
    return _gcm_encrypt(key, plaintext, aad)


def decrypt_bytes(key: bytes, blob: bytes, aad: bytes) -> bytes:
    """decrypt_bytes 与 encrypt_bytes 互逆；失败抛 VaultCryptoError。"""
    return _gcm_decrypt(key, blob, aad)


def wrap_dek(dek: bytes, kek: bytes) -> str:
    """用 KEK 包裹 DEK，输出可落盘的 base64 串。"""
    return _b64e(_gcm_encrypt(kek, dek, b"vault-dek-wrap"))


def unwrap_dek(blob: str, kek: bytes) -> bytes:
    """解裹 DEK；主密码错误时抛 VaultCryptoError。"""
    return _gcm_decrypt(kek, _b64d(blob), b"vault-dek-wrap")


def make_verifier(dek: bytes) -> str:
    """生成解锁校验器：DEK 加密固定常量，用于验证主密码派生结果正确。"""
    return _b64e(_gcm_encrypt(dek, _VERIFIER_PLAINTEXT, b"vault-verifier"))


def check_verifier(dek: bytes, verifier: str) -> bool:
    try:
        return _gcm_decrypt(dek, _b64d(verifier), b"vault-verifier") == _VERIFIER_PLAINTEXT
    except VaultCryptoError:
        return False


def encrypt_field(dek: bytes, plaintext: str, aad: bytes) -> str:
    """加密单个敏感字段；空串原样返回（不占密文空间）。"""
    if not plaintext:
        return ""
    return _b64e(_gcm_encrypt(dek, plaintext.encode("utf-8"), aad))


def decrypt_field(dek: bytes, blob: str, aad: bytes) -> str:
    """解密单个敏感字段；空串原样返回。"""
    if not blob:
        return ""
    return _gcm_decrypt(dek, _b64d(blob), aad).decode("utf-8")
