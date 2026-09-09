"""密码本导入导出：与市面密码管理器互通。

导入：Bitwarden JSON / Bitwarden CSV / Chrome CSV / KeePass CSV / 本实体加密备份。
导出：Bitwarden 兼容 JSON / CSV（明文，需解锁 + 显式确认）/ 主密码加密备份（.vault.enc）。

加密备份格式（JSON）：
    {"format": "anelf-vault-enc-v1", "kdf": {"time_cost":..,"memory_cost":..,"parallelism":..},
     "salt": b64, "payload": b64(nonce‖AES-256-GCM(json))}
密钥由主密码现派生（独立 salt），与库内 KEK/DEK 无关，可离线保存。
"""

from __future__ import annotations

import base64
import csv
import io
import json
from dataclasses import asdict, dataclass, field
from typing import Any, List

from . import crypto

ENCRYPTED_FORMAT = "anelf-vault-enc-v1"


@dataclass
class EntryDraft:
    """导入/导出的明文条目载体（仅存在于内存）。"""

    title: str = ""
    username: str = ""
    url: str = ""
    password: str = ""
    totp: str = ""
    notes: str = ""
    tags: List[str] = field(default_factory=list)
    favorite: bool = False


def _clean(text: Any) -> str:
    return str(text).strip() if text is not None else ""


# ------------------------------------------------------------------
# 导入
# ------------------------------------------------------------------

def parse_bitwarden_json(text: str) -> List[EntryDraft]:
    data = json.loads(text)
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("不是有效的 Bitwarden JSON（缺少 items 数组）")
    drafts: List[EntryDraft] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") not in (1, None):
            continue  # type=1 为登录项；无 type 字段时按登录项兼容处理
        login = item.get("login") or {}
        drafts.append(EntryDraft(
            title=_clean(item.get("name")),
            username=_clean(login.get("username")),
            url=_clean(login.get("uri") or (login.get("uris") or [{}])[0].get("uri", "")
                       if login.get("uris") else login.get("uri")),
            password=_clean(login.get("password")),
            totp=_clean(login.get("totp")),
            notes=_clean(item.get("notes")),
            favorite=bool(item.get("favorite")),
        ))
    return drafts


def parse_bitwarden_csv(text: str) -> List[EntryDraft]:
    reader = csv.DictReader(io.StringIO(text))
    drafts = []
    for row in reader:
        if _clean(row.get("type")) and _clean(row.get("type")) != "login":
            continue
        drafts.append(EntryDraft(
            title=_clean(row.get("name")),
            username=_clean(row.get("login_username")),
            url=_clean(row.get("login_uri")),
            password=_clean(row.get("login_password")),
            totp=_clean(row.get("login_totp")),
            notes=_clean(row.get("notes")),
            favorite=_clean(row.get("favorite")) == "1",
        ))
    return drafts


def parse_chrome_csv(text: str) -> List[EntryDraft]:
    reader = csv.DictReader(io.StringIO(text))
    drafts = []
    for row in reader:
        drafts.append(EntryDraft(
            title=_clean(row.get("name")),
            username=_clean(row.get("username")),
            url=_clean(row.get("url")),
            password=_clean(row.get("password")),
            notes=_clean(row.get("note")),
        ))
    return drafts


def parse_keepass_csv(text: str) -> List[EntryDraft]:
    reader = csv.DictReader(io.StringIO(text))
    drafts = []
    for row in reader:
        if _clean(row.get("Group")) == "Recycle Bin":
            continue
        drafts.append(EntryDraft(
            title=_clean(row.get("Title")),
            username=_clean(row.get("Username")),
            url=_clean(row.get("URL")),
            password=_clean(row.get("Password")),
            notes=_clean(row.get("Notes")),
            totp=_clean(row.get("TOTP")),
        ))
    return drafts


IMPORT_PARSERS = {
    "bitwarden_json": parse_bitwarden_json,
    "bitwarden_csv": parse_bitwarden_csv,
    "chrome_csv": parse_chrome_csv,
    "keepass_csv": parse_keepass_csv,
}


# ------------------------------------------------------------------
# 明文导出（Bitwarden 兼容）
# ------------------------------------------------------------------

def export_bitwarden_json(drafts: List[EntryDraft]) -> str:
    items = []
    for d in drafts:
        items.append({
            "type": 1,
            "name": d.title,
            "notes": d.notes or None,
            "favorite": d.favorite,
            "login": {
                "username": d.username or None,
                "password": d.password or None,
                "uri": d.url or None,
                "totp": d.totp or None,
            },
        })
    return json.dumps({"encrypted": False, "items": items}, ensure_ascii=False, indent=2)


def export_csv(drafts: List[EntryDraft]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["folder", "favorite", "type", "name", "notes", "fields",
                     "reprompt", "login_uri", "login_username", "login_password",
                     "login_totp"])
    for d in drafts:
        writer.writerow(["", 1 if d.favorite else 0, "login", d.title, d.notes,
                         "", "", d.url, d.username, d.password, d.totp])
    return buf.getvalue()


# ------------------------------------------------------------------
# 加密备份（主密码现派生 KEK，独立 salt）
# ------------------------------------------------------------------

def export_encrypted(drafts: List[EntryDraft], master_password: str) -> str:
    salt = crypto.generate_salt()
    params = {
        "time_cost": crypto.DEFAULT_TIME_COST,
        "memory_cost": crypto.DEFAULT_MEMORY_COST,
        "parallelism": crypto.DEFAULT_PARALLELISM,
    }
    kek = crypto.derive_kek(master_password, salt, **params)
    payload = json.dumps([asdict(d) for d in drafts], ensure_ascii=False).encode("utf-8")
    nonce_ct = crypto.encrypt_bytes(kek, payload, b"vault-export")
    return json.dumps({
        "format": ENCRYPTED_FORMAT,
        "kdf": params,
        "salt": base64.b64encode(salt).decode("ascii"),
        "payload": base64.b64encode(nonce_ct).decode("ascii"),
    }, indent=2)


def import_encrypted(text: str, master_password: str) -> List[EntryDraft]:
    data = json.loads(text)
    if data.get("format") != ENCRYPTED_FORMAT:
        raise ValueError("不是有效的密码本加密备份格式")
    salt = base64.b64decode(data["salt"])
    kek = crypto.derive_kek(master_password, salt, **data["kdf"])
    blob = base64.b64decode(data["payload"])
    payload = crypto.decrypt_bytes(kek, blob, b"vault-export")
    return [EntryDraft(**item) for item in json.loads(payload.decode("utf-8"))]
