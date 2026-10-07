# 建造模板（Minecraft 陪玩）

沟通者使用的建造任务分解手册。**worker（mc-worker）是反射式执行者，不做规划**——
你要把任务分解为带明确坐标和参数的步骤序列，写进 `delegate_task` 的任务描述里；
单步小修（放一块、挖一格）自己做，不用派活。

## 通用纪律（所有建造任务）

1. **材料先行**：按模板算料 → `get_inventory` 核对 → 不够先 `collect_block`
   采集（或向玩家报缺料清单）。`fill_region` 是 best-effort：材料不足或够不到
   的格子会被静默跳过，事后要用 `get_block_at` 抽查或目视确认成品。
2. **坐标锚点**：以玩家指定位置或 bot 当前位置（`get_state`）为锚计算整数角点。
   `from`/`to` 是相对角点（含端点），任何顺序都可以。
3. **尺寸上限**：单次 `maxBlocks` 默认 256，硬上限 20000 格。超出就分段调用，
   每段之间让 worker 检查剩余材料。
4. **破坏性确认**：`fill_region`/`clear_region`/`dig_tunnel`/`dig_staircase`
   动手前向玩家复述「位置 + 尺寸 + 材料/工具」，等确认。绝不覆盖玩家既有建筑——
   锚点在玩家基地附近时，先 `get_block_at` 抽查目标区域是否已有方块，
   有就换址或请示。
5. **夜间照明**：地下或夜间施工时，让 worker 每推进 8~10 格用 `place_block`
   插一根火把（反射层只在空闲时插火把，干活时靠这个纪律）。

## 工具速查（参数以本表为准）

- `fill_region`：from/to（整数角点）+ block（如 "cobblestone"）+ maxBlocks
- `clear_region`：from/to + maxBlocks（挖空盒内所有实心方块）
- `dig_staircase`：direction（north/south/east/west/forward）+ depth（下降几格）
  [+height 默认 2]——从 bot 脚下开始向下凿楼梯
- `dig_tunnel`：direction + length [+width 默认 1 +height 默认 2]——水平直洞
- `place_block`：referenceX/Y/Z（参照方块）+ faceVector（朝向单位向量）
  + itemName——单块放置，贴面放

## 模板 1：应急庇护所（第一夜小屋）

尺寸 5(宽)×4(高)×5(深)，圆石/木板皆可。材料约 60~80 块 + 2 火把。
锚点 = 玩家指定点或 bot 站位，地面为 y，朝向任意。

1. 核对/采集主材（crafting 链：原木→木板，不够就 collect_block oak_log）。
2. 四面墙：4 次 `fill_region`，每面墙是 1 格厚、高 3 的薄片
   （from/to 让盒的厚度为 1）。门留 2 格空：正面墙的 fill 从 y+2 起
   （门洞高 2），或事后 `clear_region` 抠出 1×2 门洞。
3. 屋顶：1 次 `fill_region`，y+3 整面封顶。
4. 进门后 `place_block` 插 2 根火把（参照脚下方块，faceVector {x:0,y:1,z:0}）。
5. 汇报：成品尺寸、用料、门朝向；把家位置提示玩家可 `!sethome`。

## 模板 2：围墙 / 畜栏

玩家圈地需求先确认边界（两个对角）和高度（默认 2）。

- 实体墙：`fill_region` 沿四边各一次，block 用 cobblestone/brick 等。
- 栅栏栏：`fill_region` 的 block 用 "oak_fence"（栅栏可当填充块），
  门留 1 格空装栅栏门（`place_block` itemName "oak_fence_gate"）。
- 材料 = 周长 × 高度 + 10% 余量。矩形周长 = 2×(长+宽)。

## 模板 3：立柱 / 瞭望塔

`fill_region` from/to 同 x/z、高度差 = 柱高（1×N×1 盒）。
顶部加 `place_block` 放火把或信标。超过 8 格建议分段（中途抽查垂直度）。

## 模板 4：桥面 / 栈道

跨越沟壑或水面：确定两端点，`fill_region` 1~2 格厚平板，
block 用木板/圆石。宽度 1 人通行即可，玩家要求宽则加宽。
水下施工材料损耗大，优先圆石。

## 模板 5：矿洞下行（楼梯 + 巷道）

玩家要下矿时使用，**必须复述走向并等确认**（在玩家建筑下方乱挖是灾难）。

1. 在入口位置 `dig_staircase`（direction 选远离建筑方向，depth = 目标深度，
  height 2）。每段 depth 建议 ≤16，到底先插火把。
2. 目标层 `dig_tunnel` 掘进（length 16 一段，width 1，height 2），
  每段插火把。听到怪声/发现洞穴先停，向玩家报告再决定。
3. 返回：沿路插火把标记（或让玩家记坐标），用 `goto` 回到入口。

## 委派写法范例

```text
delegate_task(agent_name="mc-worker", prompt="在我脚边建 5x4x5 庇护所，锚点 (100,64,-50)。
步骤：1) get_inventory 核对圆石 ≥80，不足先 collect_block cobblestone 补齐；
2) fill_region 四面墙（各为厚1高3薄片，正面从 y+2 起留门洞）；
3) fill_region 屋顶 y+3 整面；4) 进屋 place_block 插 2 火把；
5) 返回 {done, actions, gained, failure}，报告实际放置数。")
```
