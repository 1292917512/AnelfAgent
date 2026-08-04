"""SSH 实体的 Web API 数据模型。

所有出站模型一律不含凭据（password/passphrase），仅暴露连接元信息与实时状态。
入站模型对密码字段做可选处理（更新时留空表示保持不变）。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ConnectionOut(BaseModel):
    """连接状态快照（出站，不含凭据）。"""

    name: str
    host: str = ""
    port: int = 22
    username: str = ""
    description: str = ""
    status: str = "disconnected"
    last_error: str = ""
    connected_at: int = 0
    last_used_at: int = 0
    is_default: bool = False
    has_password: bool = False
    has_key: bool = False


class ConnectionListResult(BaseModel):
    """连接列表（含默认连接名）。"""

    default: str = ""
    connections: List[ConnectionOut] = Field(default_factory=list)


class ConnectionCreateRequest(BaseModel):
    """新增连接请求。"""

    name: str = Field(..., min_length=1, max_length=32)
    host: str = Field(..., min_length=1)
    port: int = Field(22, ge=1, le=65535)
    username: str = Field(..., min_length=1)
    password: str = ""
    key_path: str = ""
    passphrase: str = ""
    description: str = ""


class ConnectionUpdateRequest(BaseModel):
    """更新连接请求（字段均可选，密码留空保持不变）。"""

    name: Optional[str] = Field(None, min_length=1, max_length=32)
    host: Optional[str] = None
    port: Optional[int] = Field(None, ge=1, le=65535)
    username: Optional[str] = None
    password: Optional[str] = None
    key_path: Optional[str] = None
    passphrase: Optional[str] = None
    description: Optional[str] = None


class ExecRequest(BaseModel):
    """Web 端执行命令请求。"""

    command: str = Field(..., min_length=1)
    timeout: int = Field(60, ge=1, le=600)
    work_dir: str = ""


class ExecResult(BaseModel):
    """命令执行结果。"""

    ok: bool = False
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    connection: str = ""
    truncated: bool = False


class TransferResult(BaseModel):
    """文件传输结果。"""

    ok: bool = False
    connection: str = ""
    remote_path: str = ""
    local_path: str = ""
    size: int = 0


class SetDefaultRequest(BaseModel):
    """设置默认连接请求。"""

    name: str = Field(..., min_length=1)
