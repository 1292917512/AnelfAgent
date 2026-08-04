"""SSH 连接配置持久化存储。

存储于实体目录 ``connections.json``（gitignored，secrets-backup 脚本备份），
与实体设置文件 config.json 分离（后者由 Web 实体配置页读-改-写，避免互相踩踏）。

- 原子写（同目录临时文件 + os.replace），asyncio.Lock 保护并发
- 文件权限 0600（POSIX），含凭据不对外暴露
- 密码/私钥口令支持 ``${ENV_VAR}`` 引用：读-改-写全程保留原始引用语法，
  仅在建立连接时经 expand_env_refs 展开（密钥外置到环境变量）
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional

from core.log import log

# 连接名规范：中英文/数字/下划线/连字符/点，1-32 字符
_NAME_RE = re.compile(r"^[\w\u4e00-\u9fa5.-]{1,32}$")


class SshConfigStore:
    """SSH 连接配置的内存缓存 + 文件持久化。

    连接条目为 Dict[str, Any]，字段：name/host/port/username/password/
    key_path/passphrase/description/created_at/updated_at。
    """

    def __init__(self, path: str = "") -> None:
        self._path = path or os.path.join(os.path.dirname(__file__), "connections.json")
        self._lock = asyncio.Lock()
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._default: str = ""
        self._loaded = False

    # ------------------------------------------------------------------
    # 加载 / 持久化
    # ------------------------------------------------------------------

    def _load_sync(self) -> None:
        """从文件加载配置（保留 ${ENV_VAR} 原始引用，不做展开）。"""
        if not os.path.exists(self._path):
            self._profiles, self._default = {}, ""
            self._loaded = True
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            log(f"SSH 连接配置加载失败，按空配置处理: {exc}", "WARNING", tag="SSH")
            self._profiles, self._default = {}, ""
            self._loaded = True
            return

        profiles: Dict[str, Dict[str, Any]] = {}
        for item in data.get("connections", []):
            if isinstance(item, dict) and item.get("name"):
                profiles[str(item["name"])] = item
        self._profiles = profiles
        default = str(data.get("default", "") or "")
        self._default = default if default in profiles else ""
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load_sync()

    async def _write(self) -> None:
        """原子写入配置文件（0600 权限）。"""
        payload = {
            "default": self._default,
            "connections": list(self._profiles.values()),
        }
        dir_name = os.path.dirname(self._path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_profiles(self) -> List[Dict[str, Any]]:
        """返回全部连接配置（按名称排序）。"""
        self._ensure_loaded()
        return [dict(p) for _, p in sorted(self._profiles.items())]

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """按名称获取单条连接配置，不存在返回 None。"""
        self._ensure_loaded()
        profile = self._profiles.get(name)
        return dict(profile) if profile else None

    def get_default_name(self) -> str:
        """返回默认连接名（未设置时回退第一个配置）。"""
        self._ensure_loaded()
        if self._default in self._profiles:
            return self._default
        return next(iter(sorted(self._profiles)), "")

    # ------------------------------------------------------------------
    # 变更（锁内读-改-写 + 原子持久化）
    # ------------------------------------------------------------------

    @staticmethod
    def validate(profile: Dict[str, Any]) -> Optional[str]:
        """校验连接配置字段，返回错误信息（None 表示通过）。"""
        name = str(profile.get("name", "")).strip()
        if not name or not _NAME_RE.match(name):
            return "连接名须为 1-32 位中英文、数字、下划线、连字符或点"
        if not str(profile.get("host", "")).strip():
            return "主机地址不能为空"
        port_value = profile.get("port", 22)
        if port_value is None:
            port_value = 22
        try:
            port = int(port_value)
        except (TypeError, ValueError):
            return "端口须为整数"
        if not 1 <= port <= 65535:
            return "端口须在 1-65535 范围内"
        if not str(profile.get("username", "")).strip():
            return "用户名不能为空"
        if not str(profile.get("password", "")).strip() and not str(profile.get("key_path", "")).strip():
            return "密码与私钥至少提供一项"
        return None

    async def save(self, profile: Dict[str, Any], *, rename_from: str = "") -> Dict[str, Any]:
        """新增或更新连接配置，校验失败抛 ValueError。

        Args:
            profile: 连接配置字段字典（name 为目标名称）。
            rename_from: 重命名场景下的原名称（更新原条目键）。
        """
        error = self.validate(profile)
        if error:
            raise ValueError(error)

        name = str(profile["name"]).strip()
        now_ms = int(time.time() * 1000)
        async with self._lock:
            self._ensure_loaded()
            source_key = rename_from or name
            existing = self._profiles.get(source_key)
            if existing is None and rename_from:
                raise ValueError(f"原连接不存在: {rename_from}")
            if rename_from and rename_from != name and name in self._profiles:
                raise ValueError(f"连接名已存在: {name}")
            if not rename_from and name in self._profiles:
                # 更新场景：保留创建时间
                pass

            entry: Dict[str, Any] = {
                "name": name,
                "host": str(profile.get("host", "")).strip(),
                "port": int(profile.get("port", 22) or 22),
                "username": str(profile.get("username", "")).strip(),
                "password": str(profile.get("password", "") or ""),
                "key_path": str(profile.get("key_path", "") or "").strip(),
                "passphrase": str(profile.get("passphrase", "") or ""),
                "description": str(profile.get("description", "") or "").strip(),
                "created_at": existing.get("created_at", now_ms) if existing else now_ms,
                "updated_at": now_ms,
            }
            if rename_from and rename_from != name:
                self._profiles.pop(rename_from, None)
                if self._default == rename_from:
                    self._default = name
            self._profiles[name] = entry
            if len(self._profiles) == 1:
                self._default = name
            await self._write()
            return dict(entry)

    async def delete(self, name: str) -> bool:
        """删除连接配置，返回是否存在并删除成功。"""
        async with self._lock:
            self._ensure_loaded()
            if name not in self._profiles:
                return False
            self._profiles.pop(name, None)
            if self._default == name:
                self._default = next(iter(sorted(self._profiles)), "")
            await self._write()
            return True

    async def set_default(self, name: str) -> None:
        """设置默认连接，连接不存在抛 ValueError。"""
        async with self._lock:
            self._ensure_loaded()
            if name not in self._profiles:
                raise ValueError(f"连接不存在: {name}")
            self._default = name
            await self._write()


_store_instance: Optional[SshConfigStore] = None


def get_ssh_store() -> SshConfigStore:
    """获取 SshConfigStore 单例。"""
    global _store_instance
    if _store_instance is None:
        _store_instance = SshConfigStore()
    return _store_instance
