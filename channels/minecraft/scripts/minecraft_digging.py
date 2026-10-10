"""锁定版 Minecraft 挖掘的装备、可见性与服务器确认补丁。"""

from __future__ import annotations

import argparse
from pathlib import Path

_FINISH_ORIGINAL = """      if (bot.targetDigBlock) {
        bot._client.write('block_dig', {
          status: 2, // finish digging
          location: bot.targetDigBlock.position,
          face: bot.targetDigFace // always the same as the start face
        })
      }
      bot.targetDigBlock = null
      bot.targetDigFace = null
      bot.lastDigTime = performance.now()
      bot._updateBlockState(block.position, 0)"""
_FINISH_PATCHED = """      // The local timer only sends the finish request. A server block update
      // must confirm removal; predicting air here would resolve our own task.
      waitTimeout = setTimeout(() => {
        const error = new Error(`Server did not confirm digging ${block.name} at ${block.position}`)
        error.code = 'DIG_UNCONFIRMED'
        bot.stopDigging(error)
      }, 5000)
      if (bot.targetDigBlock) {
        bot._client.write('block_dig', {
          status: 2, // finish digging
          location: bot.targetDigBlock.position,
          face: bot.targetDigFace // always the same as the start face
        })
      }"""

_DESCRIPTION_ORIGINAL = (
    'Dig (break) the block at a world coordinate, equipping the best tool automatically.'
)
_DESCRIPTION_PATCHED = (
    'Dig a visible, reachable block with a harvestable tool from inventory.'
    ' Waits for the server to confirm removal; success does not confirm item pickup.'
    ' Use collect_block for gathering and get_inventory to verify actual gains.'
)
_HANDLER_ORIGINAL = """                const forceLook = args.forceLook ?? true;
                if (args.digFace !== undefined) {
                    await bot.dig(block, forceLook, args.digFace);
                }
                else {
                    await bot.dig(block, forceLook);
                }
                return { ok: true, dug: block.name };"""
_HANDLER_PATCHED = """                await bot.tool.equipForBlock(block, {
                    requireHarvest: bot.game.gameMode !== "creative",
                    getFromChest: false,
                });
                if (h.signal.aborted)
                    throw new ToolError("CANCELLED", "dig was cancelled while equipping");
                const forceLook = args.forceLook === "ignore" ? false : (args.forceLook ?? true);
                await bot.dig(block, forceLook, "raycast");
                return { ok: true, dug: block.name, serverConfirmed: true,
                    equipped: bot.heldItem?.name ?? null, collectionVerified: false };"""
_ERROR_ORIGINAL = """                    throw new ToolError("CANCELLED", "dig was cancelled");
                throw e instanceof ToolError ? e : new ToolError("INTERNAL", String(e?.message ?? e));"""
_ERROR_PATCHED = """                    throw new ToolError("CANCELLED", "dig was cancelled");
                if (e?.name === "NoItem")
                    throw new ToolError("MISSING_TOOL", `No harvestable tool for ${block.name}.`,
                        ["Check inventory and craft or obtain a suitable tool before retrying."]);
                if (e?.message === "Block not in view")
                    throw new ToolError("BLOCK_NOT_VISIBLE", `Cannot see an exposed face of ${block.name} at ${block.position}.`,
                        ["Move to an exposed face or choose an accessible block. find_blocks also finds buried blocks; nearby does not mean visible."]);
                if (e?.code === "DIG_UNCONFIRMED")
                    throw new ToolError("DIG_UNCONFIRMED", e.message,
                        ["No removal was confirmed. Check get_block_at and inventory; do not count this as mined or collected. Check reach, server permissions and connection before retrying."]);
                throw e instanceof ToolError ? e : new ToolError("INTERNAL", String(e?.message ?? e));"""

_PATCHES: dict[str, tuple[tuple[str, str], ...]] = {
    "mineflayer/lib/plugins/digging.js": (
        (_FINISH_ORIGINAL, _FINISH_PATCHED),
        ("    bot.stopDigging = () => {", "    bot.stopDigging = (error = new Error('Digging aborted')) => {"),
        ("      diggingTask.cancel(new Error('Digging aborted'))", "      diggingTask.cancel(error)"),
    ),
    "awesome-mineflayer-mcp/dist/tools/digging.js": (
        (_DESCRIPTION_ORIGINAL, _DESCRIPTION_PATCHED),
        (_HANDLER_ORIGINAL, _HANDLER_PATCHED),
        (_ERROR_ORIGINAL, _ERROR_PATCHED),
        (
            'Look at the block before digging; "ignore" to skip looking. Default true.',
            'Look before digging. Default true; false or legacy "ignore" turns smoothly. Visibility is always checked.',
        ),
        (
            'Face to dig from: "auto", "raycast", or a vector. Default "auto".',
            'An exposed face is always selected by raycast, including for legacy "auto" requests.',
        ),
    ),
}


def patch_digging(payload: Path) -> str:
    """先验证所有补丁位置，再写入服务器确认及采掘前置检查。"""
    updates: dict[Path, str] = {}
    for relative, replacements in _PATCHES.items():
        target = payload / "node_modules" / relative
        source = target.read_text(encoding="utf-8")
        updated = source
        for original, patched in replacements:
            if updated.count(patched) == 1:
                continue
            if updated.count(original) != 1:
                raise RuntimeError(f"无法定位唯一的挖掘补丁位置，请核对依赖版本: {target}")
            updated = updated.replace(original, patched, 1)
        if updated != source:
            updates[target] = updated
    for target, updated in updates.items():
        target.write_text(updated, encoding="utf-8")
    return "patched" if updates else "exists"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="已安装 npm 依赖的 Minecraft 插件目录")
    print(patch_digging(parser.parse_args().payload))
