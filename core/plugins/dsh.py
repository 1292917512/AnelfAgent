"""将 DeepSeek Harness bundle 中声明的独立 MCP 服务接入本地插件生命周期。

Model Experience:
    服务的工具定义与结果经现有 MCP 桥提供给模型。
    token 开销取决于实际加载的工具数量与 schema 大小。
    安装器不注入额外提示，工具前缀沿用 MCP 的分组唤醒策略。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from yaml.constructor import ConstructorError

from core.plugins.manifest import PluginError, PluginInterface, PluginManifest

_NonBlank = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


class RequiresDshHost(ValueError):
    """需要由官方宿主解释的 Cordis 配置或插件。"""


class _McpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    serverName: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    toolCallTimeoutMs: float = Field(default=60_000, gt=0, allow_inf_nan=False)
    failOnStartupError: bool = False


class _StdioConfig(_McpConfig):
    transport: Literal["stdio"]
    command: _NonBlank
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class _HttpConfig(_McpConfig):
    transport: Literal["streamable-http"]
    url: str = Field(pattern=r"^https?://\S+$")
    headers: dict[str, str] = Field(default_factory=dict)


_MCP_CONFIG: TypeAdapter[_StdioConfig | _HttpConfig] = TypeAdapter(
    Annotated[_StdioConfig | _HttpConfig, Field(discriminator="transport")]
)


def _patch_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative.strip() or Path(relative).anchor:
        raise ValueError("dsh.bundle.patch 需要包内的相对文件路径")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"DSH patch 路径越出插件根目录: {relative}")
    if not path.is_file():
        raise ValueError(f"找不到 DSH patch: {relative}")
    return path


def _read_patch(path: Path) -> list[Any]:
    try:
        rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    except ConstructorError as exc:
        raise RequiresDshHost("DSH YAML 标签需要 Cordis 宿主") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"无效的 DSH YAML: {path.name}") from exc
    if not isinstance(rows, list):
        raise ValueError(f"DSH patch 需要操作数组: {path.name}")
    return rows


def read_dsh_servers(root: Path, patches: object) -> dict[str, dict[str, Any]]:
    """提取独立 MCP 服务，无法直接表达的宿主语义交给 Cordis。"""
    servers: dict[str, dict[str, Any]] = {}
    ids: set[str] = set()
    paths = [_patch_path(root, item) for item in (patches if isinstance(patches, list) else [patches])]
    if not paths:
        raise ValueError("dsh.bundle.patch 不能为空")
    for path in paths:
        for patch in _read_patch(path):
            if not isinstance(patch, dict):
                raise ValueError("无效的 DSH patch 操作")
            # 单独切换 DSH 界面（如侧边预览）的操作不影响工具服务。
            if (set(patch) == {"id", "disabled"} and str(patch["id"]).startswith("ui-")
                    and isinstance(patch["disabled"], bool)):
                continue
            if set(patch) != {"insert"} or not isinstance(patch["insert"], list):
                raise RequiresDshHost("DSH 作用域或覆盖操作需要 Cordis 宿主")
            for row in patch["insert"]:
                if not isinstance(row, dict) or not isinstance(row.get("disabled", False), bool):
                    raise ValueError("无效的 DSH 插件声明")
                if row.get("disabled"):
                    continue
                if row.get("name") != "@deepseek-ai/dsh-mcp-client" or row.keys() - {"id", "name", "config", "disabled"}:
                    raise RequiresDshHost(f"DSH 组件 {row.get('name')!r} 需要 Cordis 宿主")
                row_id = row.get("id")
                if row_id is not None:
                    if not isinstance(row_id, str) or row_id in ids:
                        raise ValueError("DSH 组件 id 无效或重复")
                    ids.add(row_id)
                settings = row.get("config")
                if isinstance(settings, dict) and settings.keys() - (
                    _StdioConfig.model_fields.keys() | _HttpConfig.model_fields.keys()
                ):
                    raise RequiresDshHost("该 MCP 配置使用 DSH 宿主选项")
                config = _MCP_CONFIG.validate_python(settings)
                if config.failOnStartupError:
                    raise RequiresDshHost("该插件要求 DSH 启动阶段确认服务连接")
                if config.serverName in servers:
                    raise ValueError(f"重复的 DSH MCP serverName: {config.serverName}")
                server = config.model_dump(exclude={"serverName", "toolCallTimeoutMs", "failOnStartupError"})
                server["transport"] = "stdio" if isinstance(config, _StdioConfig) else "streamable_http"
                server["call_timeout"] = config.toolCallTimeoutMs / 1000
                servers[config.serverName] = server
    if not servers:
        raise RequiresDshHost("该 bundle 由 DSH 管理")
    return servers


def parse_dsh_manifest(root: Path, data: dict[str, Any]) -> PluginManifest:
    """将 DSH 包标识与实际运行方式转换成统一清单。"""
    package_name = str(data.get("name") or "")
    if not re.fullmatch(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*", package_name):
        raise PluginError(f"DSH npm 包名非法: {package_name!r}")
    name = package_name.lstrip("@").replace("/", "--")
    if len(name) > 64:
        name = f"{name[:51]}-{hashlib.sha256(package_name.encode()).hexdigest()[:12]}"
    bundle = data["dsh"]["bundle"]
    if not isinstance(bundle, dict):
        raise PluginError("dsh.bundle 必须是包含 patch 的对象")
    plugin_format = "dsh"
    host_reason = ""
    try:
        servers = read_dsh_servers(root, bundle.get("patch"))
        if "platforms" in bundle or "client" in data["dsh"] or "profile" in data["dsh"]:
            raise RequiresDshHost("该插件使用 DSH 平台约束或界面")
    except RequiresDshHost as exc:
        host_reason = str(exc)
        servers = {}
        plugin_format = "dsh-host"
    except (ValueError, OSError) as exc:
        raise PluginError(f"DSH 插件 {package_name}: {exc}") from exc
    requirements = {
        key: value for field in ("engines", "peerDependencies")
        if isinstance(section := data.get(field), dict)
        for key, value in section.items() if isinstance(value, str)
        and (field == "engines" or key.startswith("@deepseek-ai/dsh"))
    }
    return PluginManifest(
        requirements=requirements, host_reason=host_reason,
        name=name, version=str(data.get("version") or ""), description=str(data.get("description") or ""),
        interface=PluginInterface(display_name=package_name), mcp_servers_inline=servers, format=plugin_format,
    )
