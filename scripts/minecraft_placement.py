"""锁定版 Minecraft 执行器的放置前置校验。

Model Experience: 工具错误返回支撑方块/自身碰撞的事实及恢复建议，约数十
token；工具描述补充坐标语义，schema 前缀更新一次，失败事实留在工具结果。
"""

from __future__ import annotations

import argparse
from pathlib import Path

_DESCRIPTION_ORIGINAL = "faceVector is the unit direction from the reference block toward the new block."
_DESCRIPTION_PATCHED = (
    "faceVector is one of the six axis-aligned unit directions. Inspect the reference block first;"
    " it must not be air. Destination = reference block position + faceVector. Choose a clear spot"
    " beside the bot, not its feet or body. A crafting table in inventory must be placed before use."
)

_HANDLER_ORIGINAL = """            if (args.itemName !== undefined) {
                const resolved = resolveItem(bot.registry, args.itemName);
                await bot.equip(resolved.id, "hand");
            }
            const ref = bot.blockAt(new Vec3(args.referenceX, args.referenceY, args.referenceZ));
            if (!ref) {
                throw new ToolError("NOT_FOUND", `No reference block at (${args.referenceX}, ${args.referenceY}, ${args.referenceZ}).`);
            }
            const face = new Vec3(args.faceVector.x, args.faceVector.y, args.faceVector.z);"""

_HANDLER_PATCHED = """            const face = new Vec3(args.faceVector.x, args.faceVector.y, args.faceVector.z);
            const axes = [face.x, face.y, face.z];
            if (!axes.every(Number.isInteger) || axes.reduce((sum, v) => sum + Math.abs(v), 0) !== 1) {
                throw new ToolError("INVALID_FACE", "faceVector must be one of the six axis-aligned unit directions.");
            }
            const ref = bot.blockAt(new Vec3(args.referenceX, args.referenceY, args.referenceZ));
            if (!ref || ["air", "cave_air", "void_air"].includes(ref.name)) {
                throw new ToolError("INVALID_REFERENCE", `Reference at (${args.referenceX}, ${args.referenceY}, ${args.referenceZ}) is ${ref?.name ?? "not loaded"}, not a supporting block.`,
                    ["Use get_block_at to find an existing supporting block beside the bot. The bot position is its feet, not the ground block. Destination = reference.position + faceVector."]);
            }
            const item = args.itemName !== undefined ? resolveItem(bot.registry, args.itemName) : bot.heldItem;
            const dest = ref.position.plus(face);
            // Only static full cubes are checked here. State-dependent shapes
            // and non-solid items such as torches remain server-validated.
            const collisions = bot.registry.blockCollisionShapes;
            const shapeId = item && collisions?.blocks[item.name];
            const shapes = typeof shapeId === "number" ? collisions.shapes[shapeId] : null;
            const cube = [0, 0, 0, 1, 1, 1];
            const fullCube = shapes?.length === 1 && shapes[0].length === 6
                && shapes[0].every((v, i) => v === cube[i]);
            const entity = bot.entity;
            if (!args.asEntity && fullCube && entity?.position && entity.width > 0 && entity.height > 0) {
                const p = entity.position;
                const half = entity.width / 2;
                if (dest.x < p.x + half && dest.x + 1 > p.x - half
                    && dest.y < p.y + entity.height && dest.y + 1 > p.y
                    && dest.z < p.z + half && dest.z + 1 > p.z - half) {
                    throw new ToolError("PLACEMENT_BLOCKED", `Destination ${dest} overlaps the bot's body.`,
                        ["Choose a clear adjacent spot, inspect its support and destination with get_block_at, or move aside before placing. Do not repeat the same coordinates."]);
                }
            }
            if (args.itemName !== undefined) {
                await bot.equip(item.id, "hand");
            }"""


def patch_placement(payload: Path) -> str:
    """在装备和放置前校验支撑点、朝向及整方块与自身的碰撞。"""
    target = payload / "node_modules/awesome-mineflayer-mcp/dist/tools/digging.js"
    source = target.read_text(encoding="utf-8")
    updated = source
    for original, patched in (
        (_DESCRIPTION_ORIGINAL, _DESCRIPTION_PATCHED),
        (_HANDLER_ORIGINAL, _HANDLER_PATCHED),
    ):
        if updated.count(patched) == 1:
            continue
        if updated.count(original) != 1:
            raise RuntimeError(f"无法定位唯一的放置补丁位置，请核对依赖版本: {target}")
        updated = updated.replace(original, patched, 1)
    if updated == source:
        return "exists"
    target.write_text(updated, encoding="utf-8")
    return "patched"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="已安装 npm 依赖的 Minecraft 插件目录")
    print(patch_placement(parser.parse_args().payload))
