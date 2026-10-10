"""锁定版 Minecraft 执行器的合成同步补丁。

Model Experience: 工具失败后显示合成格与光标事实，防止把部分执行误报为
材料不足；失败与下线前归还临时物品，满包不自动丢弃。字段只随工具结果返回，
不改会话稳定前缀；空闲快照增量为数十 token，收尾失败附简短原因。
"""

from __future__ import annotations

import argparse
from pathlib import Path

_HELPER = Path(__file__).resolve().parent.parent / "mcp/runtime/inventory-safety.cjs"

_OVERFLOW_ORIGINAL = """        if (emptySlot === null) { // no room left
          if (slot === null) { // no room => drop it
            await tossLeftover()
          } else { // if there is still some leftover and slot is not null, click slot
            await clickWindow(slot, 0, 0)
            await tossLeftover()
          }
        } else {"""
_OVERFLOW_PATCHED = """        if (emptySlot === null) {
          throw new Error('Inventory full: cursor item retained; automatic dropping is disabled')
        } else {"""
_TOSS_ORIGINAL = """
    async function tossLeftover () {
      if (window.selectedItem) {
        await clickWindow(-999, 0, 0)
      }
    }
"""
_TOSS_PATCHED = "\n    // Overflow leaves items on the cursor for explicit recovery.\n"

_CATCH_ORIGINAL = """      if (windowCraftingTable) {
        bot.closeWindow(windowCraftingTable)
        windowCraftingTable = undefined
      }
      throw new Error(err)"""
_CATCH_PATCHED = """      // Keep the window open until runCraft has returned its transient items.
      windowCraftingTable = undefined
      throw new Error(err)"""
_CRAFT_ORIGINAL = "  bot.craft = craft"
_CRAFT_PATCHED = """  const { runCraft } = require('../anelf_inventory')
  bot.craft = (...args) => runCraft(bot, () => craft(...args))"""

_CRAFT_CHECKPOINTS = tuple(
    (anchor, anchor + "\n" + indent + "require('../anelf_inventory').checkCraft(bot)")
    for anchor, indent in (
        ("      for (let i = 0; i < count; i++) {", "        "),
        ("      async function clickShape () {", "        "),
        ("      async function nextIngredientsClick () {", "        "),
        ("      async function grabResult () {", "        "),
    )
)

_MANAGER_IMPORT_ORIGINAL = 'import { createBot } from "mineflayer";'
_MANAGER_IMPORT_PATCHED = _MANAGER_IMPORT_ORIGINAL + '\nimport inventorySafety from "mineflayer/lib/anelf_inventory.js";'
_REQUIRE_BOT_ORIGINAL = '        if (!this._bot || this._status !== "online") {'
_REQUIRE_BOT_PATCHED = '        if (!this._bot || this._status !== "online" || this.intentionalQuit) {'
_DISCONNECT_ORIGINAL = """        this.locks.cancelAll("manual");
        await new Promise"""
_DISCONNECT_PATCHED = """        this.locks.cancelAll("manual");
        const inventory = force ? { restored: false, reason: "forced disconnect" }
            : await inventorySafety.prepareDisconnect(bot);
        await new Promise"""
_DISCONNECT_RESULT_ORIGINAL = "        return { status: this._status, reason };"
_DISCONNECT_RESULT_PATCHED = "        return { status: this._status, reason, inventory };"
_SHUTDOWN_ORIGINAL = """    shutdown() {
        this.cancelReconnectTimer();
        this.intentionalQuit = true;
        this.locks.cancelAll("shutdown");
        try {
            this._bot?.quit("server shutting down");
        }
        catch {
            /* ignore */
        }
        this.teardown("disconnected", "shutdown");
    }"""
_SHUTDOWN_PATCHED = """    async shutdown() {
        await this.disconnect("server shutting down");
    }"""
_EXIT_ORIGINAL = "    const shutdown = (signal) => {"
_EXIT_PATCHED = "    const shutdown = async (signal) => {"
_AWAIT_EXIT_ORIGINAL = "            ctx.manager.shutdown();"
_AWAIT_EXIT_PATCHED = "            await ctx.manager.shutdown();"
_EOF_ORIGINAL = '    process.on("SIGTERM", () => shutdown("SIGTERM"));'
_EOF_PATCHED = _EOF_ORIGINAL + '\n    process.stdin.on("end", () => void shutdown("stdin end"));'

_WAIT_ORIGINAL = """    if (window.type === 'minecraft:inventory') {
      if (slot >= 0 && slot <= 4) {
        await once(bot.inventory, 'updateSlot:0')
      }
    } else if (window.type === 'minecraft:crafting') {
      if (slot >= 0 && slot <= 9) {
        await once(bot.currentWindow, 'updateSlot:0')
      }
    } else if (window.type === 'minecraft:merchant') {"""

_WAIT_PATCHED = """    const craftingSlots = window.type === 'minecraft:inventory' ? 4
      : window.type === 'minecraft:crafting' ? 9 : -1
    if (craftingSlots >= 0 && slot >= 0 && slot <= craftingSlots) {
      // Partial recipes can leave slot 0 unchanged. Confirm the whole window
      // before the next click instead of waiting for an optional slot update.
      if (bot.supportFeature('stateIdUsed')) await syncWindow(window)
      else await once(window, 'updateSlot:0')
    } else if (window.type === 'minecraft:merchant') {"""

_FINISH_ORIGINAL = """      if (windowCraftingTable) {
        // The last put-away click is unconfirmed; closing on a wrong model
        // leaves items the server never placed.
        await bot._syncWindow(windowCraftingTable)
        await bot.closeWindow(windowCraftingTable)"""
_FINISH_PATCHED = """      // Confirm the last inventory click for both 2x2 and 3x3 crafting.
      await bot._syncWindow(windowCraftingTable || bot.inventory)
      if (windowCraftingTable) {
        await bot.closeWindow(windowCraftingTable)"""

_STATE_ORIGINAL = """        items: inv.items().map((i) => serializeItem(i, detailed)),
        armor,"""
_STATE_PATCHED = """        items: inv.items().map((i) => serializeItem(i, detailed)),
        crafting: (() => {
            const window = bot.currentWindow ?? inv;
            const size = window.type === "minecraft:crafting" ? 9
                : window.type === "minecraft:inventory" ? 4 : 0;
            return {
                windowId: window.id,
                windowType: window.type,
                emptySlots: window.slots.slice(window.inventoryStart, window.inventoryEnd)
                    .flatMap((item, index) => item ? [] : [window.inventoryStart + index]),
                cursor: serializeItem(window.selectedItem ?? null, detailed),
                slots: size ? window.slots.slice(0, size + 1).map((i) => serializeItem(i, detailed)) : [],
            };
        })(),
        armor,"""

_PREFLIGHT_ORIGINAL = """            const id = resolved.id;
            const table = args.craftingTablePos"""
_PREFLIGHT_PATCHED = """            const id = resolved.id;
            const window = bot.currentWindow ?? bot.inventory;
            const gridSize = window.type === "minecraft:crafting" ? 9
                : window.type === "minecraft:inventory" ? 4 : 0;
            if (window.selectedItem || (gridSize && window.slots.slice(0, gridSize + 1).some(Boolean))) {
                throw new ToolError("CRAFTING_STATE_DIRTY", "An unfinished inventory operation is holding items on the cursor or crafting grid.",
                    ["Inspect get_inventory.crafting and recover these items before crafting again. Do not retry a dependent recipe or assume the ingredients are missing."]);
            }
            const table = args.craftingTablePos"""

_COUNT_ORIGINAL = """            const count = args.count ?? 1;
            const h = ctx.locks.begin("craft_item");"""
_COUNT_PATCHED = """            const count = args.count ?? 1;
            const missing = recipes[0].delta.filter((d) => d.count < 0
                && bot.inventory.count(d.id, d.metadata) < -d.count * count);
            if (missing.length) {
                throw new ToolError("MISSING_MATERIALS", "Not enough materials for all requested craft operations.",
                    ["count is operations, not output items. One stick operation makes four sticks; inspect inventory before retrying."]);
            }
            const h = ctx.locks.begin("craft_item");"""

_ERROR_ORIGINAL = """                throw e instanceof ToolError ? e : new ToolError("INTERNAL", String(e?.message ?? e));"""
_ERROR_PATCHED = """                const message = String(e?.message ?? e);
                throw new ToolError(e instanceof ToolError ? e.code : "INTERNAL", message, [
                    ...(e instanceof ToolError ? e.suggestions ?? [] : []),
                    "Crafting may be partially complete. Read get_inventory, including crafting.cursor and crafting.slots, before another action. Do not assume materials are missing or retry a dependent recipe.",
                ]);"""


def patch_crafting(payload: Path) -> str:
    """安装合成确认、整批材料校验和失败状态可见性，版本漂移时拒绝写入。"""
    modules = payload / "node_modules"
    changes = (
        (modules / "mineflayer/lib/plugins/inventory.js", (
            (_WAIT_ORIGINAL, _WAIT_PATCHED), (_OVERFLOW_ORIGINAL, _OVERFLOW_PATCHED), (_TOSS_ORIGINAL, _TOSS_PATCHED),
        )),
        (modules / "mineflayer/lib/plugins/craft.js", (
            (_FINISH_ORIGINAL, _FINISH_PATCHED), (_CATCH_ORIGINAL, _CATCH_PATCHED), (_CRAFT_ORIGINAL, _CRAFT_PATCHED),
            *_CRAFT_CHECKPOINTS,
        )),
        (modules / "awesome-mineflayer-mcp/dist/bot/state.js", ((_STATE_ORIGINAL, _STATE_PATCHED),)),
        (modules / "awesome-mineflayer-mcp/dist/tools/crafting.js", (
            (_PREFLIGHT_ORIGINAL, _PREFLIGHT_PATCHED),
            (_COUNT_ORIGINAL, _COUNT_PATCHED), (_ERROR_ORIGINAL, _ERROR_PATCHED),
        )),
        (modules / "awesome-mineflayer-mcp/dist/bot/manager.js", (
            (_MANAGER_IMPORT_ORIGINAL, _MANAGER_IMPORT_PATCHED), (_DISCONNECT_ORIGINAL, _DISCONNECT_PATCHED),
            (_REQUIRE_BOT_ORIGINAL, _REQUIRE_BOT_PATCHED),
            (_DISCONNECT_RESULT_ORIGINAL, _DISCONNECT_RESULT_PATCHED), (_SHUTDOWN_ORIGINAL, _SHUTDOWN_PATCHED),
        )),
        (modules / "awesome-mineflayer-mcp/dist/index.js", (
            (_EXIT_ORIGINAL, _EXIT_PATCHED), (_AWAIT_EXIT_ORIGINAL, _AWAIT_EXIT_PATCHED), (_EOF_ORIGINAL, _EOF_PATCHED),
        )),
    )
    helper = _HELPER.read_text(encoding="utf-8")
    pending: list[tuple[Path, str]] = []
    for target, replacements in changes:
        source = target.read_text(encoding="utf-8")
        if target.name == "inventory.js" and "  async function syncWindow (window) {" not in source:
            raise RuntimeError(f"缺少合成窗口同步接口，请核对 Mineflayer 版本: {target}")
        updated = source
        for original, patched in replacements:
            if updated.count(patched) == 1:
                continue
            if updated.count(original) != 1:
                raise RuntimeError(f"无法定位唯一的合成补丁位置，请核对依赖版本: {target}")
            updated = updated.replace(original, patched, 1)
        if updated != source:
            pending.append((target, updated))
    helper_path = modules / "mineflayer/lib/anelf_inventory.js"
    if not helper_path.exists() or helper_path.read_text(encoding="utf-8") != helper:
        pending.append((helper_path, helper))
    for target, source in pending:
        target.write_text(source, encoding="utf-8")
    return "patched" if pending else "exists"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="已安装 npm 依赖的 Minecraft 插件目录")
    options = parser.parse_args()
    print(patch_crafting(options.payload))
