"""crypto 层测试：KDF / AES-GCM / DEK 包裹与 verifier。"""

import pytest

from entities.vault import crypto

# 测试用小参数 KDF（默认 64MiB/3 轮对单测过重）
_KDF = {"time_cost": 1, "memory_cost": 1024, "parallelism": 1}


def _kek(password: str, salt: bytes) -> bytes:
    return crypto.derive_kek(password, salt, **_KDF)


class TestKdf:
    def test_same_password_same_kek(self):
        salt = crypto.generate_salt()
        assert _kek("主密码-abc123", salt) == _kek("主密码-abc123", salt)

    def test_different_password_different_kek(self):
        salt = crypto.generate_salt()
        assert _kek("password-a", salt) != _kek("password-b", salt)

    def test_different_salt_different_kek(self):
        assert _kek("pw", crypto.generate_salt()) != _kek("pw", crypto.generate_salt())


class TestDekWrap:
    def test_wrap_unwrap_roundtrip(self):
        salt = crypto.generate_salt()
        dek = crypto.generate_dek()
        wrapped = crypto.wrap_dek(dek, _kek("pw-correct", salt))
        assert crypto.unwrap_dek(wrapped, _kek("pw-correct", salt)) == dek

    def test_unwrap_wrong_password_raises(self):
        salt = crypto.generate_salt()
        wrapped = crypto.wrap_dek(crypto.generate_dek(), _kek("pw-correct", salt))
        with pytest.raises(crypto.VaultCryptoError):
            crypto.unwrap_dek(wrapped, _kek("pw-wrong!", salt))

    def test_rewrap_keeps_dek(self):
        """改主密码场景：重包后新 KEK 可解出同一个 DEK。"""
        salt1, salt2 = crypto.generate_salt(), crypto.generate_salt()
        dek = crypto.generate_dek()
        wrapped1 = crypto.wrap_dek(dek, _kek("old-password", salt1))
        dek2 = crypto.unwrap_dek(wrapped1, _kek("old-password", salt1))
        wrapped2 = crypto.wrap_dek(dek2, _kek("new-password", salt2))
        assert crypto.unwrap_dek(wrapped2, _kek("new-password", salt2)) == dek


class TestVerifier:
    def test_verifier_accepts_correct_dek(self):
        dek = crypto.generate_dek()
        assert crypto.check_verifier(dek, crypto.make_verifier(dek))

    def test_verifier_rejects_wrong_dek(self):
        verifier = crypto.make_verifier(crypto.generate_dek())
        assert not crypto.check_verifier(crypto.generate_dek(), verifier)


class TestFieldEncryption:
    def test_roundtrip(self):
        dek = crypto.generate_dek()
        blob = crypto.encrypt_field(dek, "p@ssw0rd-秘密", b"aad-1")
        assert crypto.decrypt_field(dek, blob, b"aad-1") == "p@ssw0rd-秘密"

    def test_aad_mismatch_fails(self):
        """AAD 绑定条目 id：密文调换到别的条目下无法解密。"""
        dek = crypto.generate_dek()
        blob = crypto.encrypt_field(dek, "secret", b"entry-a")
        with pytest.raises(crypto.VaultCryptoError):
            crypto.decrypt_field(dek, blob, b"entry-b")

    def test_empty_passthrough(self):
        dek = crypto.generate_dek()
        assert crypto.encrypt_field(dek, "", b"aad") == ""
        assert crypto.decrypt_field(dek, "", b"aad") == ""

    def test_random_nonce_produces_different_ciphertext(self):
        dek = crypto.generate_dek()
        a = crypto.encrypt_field(dek, "same", b"aad")
        b = crypto.encrypt_field(dek, "same", b"aad")
        assert a != b
