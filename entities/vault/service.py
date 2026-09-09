"""VaultService：密码本业务门面（store + crypto + session 组合）。

解锁模型（AI 优先，Agent 是系统主控）：
- **机器密钥模式（默认）**：随机机器密钥存 ``<db>.key``（0600），首次写入自动建库、
  启动/锁定后透明自动解锁，AI 与 Web 全程零摩擦
- **主密码模式（可选加固）**：主密码（仅内存，永不落盘）→ Argon2id KEK → 包裹 DEK，
  需 Web/AI 解锁，闲置自动锁定；`ANELF_VAULT_PASSWORD` 环境变量可无人值守解锁
- 两模式双向转换仅重包 DEK，条目密文不动；改主密码同理

密钥层级：
    KEK（主密码 Argon2id 派生 或 机器密钥文件）→ AES-256-GCM 包裹 → DEK
      → 逐字段加密 password / totp_secret / notes（AAD 绑定条目 id 防调换）

安全约定：
- 公开输出一律走 _public()（不含任何密文/明文敏感字段）
- 敏感操作经 ensure_unlocked() 门控（机器模式透明解锁，主密码模式需先解锁）
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from typing import Any, Dict, List, Optional

from core.config import get_config_bool, get_config_int
from core.log import log

from . import crypto, portable, search, totp
from .breach import breach_report
from .session import VaultLockedError, VaultSession
from .store import VaultStore

_UNLOCK_ENV = "ANELF_VAULT_PASSWORD"


class VaultError(Exception):
    """密码本业务错误基类。"""


class VaultAuthError(VaultError):
    """主密码错误。"""


class VaultNotInitializedError(VaultError):
    """密码本尚未初始化。"""


class VaultAlreadyInitializedError(VaultError):
    """密码本已初始化，不能重复设置。"""


class VaultCryptoStateError(VaultError):
    """密码本加密状态损坏（密钥文件丢失/校验器不匹配等）。"""


class EntryNotFoundError(VaultError):
    """条目不存在。"""


_MASK = "********"


class VaultService:
    """密码本服务（进程单例，见 get_vault_service）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.store = VaultStore(db_path)
        self.session = VaultSession()

    async def initialize(self) -> None:
        """启动初始化：机器模式自动解锁；主密码模式尝试环境变量解锁。"""
        await self.store.initialize()
        mode = await self._mode()
        if mode == "machine":
            await self._unlock_machine()
        elif mode == "master":
            env_password = os.environ.get(_UNLOCK_ENV, "")
            if env_password:
                try:
                    await self.unlock(env_password)
                except VaultAuthError:
                    log(f"密码本环境变量 {_UNLOCK_ENV} 解锁失败（主密码错误）", "WARNING")

    async def close(self) -> None:
        self.session.lock()
        await self.store.close()

    # ------------------------------------------------------------------
    # 解锁模式
    # ------------------------------------------------------------------

    async def _mode(self) -> str:
        """当前解锁模式：machine / master / ""（未初始化）。"""
        return await self.store.get_meta("unlock_mode") or ""

    async def is_initialized(self) -> bool:
        return bool(await self._mode())

    def _machine_key_path(self) -> str:
        return f"{self.store.db_path}.key"

    def _load_or_create_machine_key(self) -> bytes:
        """读取或生成机器密钥（0600 权限，与库文件同目录）。"""
        path = self._machine_key_path()
        if os.path.exists(path):
            with open(path, "rb") as f:
                key = f.read()
            if len(key) == crypto.DEK_LENGTH:
                return key
            raise VaultCryptoStateError("机器密钥文件损坏")
        key = crypto.generate_dek()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return key

    async def _unlock_machine(self) -> None:
        wrapped = await self.store.get_meta("machine_wrapped_dek")
        verifier = await self.store.get_meta("verifier")
        if not wrapped or not verifier:
            raise VaultCryptoStateError("密码本机器模式数据不完整")
        try:
            dek = crypto.unwrap_dek(wrapped, self._load_or_create_machine_key())
        except crypto.VaultCryptoError as exc:
            raise VaultCryptoStateError(
                "机器密钥无法解包数据库（密钥文件与库不匹配）") from exc
        if not crypto.check_verifier(dek, verifier):
            raise VaultCryptoStateError("密码本校验器验证失败")
        self.session.unlock(dek)  # 机器模式无 TTL，常驻解锁

    async def setup_machine(self) -> None:
        """机器密钥模式初始化：零交互，AI/系统可自助完成。"""
        if await self.is_initialized():
            raise VaultAlreadyInitializedError("密码本已初始化")
        dek = crypto.generate_dek()
        machine_key = self._load_or_create_machine_key()
        await self.store.set_meta("unlock_mode", "machine")
        await self.store.set_meta("machine_wrapped_dek", crypto.wrap_dek(dek, machine_key))
        await self.store.set_meta("verifier", crypto.make_verifier(dek))
        self.session.unlock(dek)
        log("密码本已初始化（机器密钥模式，自动解锁）", "INFO")

    async def ensure_unlocked(self) -> None:
        """敏感操作门控：机器模式透明解锁；未初始化按配置自动建库。"""
        if self.session.is_unlocked:
            return
        mode = await self._mode()
        if mode == "machine":
            await self._unlock_machine()
            return
        if not mode and get_config_bool("vault_auto_setup_enabled", True):
            await self.setup_machine()
            return
        if not mode:
            raise VaultNotInitializedError("密码本尚未初始化")
        raise VaultLockedError("密码本已锁定（主密码模式），请先解锁")

    async def status(self) -> Dict[str, Any]:
        mode = await self._mode()
        return {
            "initialized": bool(mode),
            "unlock_mode": mode,
            "unlocked": self.session.is_unlocked,
            "entry_count": await self.store.count_entries() if mode else 0,
            "auto_lock_remaining": self.session.remaining_seconds(),
        }

    # ------------------------------------------------------------------
    # 主密码模式
    # ------------------------------------------------------------------

    def _kdf_params(self) -> Dict[str, int]:
        return {
            "time_cost": get_config_int("vault_kdf_time_cost", crypto.DEFAULT_TIME_COST),
            "memory_cost": get_config_int("vault_kdf_memory_cost", crypto.DEFAULT_MEMORY_COST),
            "parallelism": get_config_int("vault_kdf_parallelism", crypto.DEFAULT_PARALLELISM),
        }

    def _lock_ttl(self) -> int:
        return max(1, get_config_int("vault_auto_lock_minutes", 15)) * 60

    async def _wrap_with_master(self, dek: bytes, master_password: str) -> None:
        params = self._kdf_params()
        salt = crypto.generate_salt()
        kek = crypto.derive_kek(master_password, salt, **params)
        await self.store.set_meta("kdf_params", json.dumps(params))
        await self.store.set_meta("kdf_salt", base64.b64encode(salt).decode("ascii"))
        await self.store.set_meta("wrapped_dek", crypto.wrap_dek(dek, kek))

    async def setup(self, master_password: str) -> None:
        """主密码模式初始化（Web 面板用户显式选择）。"""
        if await self.is_initialized():
            raise VaultAlreadyInitializedError("密码本已初始化")
        if len(master_password) < 8:
            raise VaultError("主密码至少 8 位")
        dek = crypto.generate_dek()
        await self._wrap_with_master(dek, master_password)
        await self.store.set_meta("unlock_mode", "master")
        await self.store.set_meta("verifier", crypto.make_verifier(dek))
        self.session.unlock(dek, ttl_seconds=self._lock_ttl())
        log("密码本已初始化（主密码模式）", "INFO")

    async def _load_dek_master(self, master_password: str) -> bytes:
        salt_b64 = await self.store.get_meta("kdf_salt")
        wrapped = await self.store.get_meta("wrapped_dek")
        verifier = await self.store.get_meta("verifier")
        if not salt_b64 or not wrapped or not verifier:
            raise VaultCryptoStateError("密码本主密码模式数据不完整")
        params_raw = await self.store.get_meta("kdf_params")
        try:
            params = json.loads(params_raw) if params_raw else {}
        except json.JSONDecodeError:
            params = {}
        kek = crypto.derive_kek(master_password, base64.b64decode(salt_b64), **{
            k: int(v) for k, v in params.items()
            if k in ("time_cost", "memory_cost", "parallelism")
        })
        try:
            dek = crypto.unwrap_dek(wrapped, kek)
        except crypto.VaultCryptoError as exc:
            raise VaultAuthError("主密码错误") from exc
        if not crypto.check_verifier(dek, verifier):
            raise VaultAuthError("主密码错误")
        return dek

    async def unlock(self, master_password: str = "") -> None:
        """解锁：机器模式无需密码；主密码模式校验主密码。"""
        mode = await self._mode()
        if not mode:
            raise VaultNotInitializedError("密码本尚未初始化")
        if mode == "machine":
            await self._unlock_machine()
            return
        dek = await self._load_dek_master(master_password)
        self.session.unlock(dek, ttl_seconds=self._lock_ttl())

    def lock(self) -> None:
        self.session.lock()

    async def change_master(self, old_password: str, new_password: str) -> None:
        """改主密码：仅重包 DEK（条目不重加密）。仅主密码模式。"""
        if await self._mode() != "master":
            raise VaultError("当前不是主密码模式")
        if len(new_password) < 8:
            raise VaultError("新主密码至少 8 位")
        dek = await self._load_dek_master(old_password)  # 先验证旧密码
        await self._wrap_with_master(dek, new_password)
        await self.store.set_meta("verifier", crypto.make_verifier(dek))
        self.session.unlock(dek, ttl_seconds=self._lock_ttl())
        log("密码本主密码已更换", "INFO")

    async def enable_master(self, master_password: str) -> None:
        """机器模式 → 主密码模式（需当前已解锁；重包 DEK，条目不重加密）。"""
        if await self._mode() != "machine":
            raise VaultError("仅机器密钥模式可启用主密码保护")
        if len(master_password) < 8:
            raise VaultError("主密码至少 8 位")
        dek = self.session.dek  # 需解锁态
        await self._wrap_with_master(dek, master_password)
        await self.store.set_meta("unlock_mode", "master")
        await self.store.set_meta("machine_wrapped_dek", "")
        key_path = self._machine_key_path()
        if os.path.exists(key_path):
            os.remove(key_path)
        self.session.unlock(dek, ttl_seconds=self._lock_ttl())
        log("密码本已切换为主密码模式", "INFO")

    async def disable_master(self) -> None:
        """主密码模式 → 机器模式（需当前已解锁；重包 DEK，条目不重加密）。"""
        if await self._mode() != "master":
            raise VaultError("当前不是主密码模式")
        dek = self.session.dek  # 需解锁态
        machine_key = self._load_or_create_machine_key()
        await self.store.set_meta("machine_wrapped_dek", crypto.wrap_dek(dek, machine_key))
        await self.store.set_meta("unlock_mode", "machine")
        await self.store.set_meta("wrapped_dek", "")
        await self.store.set_meta("kdf_salt", "")
        self.session.unlock(dek)  # 机器模式无 TTL
        log("密码本已切换为机器密钥模式（自动解锁）", "INFO")

    # ------------------------------------------------------------------
    # 条目 CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _public(entry: Dict[str, Any], *, score: float = 0.0) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": entry["id"],
            "title": entry["title"],
            "username": entry["username"],
            "url": entry["url"],
            "tags": entry["tags"],
            "favorite": entry["favorite"],
            "has_password": bool(entry.get("password_enc")),
            "has_totp": bool(entry.get("totp_enc")),
            "has_notes": bool(entry.get("notes_enc")),
            "created_at": entry["created_at"],
            "updated_at": entry["updated_at"],
        }
        if score:
            out["score"] = score
        return out

    @staticmethod
    def _aad(entry_id: str) -> bytes:
        return f"vault-entry:{entry_id}".encode()

    def _encrypt(self, entry_id: str, plaintext: str) -> str:
        return crypto.encrypt_field(self.session.dek, plaintext, self._aad(entry_id))

    def _decrypt(self, entry: Dict[str, Any], column: str) -> str:
        return crypto.decrypt_field(
            self.session.dek, entry.get(column, ""), self._aad(entry["id"]))

    async def add_entry(
        self,
        *,
        title: str,
        username: str = "",
        url: str = "",
        password: str = "",
        totp_secret: str = "",
        notes: str = "",
        tags: Optional[List[str]] = None,
        favorite: bool = False,
    ) -> Dict[str, Any]:
        if not title.strip():
            raise VaultError("标题不能为空")
        if totp_secret and not totp.validate_secret(totp_secret):
            raise VaultError("TOTP secret 无效")
        await self.ensure_unlocked()
        entry_id = uuid.uuid4().hex
        entry = {
            "id": entry_id,
            "title": title.strip(),
            "username": username.strip(),
            "url": url.strip(),
            "password_enc": self._encrypt(entry_id, password) if password else "",
            "totp_enc": self._encrypt(entry_id, totp_secret) if totp_secret else "",
            "notes_enc": self._encrypt(entry_id, notes) if notes else "",
            "tags": [t.strip() for t in (tags or []) if t.strip()],
            "favorite": favorite,
        }
        await self.store.insert_entry(entry)
        return self._public(await self._require_entry(entry_id))

    async def update_entry(
        self,
        entry_id: str,
        *,
        title: Optional[str] = None,
        username: Optional[str] = None,
        url: Optional[str] = None,
        password: Optional[str] = None,
        totp_secret: Optional[str] = None,
        notes: Optional[str] = None,
        tags: Optional[List[str]] = None,
        favorite: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """更新条目；敏感字段 None=保持不变、空串=清除、非空=重写。"""
        await self._require_entry(entry_id)
        fields: Dict[str, Any] = {}
        if title is not None:
            if not title.strip():
                raise VaultError("标题不能为空")
            fields["title"] = title.strip()
        if username is not None:
            fields["username"] = username.strip()
        if url is not None:
            fields["url"] = url.strip()
        if tags is not None:
            fields["tags"] = [t.strip() for t in tags if t.strip()]
        if favorite is not None:
            fields["favorite"] = favorite
        sensitive = [(password, "password_enc"), (totp_secret, "totp_enc"),
                     (notes, "notes_enc")]
        if any(value for value, _ in sensitive):
            await self.ensure_unlocked()
        for value, column in sensitive:
            if value is None:
                continue
            if value == "":
                fields[column] = ""
            else:
                if column == "totp_enc" and not totp.validate_secret(value):
                    raise VaultError("TOTP secret 无效")
                fields[column] = self._encrypt(entry_id, value)
        if not await self.store.update_entry(entry_id, fields):
            raise EntryNotFoundError(f"条目不存在: {entry_id}")
        return self._public(await self._require_entry(entry_id))

    async def delete_entry(self, entry_id: str) -> None:
        if not await self.store.delete_entry(entry_id):
            raise EntryNotFoundError(f"条目不存在: {entry_id}")

    async def get_entry(self, entry_id: str) -> Dict[str, Any]:
        return self._public(await self._require_entry(entry_id))

    async def reveal(self, entry_id: str, field: str = "password") -> str:
        """返回敏感字段明文（password/totp/notes）。"""
        column = {"password": "password_enc", "totp": "totp_enc",
                  "notes": "notes_enc"}.get(field)
        if column is None:
            raise VaultError(f"不支持的字段: {field}（可选 password/totp/notes）")
        await self.ensure_unlocked()
        entry = await self._require_entry(entry_id)
        plaintext = self._decrypt(entry, column)
        if not plaintext:
            raise VaultError(f"该条目没有{field}内容")
        return plaintext

    async def masked_entry(self, entry_id: str) -> Dict[str, Any]:
        """详情（密码掩码展示，不含明文）。"""
        entry = await self._require_entry(entry_id)
        out = self._public(entry)
        out["password"] = _MASK if entry.get("password_enc") else ""
        return out

    async def search(
        self,
        query: str = "",
        *,
        tag: str = "",
        favorite_only: bool = False,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """模糊检索（只读明文元数据，无需解锁）。"""
        entries = await self.store.list_entries()
        if tag:
            entries = [e for e in entries if tag in e["tags"]]
        if favorite_only:
            entries = [e for e in entries if e["favorite"]]
        if query.strip():
            ranked = search.rank_entries(query, entries, limit=limit)
            return [self._public(e, score=e["score"]) for e in ranked]
        return [self._public(e) for e in entries[:limit]]

    async def list_all_tags(self) -> List[str]:
        tags = set()
        for entry in await self.store.list_entries():
            tags.update(entry["tags"])
        return sorted(tags)

    async def totp_code(self, entry_id: str) -> Dict[str, Any]:
        await self.ensure_unlocked()
        entry = await self._require_entry(entry_id)
        secret = self._decrypt(entry, "totp_enc")
        if not secret:
            raise VaultError("该条目未配置 TOTP")
        result = totp.current_code(secret)
        return {"entry_id": entry_id, "title": entry["title"], **result}

    # ------------------------------------------------------------------
    # 导入导出 / 体检
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_key(draft: portable.EntryDraft) -> tuple:
        return (draft.title.strip().lower(), draft.username.strip().lower(),
                draft.url.strip().lower())

    async def import_drafts(
        self,
        drafts: List[portable.EntryDraft],
        *,
        strategy: str = "skip",
    ) -> Dict[str, int]:
        """批量导入；strategy: skip（同键跳过）/ overwrite（同键覆盖）。"""
        await self.ensure_unlocked()
        existing = {self._dedup_key(portable.EntryDraft(
            title=e["title"], username=e["username"], url=e["url"])): e["id"]
            for e in await self.store.list_entries()}
        report = {"added": 0, "skipped": 0, "overwritten": 0, "failed": 0}
        for draft in drafts:
            try:
                key = self._dedup_key(draft)
                if key in existing:
                    if strategy == "overwrite":
                        await self.update_entry(
                            existing[key], title=draft.title or "未命名",
                            username=draft.username, url=draft.url,
                            password=draft.password or None,
                            totp_secret=draft.totp or None,
                            notes=draft.notes or None,
                            favorite=draft.favorite)
                        report["overwritten"] += 1
                    else:
                        report["skipped"] += 1
                    continue
                await self.add_entry(
                    title=draft.title or "未命名", username=draft.username,
                    url=draft.url, password=draft.password,
                    totp_secret=draft.totp, notes=draft.notes,
                    tags=draft.tags, favorite=draft.favorite)
                existing[key] = ""
                report["added"] += 1
            except Exception as exc:
                log(f"密码本导入条目失败({draft.title}): {exc}", "WARNING")
                report["failed"] += 1
        return report

    async def _dump_drafts(self) -> List[portable.EntryDraft]:
        """导出全部条目的明文载体。"""
        await self.ensure_unlocked()
        drafts = []
        for e in await self.store.list_entries():
            drafts.append(portable.EntryDraft(
                title=e["title"], username=e["username"], url=e["url"],
                password=self._decrypt(e, "password_enc"),
                totp=self._decrypt(e, "totp_enc"),
                notes=self._decrypt(e, "notes_enc"),
                tags=e["tags"], favorite=e["favorite"],
            ))
        return drafts

    async def export(self, fmt: str, master_password: str = "") -> str:
        """导出：json / csv（明文）/ encrypted（独立密码加密备份）。"""
        drafts = await self._dump_drafts()
        if fmt == "json":
            return portable.export_bitwarden_json(drafts)
        if fmt == "csv":
            return portable.export_csv(drafts)
        if fmt == "encrypted":
            if not master_password:
                raise VaultError("加密导出必须提供备份密码")
            return portable.export_encrypted(drafts, master_password)
        raise VaultError(f"不支持的导出格式: {fmt}（可选 json/csv/encrypted）")

    async def import_text(
        self,
        text: str,
        fmt: str,
        *,
        strategy: str = "skip",
        master_password: str = "",
    ) -> Dict[str, int]:
        """解析文本并导入；fmt 见 portable.IMPORT_PARSERS + encrypted。"""
        if fmt == "encrypted":
            if not master_password:
                raise VaultError("导入加密备份必须提供备份密码")
            drafts = portable.import_encrypted(text, master_password)
        else:
            parser = portable.IMPORT_PARSERS.get(fmt)
            if parser is None:
                raise VaultError(f"不支持的导入格式: {fmt}")
            drafts = parser(text)
        if not drafts:
            raise VaultError("文件中没有可导入的条目")
        return await self.import_drafts(drafts, strategy=strategy)

    async def breach_check(self) -> Dict[str, Any]:
        """泄露体检：HIBP k-匿名 + 本地重复/弱密码。"""
        await self.ensure_unlocked()
        triples = [
            (e["id"], e["title"], self._decrypt(e, "password_enc"))
            for e in await self.store.list_entries()
            if e.get("password_enc")
        ]
        report = await breach_report(triples)
        report["checked_entries"] = len(triples)
        return report

    # ------------------------------------------------------------------

    async def _require_entry(self, entry_id: str) -> Dict[str, Any]:
        entry = await self.store.get_entry(entry_id)
        if entry is None:
            raise EntryNotFoundError(f"条目不存在: {entry_id}")
        return entry


_service: Optional[VaultService] = None


def get_vault_service() -> VaultService:
    """进程级密码本服务单例。"""
    global _service
    if _service is None:
        _service = VaultService()
    return _service
