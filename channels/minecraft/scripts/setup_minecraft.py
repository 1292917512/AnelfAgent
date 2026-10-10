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
import urllib.request
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from channels.minecraft.scripts.minecraft_actions import patch_actions
from channels.minecraft.scripts.minecraft_crafting import patch_crafting
from channels.minecraft.scripts.minecraft_digging import patch_digging
from channels.minecraft.scripts.minecraft_mining import patch_mining
from channels.minecraft.scripts.minecraft_placement import patch_placement

_PLUGIN_NAME = "minecraft-companion"
_SOURCE = Path(__file__).resolve().parent.parent / "mcp"
_LEGACY_SOURCE = _SOURCE.parents[2] / "plugins" / "minecraft"

_WORKER_PROFILE_NAME = "mc-worker"
# 模型池引用 llm_clients.json 模型条目的 id（客户端 ID），不是供应商模型名
_WORKER_MODEL_POOL = ("minimax-m3", "minimax-m2")
_WORKER_DESCRIPTION = "Minecraft 执行代理：反射式完成游戏内动作序列，给沟通者打下手"
_WORKER_INSTRUCTIONS = (
    "你是 Minecraft 执行代理。只使用 minecraft MCP 工具完成指定动作序列。"
    "调用及委托文字中的工具名都以实际目录为准：get_inventory/get_state/get_block_at/place_block/craft_item，"
    "不要添加 mcp__minecraft__ 前缀；委托写错名称时使用目录中的正确名称，不照抄错误名称。"
    "反射式执行：不规划、不解释、不闲聊、不询问玩家，每轮直接调用下一个工具；"
    "相互独立的工具调用在一次响应里并列发出；禁止一切记忆/备忘/笔记类操作。"
    "工具未注入时先 activate_tool_group(\"mcp:minecraft\")。"
    "工具名只许用注入列表里的真实名字：需要确认时先调 list_entity_methods({\"group\": \"mcp:minecraft\"})，"
    "严禁凭印象臆造工具名（如 get_player/get_entities 之类不存在的名字）。"
    "控制同一机器人的动作串行执行；BUSY 时查 action_status，不并发重试。"
    "cancel_task 的 stopped=false 表示还在收尾，不得说已停止或继续派活。"
    "动作失败时换一条路重试一次（如 collect_block 寻路超时→先 goto 到目标旁开阔地→用 dig 直接挖触手可及的方块），"
    "同一条路失败两次就放弃并在 failure 里写明原因，不要反复撞同一堵墙。"
    "prepare_item 已支持背包材料驱动的木制工具、工作台和木棍准备；按上级目标使用，不与其后台步骤重复执行。"
    "prepare_item 的 count 是产物数量，ensure 复用已有目标、craft 额外新做；启动不是完成，查询 production_status 核验终态。"
    "仅当上级明确允许区域采集时，为 prepare_item 传 gather 的原木类型、区域坐标、半径和 maxCount 上限；"
    "它自行按配方补缺额、返程后制作，不能再启动并行采集或因受阻扩大范围。"
    "manage_supplies 只操作明确授权且四格内可见的指定箱子/木桶；deposit 存入精确数量并用 keep 留料，withdraw 补到背包目标总数。"
    "补给启动不等于完成，按 supply_status 的双方对账和收尾事实汇报，失败或取消不重复执行已搬运部分。"
    "低层 craft_item 的 count 是操作次数，按缺少产量除以每次产量向上取整；木棍一次产出4根。"
    "合成失败先查 get_inventory 的实际产物和 crafting 光标/材料格状态，停止后续配方；"
    "按任务上下文中的恢复规则归还残留材料，无法确认状态时暂停，不编造缺料原因。"
    "挖掘只选可见可达的方块；dig 成功仅代表服务器确认方块消失，采集所得必须按背包增量核验。"
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
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
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
        record = manager.install_from_source(str(_SOURCE))
    else:
        if record.source_type == "local" and Path(record.source).resolve() == _LEGACY_SOURCE:
            record = replace(record, source=str(_SOURCE))
            manager.registry.upsert(record)
        if args.upgrade:
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
    view_distance_state = patch_executor_view_distance(payload)
    chat_discipline_state = patch_executor_chat_discipline(payload)
    think_timeout_state = patch_pathfinder_think_timeout(payload)
    craft_count_state = patch_craft_count_semantics(payload)
    crafting_state = patch_crafting(payload)
    placement_state = patch_placement(payload)
    digging_state = patch_digging(payload)
    mining_state = patch_mining(payload)
    actions_state = patch_actions(payload)
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
    store.update_server_config(server_name, {"command": str(node), "enabled": True, "priority": "below_normal", "env": values})
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
                "view_distance_patch": view_distance_state,
                "chat_discipline_patch": chat_discipline_state,
                "think_timeout_patch": think_timeout_state,
                "craft_count_patch": craft_count_state,
                "crafting_patch": crafting_state,
                "placement_patch": placement_state,
                "digging_patch": digging_state,
                "mining_patch": mining_state,
                "actions_patch": actions_state,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("安装完成。启动或重启 AnelfAgent，在 WebUI 说：/minecraft-companion 连接默认世界并陪我玩。")
    return payload


_VIEW_DISTANCE_ORIGINAL = 'viewDistance: z.enum(["far", "normal", "short", "tiny"]).optional(),'
_VIEW_DISTANCE_PATCHED = 'viewDistance: z.enum(["far", "normal", "short", "tiny"]).optional().default("short"),'

_CHAT_DESC_ORIGINAL = 'description: "Send a public chat message to the server. To run a slash command use `run_command` instead.",'
_CHAT_DESC_PATCHED_V1 = (
    'description: "Send a public chat message to the server. To run a slash command use `run_command` instead.'
    ' One call sends one line; make at most 2 chat calls per assistant turn — merge longer content into'
    ' fewer lines or continue in the next turn. Flooding the chat HUD freezes and annoys the host player.",'
)
# v2 初版在内嵌 " ; " 处未转义引号，直接把 JS 字符串截断——MCP 服务启动即 SyntaxError。
# 修复版彻底改用无引号措辞，并保留对破损文件的识别修复。
_CHAT_DESC_BROKEN_V2 = ' (separate items with " ; ")'
_CHAT_DESC_PATCHED = (
    'description: "Send a public chat message to the server. To run a slash command use `run_command` instead.'
    ' EVERY call to this tool is a separate chat message: never call it more than twice in a row — combine'
    ' points into one message (separate items with a semicolon). Long reports belong in the web/private chat,'
    ' not the game chat. Flooding the chat HUD freezes and annoys the host player.",'
)

_PATHFINDER_TIMEOUT_ORIGINAL = "bot.pathfinder.thinkTimeout = 5000 // ms"
_PATHFINDER_TIMEOUT_PATCHED = "bot.pathfinder.thinkTimeout = 30000 // ms (patched: 密林/长距离寻路 5 秒预算必超时)"

_CRAFT_COUNT_DESC_ORIGINAL = 'count: z.number().int().min(1).optional().describe("How many times to craft (default 1)"),'
_CRAFT_COUNT_DESC_PATCHED = (
    'count: z.number().int().min(1).optional().describe("How many craft operations to perform (default 1) —'
    ' each operation consumes one full set of ingredients, e.g. count=4 oak_planks consumes 4 oak logs and'
    ' yields 16 planks. Pass operations needed, not the desired result amount: count=36 planks requires 36'
    ' logs and fails with missing ingredient when the inventory is short."),'
)


def patch_executor_view_distance(payload: Path) -> str:
    """给执行器 connect_bot 的 viewDistance 打默认值补丁（安装后幂等执行）。

    bot 进服接收的区块量由视距决定，默认 far（12 区块 = 625 列）会让主机
    在进服瞬间冻结数秒；AI 调 connect_bot 可能漏传视距，默认值才是可靠防线。
    执行器是第三方包，补丁为精确字符串替换：版本漂移导致模式失配时硬失败，
    提示核对而非静默跳过。
    """
    target = payload / "node_modules" / "awesome-mineflayer-mcp" / "dist" / "tools" / "lifecycle.js"
    source = target.read_text(encoding="utf-8")
    if _VIEW_DISTANCE_PATCHED in source:
        return "exists"
    if _VIEW_DISTANCE_ORIGINAL not in source:
        raise RuntimeError(
            f"无法定位 viewDistance 模式，awesome-mineflayer-mcp 可能已升级: {target}"
        )
    target.write_text(source.replace(_VIEW_DISTANCE_ORIGINAL, _VIEW_DISTANCE_PATCHED), encoding="utf-8")
    return "patched"


def patch_executor_chat_discipline(payload: Path) -> str:
    """给执行器 chat 工具描述打刷屏纪律补丁（安装后幂等执行）。

    实机日志：模型一轮内连发 10 条 chat 逐行刷屏——SKILL 里的回合条数上限
    是注入层软约束，模型照样违反；工具描述是模型每次调用前必读的前台文本，
    把纪律写进描述才是可靠防线（与 viewDistance 默认值补丁同一思路）。
    版本漂移导致模式失配时硬失败，提示核对而非静默跳过。
    """
    target = payload / "node_modules" / "awesome-mineflayer-mcp" / "dist" / "tools" / "chat.js"
    source = target.read_text(encoding="utf-8")
    if _CHAT_DESC_PATCHED in source:
        return "exists"
    if _CHAT_DESC_BROKEN_V2 in source:
        # v2 初版内嵌引号未转义导致 SyntaxError，就地修复为无引号措辞
        target.write_text(source.replace(_CHAT_DESC_BROKEN_V2, " (separate items with a semicolon)"), encoding="utf-8")
        return "repaired"
    if _CHAT_DESC_PATCHED_V1 in source:
        target.write_text(source.replace(_CHAT_DESC_PATCHED_V1, _CHAT_DESC_PATCHED), encoding="utf-8")
        return "patched"
    if _CHAT_DESC_ORIGINAL not in source:
        raise RuntimeError(
            f"无法定位 chat 描述模式，awesome-mineflayer-mcp 可能已升级: {target}"
        )
    target.write_text(source.replace(_CHAT_DESC_ORIGINAL, _CHAT_DESC_PATCHED), encoding="utf-8")
    return "patched"


def patch_pathfinder_think_timeout(payload: Path) -> str:
    """给 mineflayer-pathfinder 默认寻路预算打补丁（安装后幂等执行）。

    实机日志：密林/树冠下 collect_block 五秒内必报 "Took to long to decide
    path to goal"（A* 全路径预算默认 5000ms 耗尽）。延长至 30 秒：每刻思考
    预算（tickTimeout 40ms）不变，但全程计算和等待可能增加，需结合运行指标
    评估。版本漂移导致模式失配时硬失败。
    """
    target = payload / "node_modules" / "mineflayer-pathfinder" / "index.js"
    source = target.read_text(encoding="utf-8")
    if _PATHFINDER_TIMEOUT_PATCHED in source:
        return "exists"
    if _PATHFINDER_TIMEOUT_ORIGINAL not in source:
        raise RuntimeError(
            f"无法定位 thinkTimeout 模式，mineflayer-pathfinder 可能已升级: {target}"
        )
    target.write_text(source.replace(_PATHFINDER_TIMEOUT_ORIGINAL, _PATHFINDER_TIMEOUT_PATCHED), encoding="utf-8")
    return "patched"


def patch_craft_count_semantics(payload: Path) -> str:
    """给 craft_item 的 count 语义打描述补丁（安装后幂等执行）。

    实机日志：模型把 count 当"目标产物数量"传（oak_planks count=36），
    而执行器语义是"合成操作次数"——每次操作消耗一份完整材料，36 次需
    36 根原木，材料不足即 missing ingredient。模型每次调用前必读工具
    描述，把语义写进描述才是可靠防线。版本漂移硬失败。
    """
    target = payload / "node_modules" / "awesome-mineflayer-mcp" / "dist" / "tools" / "crafting.js"
    source = target.read_text(encoding="utf-8")
    if _CRAFT_COUNT_DESC_PATCHED in source:
        return "exists"
    if _CRAFT_COUNT_DESC_ORIGINAL not in source:
        raise RuntimeError(
            f"无法定位 craft count 描述模式，awesome-mineflayer-mcp 可能已升级: {target}"
        )
    target.write_text(source.replace(_CRAFT_COUNT_DESC_ORIGINAL, _CRAFT_COUNT_DESC_PATCHED), encoding="utf-8")
    return "patched"


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
