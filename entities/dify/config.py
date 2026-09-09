"""Dify 实体密钥与运行状态存储（secrets.json，实体目录内，0600 权限）。

与实体设置文件 config.json 分离：config.json 由 Web 实体配置页读-改-写
（非敏感开关/地址/版本号），本模块管理的 secrets.json 存放管理员凭据、
按应用的 API Key、MCP server_code 映射与部署指纹，出站一律脱敏。

- 原子写（同目录临时文件 + os.replace），threading.RLock 串行化
  （AI 工具在工作线程、HTTP 路由经 to_thread，均为同步调用点）
- 文件权限 0600（POSIX），含凭据不对外暴露
- 脱敏约定：出站位 ``********``；提交时占位符还原真实值（仿 entities/mcp/config.py）
"""

from __future__ import annotations

import json
import os
import secrets as _secrets
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from core.log import log

_SECRET_MASK = "********"

# 密钥字段（出站脱敏 + 提交还原共用）
_SECRET_FIELDS = ("admin_password", "api_key")


class DifySecretsStore:
    """Dify 实体的密钥与运行状态存取（内存缓存 + 文件持久化）。

    顶层结构::

        {
          "admin": {"email": str, "password": str, "created_by": "auto"|"manual"},
          "apps":  {"<app_id>": {"name": str, "api_key": str, "api_key_id": str,
                                 "mcp_server_code": str, "mcp_server_id": str}},
          "deploy": {"version": str, "deployed_at": float, "base_url": str}
        }
    """

    def __init__(self, path: str = "") -> None:
        self._path = path or os.path.join(os.path.dirname(__file__), "secrets.json")
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # 加载 / 持久化
    # ------------------------------------------------------------------

    def _load_sync(self) -> None:
        if not os.path.exists(self._path):
            self._data = {}
            self._loaded = True
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._data = data if isinstance(data, dict) else {}
        except Exception as exc:
            log(f"Dify 密钥存储加载失败，按空配置处理: {exc}", "WARNING", tag="Dify")
            self._data = {}
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load_sync()

    def _write_locked(self) -> None:
        """原子写入（调用方须持锁），0600 权限。"""
        dir_name = os.path.dirname(self._path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
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

    def _mutate(self, fn) -> Any:
        """锁内读-改-写：fn(self._data) 的返回值作为结果，随后持久化。"""
        with self._lock:
            self._ensure_loaded()
            result = fn(self._data)
            self._write_locked()
            return result

    # ------------------------------------------------------------------
    # 管理员凭据
    # ------------------------------------------------------------------

    def get_admin(self) -> Dict[str, str]:
        """返回管理员凭据 {"email", "password", "created_by"}（可能为空）。"""
        with self._lock:
            self._ensure_loaded()
            admin = self._data.get("admin") or {}
            return dict(admin) if isinstance(admin, dict) else {}

    def set_admin(self, email: str, password: str, created_by: str = "manual") -> None:
        """保存管理员凭据（created_by: auto=实体自动初始化，manual=用户提供）。"""
        def _apply(data: Dict[str, Any]) -> None:
            data["admin"] = {
                "email": email.strip(),
                "password": password,
                "created_by": created_by,
            }
        self._mutate(_apply)

    def clear_admin(self) -> None:
        def _apply(data: Dict[str, Any]) -> None:
            data.pop("admin", None)
        self._mutate(_apply)

    # ------------------------------------------------------------------
    # 应用级凭据（API Key / MCP server_code）
    # ------------------------------------------------------------------

    def get_app(self, app_id: str) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            entry = (self._data.get("apps") or {}).get(app_id) or {}
            return dict(entry) if isinstance(entry, dict) else {}

    def list_apps(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            apps = self._data.get("apps") or {}
            return {k: dict(v) for k, v in apps.items() if isinstance(v, dict)}

    def find_app_by_name(self, name: str) -> Optional[str]:
        """按应用名反查 app_id（AI 工具友好：允许用名字引用应用），无匹配返回 None。"""
        with self._lock:
            self._ensure_loaded()
            for app_id, entry in (self._data.get("apps") or {}).items():
                if isinstance(entry, dict) and entry.get("name") == name:
                    return app_id
        return None

    def update_app(self, app_id: str, **fields: Any) -> Dict[str, Any]:
        """合并写入应用条目（仅覆盖传入字段）。"""
        def _apply(data: Dict[str, Any]) -> Dict[str, Any]:
            apps = data.setdefault("apps", {})
            entry = dict(apps.get(app_id) or {})
            for key, value in fields.items():
                if value is None:
                    entry.pop(key, None)
                else:
                    entry[key] = value
            apps[app_id] = entry
            return dict(entry)
        return self._mutate(_apply)

    def remove_app(self, app_id: str) -> None:
        def _apply(data: Dict[str, Any]) -> None:
            (data.get("apps") or {}).pop(app_id, None)
        self._mutate(_apply)

    def get_api_key(self, app_id: str) -> str:
        """取应用的运行时 API Key（无则空串）。"""
        return str(self.get_app(app_id).get("api_key") or "")

    # ------------------------------------------------------------------
    # 部署状态
    # ------------------------------------------------------------------

    def get_deploy(self) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            deploy = self._data.get("deploy") or {}
            return dict(deploy) if isinstance(deploy, dict) else {}

    def set_deploy(self, **fields: Any) -> None:
        def _apply(data: Dict[str, Any]) -> None:
            deploy = dict(data.get("deploy") or {})
            for key, value in fields.items():
                if value is None:
                    deploy.pop(key, None)
                else:
                    deploy[key] = value
            data["deploy"] = deploy
        self._mutate(_apply)

    # ------------------------------------------------------------------
    # 脱敏出站
    # ------------------------------------------------------------------

    @classmethod
    def mask_admin(cls, admin: Dict[str, Any]) -> Dict[str, Any]:
        """管理员凭据的脱敏出站副本。"""
        masked = dict(admin)
        if masked.get("password"):
            masked["password"] = _SECRET_MASK
        return masked

    @classmethod
    def mask_app(cls, entry: Dict[str, Any]) -> Dict[str, Any]:
        """应用条目的脱敏出站副本（api_key 替换为占位符）。"""
        masked = dict(entry)
        if masked.get("api_key"):
            masked["api_key"] = _SECRET_MASK
        return masked

    @classmethod
    def is_masked(cls, value: Any) -> bool:
        return value == _SECRET_MASK


def generate_password(length: int = 20) -> str:
    """生成强随机密码（URL 安全字符集，可直接作为 Dify 管理员密码）。"""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_"
    return "".join(_secrets.choice(alphabet) for _ in range(length))


def generate_hex_token(nbytes: int = 24) -> str:
    """生成十六进制随机令牌（API Key / 内部共享密钥用）。"""
    return _secrets.token_hex(nbytes)


_store_instance: Optional[DifySecretsStore] = None
_store_lock = threading.Lock()


def get_dify_store() -> DifySecretsStore:
    """获取 DifySecretsStore 单例。"""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = DifySecretsStore()
    return _store_instance


def _reset_store_for_tests(path: str = "") -> DifySecretsStore:
    """测试专用：重置单例指向指定路径。"""
    global _store_instance
    with _store_lock:
        _store_instance = DifySecretsStore(path)
    return _store_instance
