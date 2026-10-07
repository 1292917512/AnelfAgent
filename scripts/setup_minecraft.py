"""安装开源 Minecraft MCP 插件，并配置游戏聊天频道。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_PLUGIN_NAME = "minecraft-companion"

_WORKER_PROFILE_NAME = "mc-worker"
# 模型池引用 llm_clients.json 模型条目的 id（客户端 ID），不是供应商模型名
_WORKER_MODEL_POOL = ("glm-4.5-air", "minimax-m2")
_WORKER_DESCRIPTION = "Minecraft 执行代理：反射式完成游戏内动作序列，给沟通者打下手"
_WORKER_INSTRUCTIONS = (
    "你是 Minecraft 执行代理。只使用 minecraft MCP 工具完成指定动作序列。"
    "反射式执行：不规划、不解释、不闲聊、不询问玩家，每轮直接调用下一个工具；"
    "相互独立的工具调用在一次响应里并列发出；禁止一切记忆/备忘/笔记类操作。"
    "工具未注入时先 activate_tool_group(\"mcp:minecraft\")。"
    "完成后只返回结构化结果：做了什么、获得什么、失败原因。"
)
_WORKER_TOOL_TAGS = ["mcp:minecraft"]
_WORKER_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "done": {"type": "boolean"},
        "actions": {"type": "array", "items": {"type": "string"}},
        "gained": {"type": "array", "items": {"type": "string"}},
        "failure": {"type": "string"},
    },
    "required": ["done"],
}


def ensure_worker_profile() -> str:
    """确保 mc-worker 子代理档案存在；已存在则不改动（保留用户自定义）。"""
    from agent.llm.llm_manager import get_llm_manager

    manager = get_llm_manager()
    if manager.get_sub_agent_profile(_WORKER_PROFILE_NAME) is not None:
        return "exists"
    pool = [
        mid for mid in _WORKER_MODEL_POOL
        if manager._validate_sub_agent_model(mid) is None
    ]
    if not pool:
        default = manager.default_name
        if default and manager._validate_sub_agent_model(default) is None:
            pool = [default]
    if not pool:
        print("警告：没有可用聊天模型，跳过 mc-worker 子代理档案注册；请配置模型后手动创建。")
        return "skipped"
    ok, message = manager.create_sub_agent(
        name=_WORKER_PROFILE_NAME,
        model_id=pool[0],
        description=_WORKER_DESCRIPTION,
        instructions=_WORKER_INSTRUCTIONS,
        tool_tags=_WORKER_TOOL_TAGS,
        output_schema=_WORKER_OUTPUT_SCHEMA,
    )
    if not ok:
        print(f"警告：mc-worker 子代理档案创建失败：{message}")
        return "failed"
    if len(pool) > 1:
        manager.update_sub_agent(_WORKER_PROFILE_NAME, models=pool)
    return "created"


def download_node(runtime: Path) -> Path:
    """下载并校验 Windows x64 便携 Node 22，所有文件留在工作区。"""
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("便携下载目前支持 Windows x64；其他平台请安装 Node 22 并使用 --node")
    existing = list(runtime.glob("node-v22.*-win-x64/node.exe"))
    if existing:
        return max(existing, key=lambda p: tuple(int(n) for n in p.parent.name.split("-")[1][1:].split(".")))
    runtime.mkdir(parents=True, exist_ok=True)
    base = "https://nodejs.org/dist/latest-v22.x/"
    with urllib.request.urlopen(base + "SHASUMS256.txt", timeout=30) as response:
        sums = response.read().decode("utf-8")
    checksum, filename = next(line.split() for line in sums.splitlines() if line.endswith("win-x64.zip"))
    archive = runtime / filename
    with urllib.request.urlopen(base + filename, timeout=120) as response:
        archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        raise ValueError("Node 下载文件校验失败")
    root = runtime.resolve()
    with zipfile.ZipFile(archive) as package:
        if any(not (root / member.filename).resolve().is_relative_to(root) for member in package.infolist()):
            raise ValueError("Node 压缩包包含越界路径")
        package.extractall(root)
    return root / filename.removesuffix(".zip") / "node.exe"


def node_runtime(node: str, npm_cli: str = "") -> tuple[Path, Path]:
    """校验 Node 版本并定位随运行时分发的 npm CLI。"""
    resolved = shutil.which(node)
    executable = Path(resolved or node).expanduser().resolve()
    version = subprocess.run([str(executable), "--version"], check=True, capture_output=True, text=True).stdout.strip()
    if int(version.removeprefix("v").split(".")[0]) < 22:
        raise ValueError(f"Minecraft 执行器需要 Node 22+，当前为 {version}；Windows 可加 --download-node")
    candidates = [executable.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"]
    npm = shutil.which("npm")
    if npm:
        candidates.append(Path(npm).parent / "node_modules" / "npm" / "bin" / "npm-cli.js")
        candidates.append(Path(npm).resolve().parent / "npm-cli.js")
    if npm_cli:
        candidates.insert(0, Path(npm_cli).expanduser().resolve())
    entry = next((p for p in candidates if p.is_file()), None)
    if entry is None:
        raise ValueError("找不到 npm-cli.js，请使用 --npm-cli 指定它的绝对路径")
    return executable, entry


def setup(args: argparse.Namespace) -> Path:
    """安装锁定的执行器，保留其他插件和服务的配置。"""
    from agent.channel.config import register_channel_schema, set_channel_config
    from core.config import ConfigManager
    from core.path import workspace_root
    from core.plugins import get_plugin_manager
    from core.plugins.store import plugin_payload_dir
    from entities.mcp.config import MCPServerStore
    from entities.plugins.activation import wire_plugin_manager

    ConfigManager.initialize()
    register_channel_schema("minecraft")
    manager = get_plugin_manager()
    wire_plugin_manager(manager)
    record = manager.get_plugin(_PLUGIN_NAME)
    store = MCPServerStore()
    previous: dict[str, Any] = {}
    if record is not None:
        candidates = [store.get_server_config(name) for name in record.mcp_servers]
        owned = [cfg for cfg in candidates if cfg is not None and cfg.get("plugin") == _PLUGIN_NAME]
        if len(owned) > 1:
            raise ValueError("Minecraft 插件的 MCP 服务归属不唯一，请在插件管理页检查安装状态")
        if owned:
            previous = owned[0]
    previous_env = previous.get("env", {})
    defaults = {"host": "127.0.0.1", "port": "25565", "username": "AnelfBot", "auth": "offline", "version": "26.1"}
    for field, default in defaults.items():
        if getattr(args, field) is None:
            setattr(args, field, previous_env.get(f"MCP_DEFAULT_{field.upper()}", default))
    if args.world_id is None:
        args.world_id = ConfigManager.get("minecraft_server_id", "local")
    if args.player is None:
        args.player = [] if args.clear_players else ConfigManager.get("minecraft_allowed_players", [])
    _validate_options(args)
    workspace = Path(workspace_root()).resolve()
    runtime = workspace / "minecraft" / "runtime"
    selected = str(download_node(runtime)) if args.download_node else (args.node or previous.get("command", "node"))
    node, npm_cli = node_runtime(selected, args.npm_cli)
    if record is None:
        record = manager.install_from_source(str(_REPO / "plugins" / "minecraft"))
    elif args.upgrade:
        record, _ = manager.upgrade(_PLUGIN_NAME)
    if not record.enabled:
        record = manager.toggle(_PLUGIN_NAME, True)
    payload = plugin_payload_dir(_PLUGIN_NAME)
    env = dict(os.environ)
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    subprocess.run(
        [
            str(node),
            str(npm_cli),
            "ci",
            "--omit=optional",
            "--no-audit",
            "--no-fund",
            "--cache",
            str(workspace / "minecraft" / "npm-cache"),
        ],
        cwd=payload,
        env=env,
        check=True,
    )
    servers = store.load_config().get("mcpServers", {})
    names = [name for name in record.mcp_servers if servers.get(name, {}).get("plugin") == _PLUGIN_NAME]
    if len(names) != 1:
        raise ValueError("Minecraft 插件的 MCP 服务归属不唯一，请在插件管理页检查安装状态")
    server_name = names[0]
    values = dict(servers[server_name].get("env", {}))
    values.update(previous_env)
    values.update(
        {
            "MCP_DEFAULT_HOST": args.host,
            "MCP_DEFAULT_PORT": str(args.port),
            "MCP_DEFAULT_USERNAME": args.username,
            "MCP_DEFAULT_AUTH": args.auth,
            "MCP_DEFAULT_VERSION": args.version,
            "MCP_ALLOWED_HOSTS": args.host,
            "MCP_AUTO_CONNECT": "false",
            "AWESOME_MINEFLAYER_MCP_HOME": str(workspace / "minecraft" / "state" / args.world_id),
        }
    )
    store.update_server_config(server_name, {"command": str(node), "enabled": True, "env": values})
    set_channel_config(
        "minecraft",
        enabled=True,
        mcp_server=server_name,
        server_id=args.world_id,
        bot_username=args.username,
        allowed_players=args.player,
    )
    print(
        json.dumps(
            {
                "plugin": str(payload),
                "mcp_server": server_name,
                "minecraft_version": args.version,
                "node": str(node),
                "worker_profile": ensure_worker_profile(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("安装完成。启动或重启 AnelfAgent，在 WebUI 说：/minecraft-companion 连接默认世界并陪我玩。")
    return payload


def _validate_options(args: argparse.Namespace) -> None:
    """校验合并已有配置后的连接参数，安装前拒绝非法目标。"""
    if not 1 <= int(args.port) <= 65535:
        raise ValueError("端口必须在 1 到 65535 之间")
    if not args.host.strip() or "," in args.host:
        raise ValueError("服务器地址必须是一个非空主机名或 IP")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.world_id):
        raise ValueError("世界标识只能包含英文字母、数字、下划线和连字符")
    if args.auth == "offline" and not re.fullmatch(r"[A-Za-z0-9_]{1,16}", args.username):
        raise ValueError("离线游戏名只能包含英文字母、数字和下划线，最长 16 字符")
    if any(not re.fullmatch(r"[A-Za-z0-9_]{1,16}", player) for player in args.player):
        raise ValueError("允许交互的玩家名格式无效")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--username")
    parser.add_argument("--auth", choices=("offline", "microsoft"))
    parser.add_argument("--version")
    parser.add_argument("--world-id")
    players = parser.add_mutually_exclusive_group()
    players.add_argument("--player", action="append")
    players.add_argument("--clear-players", action="store_true")
    parser.add_argument("--node")
    parser.add_argument("--npm-cli", default="")
    parser.add_argument("--download-node", action="store_true")
    parser.add_argument("--upgrade", action="store_true")
    args = parser.parse_args()
    try:
        setup(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Minecraft 安装失败: {exc}\n")


if __name__ == "__main__":
    main()
