"""密码本 Web API 的 pydantic 模型（出站模型一律不含密文/明文敏感字段）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StatusOut(BaseModel):
    initialized: bool
    unlock_mode: str  # machine / master / ""（未初始化）
    unlocked: bool
    entry_count: int
    auto_lock_remaining: int


class SetupRequest(BaseModel):
    """初始化：master=主密码模式；machine=机器密钥模式（零交互自动解锁）。"""

    mode: str = "master"
    password: str = ""


class MasterEnableRequest(BaseModel):
    password: str = Field(min_length=8)


class PasswordRequest(BaseModel):
    password: str = ""


class ChangeMasterRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class EntryOut(BaseModel):
    id: str
    title: str
    username: str
    url: str
    tags: List[str]
    favorite: bool
    has_password: bool
    has_totp: bool
    has_notes: bool
    created_at: float
    updated_at: float
    score: Optional[float] = None


class EntryListOut(BaseModel):
    count: int
    entries: List[EntryOut]


class EntryCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    username: str = ""
    url: str = ""
    password: str = ""
    totp_secret: str = ""
    notes: str = ""
    tags: List[str] = Field(default_factory=list)
    favorite: bool = False
    generate: bool = False


class EntryUpdateRequest(BaseModel):
    """敏感字段 None=保持不变、空串=清除、非空=重写。"""

    title: Optional[str] = None
    username: Optional[str] = None
    url: Optional[str] = None
    password: Optional[str] = None
    totp_secret: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    favorite: Optional[bool] = None


class RevealRequest(BaseModel):
    field: str = "password"


class RevealOut(BaseModel):
    entry_id: str
    field: str
    value: str


class TotpOut(BaseModel):
    entry_id: str
    title: str
    code: str
    period: int
    remaining: int


class GenerateRequest(BaseModel):
    length: int = Field(default=20, ge=4, le=128)
    upper: bool = True
    lower: bool = True
    digits: bool = True
    symbols: bool = True
    exclude_ambiguous: bool = False
    memorable: bool = False


class GenerateOut(BaseModel):
    password: str
    strength: Dict[str, Any]


class ImportRequest(BaseModel):
    format: str
    content: str
    strategy: str = "skip"
    master_password: str = ""


class ImportOut(BaseModel):
    added: int
    skipped: int
    overwritten: int
    failed: int


class ExportRequest(BaseModel):
    format: str = "json"
    master_password: str = ""


class ExportOut(BaseModel):
    format: str
    content: str


class BreachReportOut(BaseModel):
    checked_entries: int
    pwned: List[Dict[str, Any]]
    reused: List[Dict[str, Any]]
    weak: List[Dict[str, Any]]
    hibp_checked: bool
    hibp_error: str
