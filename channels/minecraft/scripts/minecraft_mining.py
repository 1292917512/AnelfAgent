"""安装可往返的 Minecraft 矿洞任务和统一移动保护。

Model Experience: 新工具返回任务 ID、阶段、背包增量和返程事实；事件只播报终态。
Token effect: 工具 schema 增加数百 token，连续挖掘不再逐格消耗模型调用。
Cache effect: schema 随安装更新一次，进度位于工具结果/事件尾部，不改人设前缀。
"""

from __future__ import annotations

import argparse
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent / "mcp/runtime"
_PATCHES: dict[str, tuple[tuple[str, str], ...]] = {
    "awesome-mineflayer-mcp/dist/server.js": (
        ('import { registerBuild } from "./tools/build.js";',
         'import { registerBuild } from "./tools/build.js";\nimport { registerMining } from "./tools/anelf-mining.mjs";'),
        ("    registerBuild(reg);", "    registerBuild(reg);\n    registerMining(reg);"),
    ),
    "awesome-mineflayer-mcp/dist/tools/registry.js": (
        ('import { withTimeout } from "../util/async.js";',
         'import { withTimeout } from "../util/async.js";\nimport { wrapMiningTools } from "./anelf-mining.mjs";'),
        ("    return function register(def) {", "    return function register(def) {\n        def = wrapMiningTools(def);"),
    ),
    "awesome-mineflayer-mcp/dist/bot/plugins.js": (
        ('import armorManager from "mineflayer-armor-manager";',
         'import armorManager from "mineflayer-armor-manager";\nimport miningSafety from "mineflayer/lib/anelf_mining/mining-safety.cjs";'),
        ("    bot.loadPlugin(armorManager);", "    bot.loadPlugin(armorManager);\n    bot.once(\"spawn\", () => miningSafety.installSafety(bot));"),
    ),
    "mineflayer-collectblock/lib/CollectBlock.js": (
        ("!bot.pathfinder.movements.safeToBreak(block)",
         "!require('mineflayer/lib/anelf_mining/mining-safety.cjs').canHarvestBlock(bot, block)"),
    ),
    "mineflayer/lib/plugins/digging.js": (
        ("    // In vanilla the client will cancel digging the current block once the other block is at the crosshair.",
         "    require('../anelf_mining/mining-safety.cjs').assertFloorSafe(bot, block)\n"
         "    // In vanilla the client will cancel digging the current block once the other block is at the crosshair."),
    ),
}


def patch_mining(payload: Path) -> str:
    """锁定依赖锚点，复制类型检查过的任务模块并接入现有工具注册。"""
    updates: dict[Path, str] = {}
    for relative, replacements in _PATCHES.items():
        target = payload / "node_modules" / relative
        source = target.read_text(encoding="utf-8")
        updated = source
        for original, patched in replacements:
            if (relative.endswith("bot/plugins.js") and original == "    bot.loadPlugin(armorManager);"
                    and updated.count('    bot.once("spawn", () => miningSafety.installSafety(bot));') == 1):
                # The action installer may supervise plugin injection in a callback.
                continue
            if updated.count(patched) == 1:
                continue
            if updated.count(original) != 1:
                raise RuntimeError(f"无法定位唯一的矿洞补丁位置，请核对依赖版本: {target}")
            updated = updated.replace(original, patched, 1)
        if updated != source:
            updates[target] = updated
    for name in ("mining-safety.cjs", "mining-store.cjs", "mining-task.cjs", "mining-tools.mjs"):
        target = payload / "node_modules" / (
            "awesome-mineflayer-mcp/dist/tools/anelf-mining.mjs" if name.endswith(".mjs")
            else f"mineflayer/lib/anelf_mining/{name}"
        )
        source = (_RUNTIME / name).read_text(encoding="utf-8")
        if not target.exists() or target.read_text(encoding="utf-8") != source:
            updates[target] = source
    for target, updated in updates.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(updated, encoding="utf-8")
    return "patched" if updates else "exists"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="已安装 npm 依赖的 Minecraft 插件目录")
    print(patch_mining(parser.parse_args().payload))
