"""安装单机器人动作控制器及有界运行指标。

Model Experience: 动作控制权和真实取消状态可查询，原停止工具返回 stopped。
Token effect: 两个只读 schema 增加数百 token，指标不自动注入上下文。
Cache effect: 工具列表仅安装时变化，实时状态留在结果和事件尾部。
"""

from __future__ import annotations

import argparse
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent / "plugins/minecraft/runtime"
_PATCHES: dict[str, tuple[tuple[str, str], ...]] = {
    "awesome-mineflayer-mcp/dist/server.js": (
        ('import { ActionLocks } from "./bot/action-locks.js";',
         'import { ActionController } from "./bot/anelf-actions.mjs";\n'
         'import { registerActions } from "./tools/anelf-actions.mjs";'),
        ("    const locks = new ActionLocks();", "    const locks = new ActionController(events);"),
        ("    registerStateInspect(reg);", "    registerStateInspect(reg);\n    registerActions(reg);"),
    ),
    "awesome-mineflayer-mcp/dist/tools/registry.js": (
        ('import { wrapMiningTools } from "./anelf-mining.mjs";',
         'import { wrapMiningTools } from "./anelf-mining.mjs";\n'
         'import { wrapActionTools, withActionRequest } from "./anelf-actions.mjs";'),
        ("        def = wrapMiningTools(def);", "        def = wrapMiningTools(def);\n        def = wrapActionTools(def);"),
        ('const callback = async (args) => {', 'const callback = async (args, extra) => {'),
        ('const run = def.handler((args ?? {}), ctx);',
         'const run = withActionRequest(ctx, extra?._meta?.["anelf/action"], () => def.handler((args ?? {}), ctx));'),
    ),
    "awesome-mineflayer-mcp/dist/bot/manager.js": (
        ("        loadPlugins(bot);", "        loadPlugins(bot);\n        this.locks.attach(bot);"),
    ),
    "awesome-mineflayer-mcp/dist/tools/gathering.js": (
        ("                    collectBlock.cancelTask?.();", "                    return collectBlock.cancelTask?.();"),
    ),
    "awesome-mineflayer-mcp/dist/tools/movement.js": (
        ("bot.pathfinder.setGoal(new goals.GoalInvert(new goals.GoalNear(args.x, args.y, args.z, args.distance)), true);",
         "bot.pathfinder.setGoal(new goals.GoalInvert(new goals.GoalNear(args.x, args.y, args.z, args.distance)), false);"),
    ),
    "awesome-mineflayer-mcp/dist/bot/plugins.js": (
        ('    bot.loadPlugin(armorManager);',
         '    bot.loadPlugin((loadedBot, options) => {\n'
         '        const prior = new Set(loadedBot.listeners("playerCollect"));\n'
         '        armorManager(loadedBot, options);\n'
         '        for (const listener of loadedBot.listeners("playerCollect")) {\n'
         '            if (!prior.has(listener)) loadedBot.removeListener("playerCollect", listener);\n'
         '        }\n'
         '    });'),
    ),
    "mineflayer-auto-eat/dist/new.js": (
        ('                this.bot.util.inv.customEquip(currentItem, wantedHand);',
         '                await this.bot.util.inv.customEquip(currentItem, wantedHand);'),
        ('            if (opts.equipOldItem && switchedItems && currentItem)\n'
         '                await this.bot.util.inv.customEquip(currentItem, wantedHand);\n'
         '            delete this._rejectionBinding;\n'
         '            this._eating = false;\n'
         "            this.emit('eatFinish', opts);",
         '            try {\n'
         '                if (opts.equipOldItem && switchedItems && currentItem)\n'
         '                    await this.bot.util.inv.customEquip(currentItem, wantedHand);\n'
         '            } finally {\n'
         '                delete this._rejectionBinding;\n'
         '                this._eating = false;\n'
         "                this.emit('eatFinish', opts);\n"
         '            }'),
    ),
}
_MODULES = {
    "action-controller.mjs": "bot/anelf-actions.mjs",
    "runtime-metrics.mjs": "bot/anelf-metrics.mjs",
    "action-tools.mjs": "tools/anelf-actions.mjs",
    "placement-hints.mjs": "tools/anelf-placement-hints.mjs",
    "action-context.mjs": "bot/anelf-action-context.mjs",
    "autonomous-actions.mjs": "bot/anelf-autonomous.mjs",
}


def patch_actions(payload: Path) -> str:
    """验证全部锚点后安装控制器，支持重复安装且拒绝依赖漂移。"""
    updates: dict[Path, str] = {}
    for relative, replacements in _PATCHES.items():
        target = payload / "node_modules" / relative
        source = target.read_text(encoding="utf-8")
        updated = source
        if relative.endswith("bot/plugins.js") and 'const priorArmorListeners' in updated:
            hook = '    bot.once("spawn", () => miningSafety.installSafety(bot));\n'
            legacy = ('    const priorArmorListeners = new Set(bot.listeners("playerCollect"));\n'
                      '    bot.loadPlugin(armorManager);\n'
                      '    for (const listener of bot.listeners("playerCollect")) {\n'
                      '        if (!priorArmorListeners.has(listener)) bot.removeListener("playerCollect", listener);\n'
                      '    }')
            normalized = updated.replace(hook, '')
            if normalized.count(legacy) != 1 or updated.count(hook) != 1:
                raise RuntimeError(f"无法定位唯一的动作控制补丁位置，请核对依赖版本: {target}")
            updated = normalized.replace(legacy, '    bot.loadPlugin(armorManager);\n' + hook.rstrip('\n'), 1)
        if relative.endswith("tools/registry.js"):
            updated = updated.replace(
                'import { wrapActionTools } from "./anelf-actions.mjs";',
                'import { wrapActionTools, withActionRequest } from "./anelf-actions.mjs";',
            )
        for original, patched in replacements:
            if updated.count(patched) == 1:
                continue
            if updated.count(original) != 1:
                raise RuntimeError(f"无法定位唯一的动作控制补丁位置，请核对依赖版本: {target}")
            updated = updated.replace(original, patched, 1)
        if updated != source:
            updates[target] = updated
    for name, relative in _MODULES.items():
        target = payload / "node_modules/awesome-mineflayer-mcp/dist" / relative
        source = (_RUNTIME / name).read_text(encoding="utf-8")
        if not target.exists() or target.read_text(encoding="utf-8") != source:
            updates[target] = source
    for target, source in updates.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return "patched" if updates else "exists"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="已安装合成和矿洞补丁的插件目录")
    print(patch_actions(parser.parse_args().payload))
