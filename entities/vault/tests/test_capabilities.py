"""检索 / 生成器 / TOTP / 导入解析 测试。"""

from entities.vault import generator, portable, search, totp


class TestSearch:
    def _entry(self, **kw):
        base = {"id": "x", "title": "", "username": "", "url": "", "tags": []}
        base.update(kw)
        return base

    def test_exact_beats_substring(self):
        entries = [self._entry(title="git"), self._entry(title="github")]
        ranked = search.rank_entries("git", entries)
        assert ranked[0]["title"] == "git"

    def test_url_normalization(self):
        e = self._entry(url="https://www.github.com/login")
        # 子串命中（92+）× URL 权重 0.85 ≈ 82
        assert search.entry_score("github.com", e) >= 80.0
        # 未归一化前的裸域名查询也应命中
        assert search.entry_score("github", e) >= 70.0

    def test_min_score_filter(self):
        entries = [self._entry(title="完全无关")]
        assert search.rank_entries("zzzzqqqq", entries) == []

    def test_tag_match(self):
        e = self._entry(tags=["工作"])
        assert search.entry_score("工作", e) >= 80.0


class TestGenerator:
    def test_charset_coverage(self):
        pw = generator.generate_password(24)
        assert len(pw) == 24
        assert any(c.islower() for c in pw) and any(c.isupper() for c in pw)
        assert any(c.isdigit() for c in pw) and any(not c.isalnum() for c in pw)

    def test_exclude_ambiguous(self):
        for _ in range(20):
            pw = generator.generate_password(32, exclude_ambiguous=True)
            assert not set(pw) & set("Il1O0")

    def test_digits_only(self):
        pw = generator.generate_password(12, upper=False, lower=False, symbols=False)
        assert pw.isdigit()

    def test_no_charset_raises(self):
        import pytest

        with pytest.raises(ValueError):
            generator.generate_password(10, upper=False, lower=False,
                                        digits=False, symbols=False)

    def test_memorable(self):
        pw = generator.generate_memorable(4)
        assert pw.count("-") == 3 and pw[-2:].isdigit()

    def test_strength_levels(self):
        assert generator.assess_strength("123456")["level"] == "weak"
        strong = generator.generate_password(24)
        assert generator.assess_strength(strong)["level"] in ("strong", "excellent")


class TestTotp:
    def test_rfc6238_vector(self, monkeypatch):
        """RFC 6238 附录 B 测试向量（SHA-1，8 位码，T=59s）。"""
        import pyotp

        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890" base32
        rfc_totp = pyotp.TOTP(secret, digits=8)
        assert rfc_totp.at(59) == "94287082"

    def test_current_code_shape(self):
        result = totp.current_code("JBSWY3DPEHPK3PXP")
        assert len(result["code"]) == 6
        assert 0 < result["remaining"] <= 30

    def test_validate_secret(self):
        assert totp.validate_secret("JBSWY3DPEHPK3PXP")
        assert not totp.validate_secret("not-a-secret!!!")

    def test_otpauth_uri(self):
        uri = "otpauth://totp/GitHub:user?secret=JBSWY3DPEHPK3PXP&issuer=GitHub"
        assert totp.validate_secret(uri)


class TestPortable:
    def test_bitwarden_json(self):
        text = """{"encrypted": false, "items": [
            {"type": 1, "name": "GitHub", "notes": "n", "favorite": true,
             "login": {"username": "u", "password": "p", "uri": "https://github.com",
                       "totp": "JBSW Y3DP EHPK 3PXP"}},
            {"type": 2, "name": "安全笔记"}]}"""
        drafts = portable.parse_bitwarden_json(text)
        assert len(drafts) == 1
        d = drafts[0]
        assert d.title == "GitHub" and d.favorite and d.totp and d.password == "p"

    def test_chrome_csv(self):
        text = "name,url,username,password,note\nGitHub,https://github.com,u,p,n\n"
        drafts = portable.parse_chrome_csv(text)
        assert len(drafts) == 1 and drafts[0].url == "https://github.com"

    def test_bitwarden_csv(self):
        text = ("folder,favorite,type,name,notes,fields,reprompt,login_uri,"
                "login_username,login_password,login_totp\n"
                ",1,login,GitHub,note,,,https://github.com,u,p,\n")
        drafts = portable.parse_bitwarden_csv(text)
        assert len(drafts) == 1 and drafts[0].favorite

    def test_keepass_csv(self):
        text = ('"Group","Title","Username","Password","URL","Notes"\n'
                '"Root","GitHub","u","p","https://github.com","n"\n'
                '"Recycle Bin","Old","u","p","",""\n')
        drafts = portable.parse_keepass_csv(text)
        assert len(drafts) == 1 and drafts[0].title == "GitHub"

    def test_encrypted_roundtrip(self, monkeypatch):
        monkeypatch.setattr(portable.crypto, "DEFAULT_TIME_COST", 1)
        monkeypatch.setattr(portable.crypto, "DEFAULT_MEMORY_COST", 1024)
        monkeypatch.setattr(portable.crypto, "DEFAULT_PARALLELISM", 1)
        drafts = [portable.EntryDraft(title="A", username="u", password="p-12345")]
        blob = portable.export_encrypted(drafts, "backup-password")
        restored = portable.import_encrypted(blob, "backup-password")
        assert restored[0].password == "p-12345"
        import pytest

        from entities.vault.crypto import VaultCryptoError
        with pytest.raises(VaultCryptoError):
            portable.import_encrypted(blob, "wrong-password")
