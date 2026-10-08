"""游戏频道的稳定派工与结果核验规则。

Model Experience: 每次游戏回复均看到派工契约，不依赖玩家写出技能触发词。
Token effect: 每轮增加约数百 token，避免重复侦查、查目录和激活分组。
Cache effect: 经 channel_policy 尾部层注入，实时任务事实仍留在工具结果。
"""

from agent.channel.reply_policy import ReplyPolicy

COMPANION_GAME_TOOLS = frozenset({
    "get_inventory", "get_state", "get_block_at", "place_block", "craft_item", "mine_resources",
})


def companion_policy(server: str) -> ReplyPolicy:
    """按配置的 MCP 服务生成游戏会话策略。"""
    return ReplyPolicy(
        direct_reply=True,
        tool_groups=(f"mcp:{server}",),
        instructions=(
            "[Minecraft 陪玩执行契约]\n"
            "本频道游戏请求由当前回复负责，不另起 tool_action 或重复执行。游戏工具已在当前目录中，"
            "只用真实工具名，不先查目录/激活分组。当前名称是 `get_inventory`、`get_state`、`get_block_at`、"
            "`place_block`、`craft_item`；委托中也必须原样书写，禁止添加 mcp__minecraft__ 等前缀。\n"
            "单步查询、跟随、停止和启动 `mine_resources` 可直接做；玩家新提出的制作、放工作台再合工具、采集等多步任务，"
            "第一轮实际调用 delegate_task(agent_name='mc-worker', background=true)。"
            "必须读取本次工具返回的成功状态和 delegation_id，确认任务成功启动后，才可用 send_message 告知已派工。"
            "没有本次委托成功的工具结果就是尚未派工；文字承诺、历史任务和自己的独白均不算派工事实。"
            "不要在主会话先查背包、预演、present_plan 或执行同一组动作；委托失败才如实解释。"
            "成功委托后无需再建计划或定时提醒；此时才结束本轮等待后台完成通知，不轮询。\n"
            "后台任务完成/失败/取消通知只用于核对并汇报，不能当成玩家的新请求再次 delegate_task，"
            "更不能从头重做消耗材料；没有玩家新指令就待命。\n"
            "委托的 task/context 必须保留玩家原始目标、世界和全部约束，并带上以下执行规则："
            "只用指定游戏 MCP 工具，不调用 shell/记忆/规划工具；首轮并行 get_inventory 和 get_state，"
            "同一机器人的写动作串行。已有工作台可复用；新台先确认背包到账，再选身体外的空位和可见实心支撑面，"
            "place_block 后用 get_block_at 确认 crafting_table，才能用 craftingTablePos 合三乘三配方。"
            "craft_item 的 count 是配方操作次数。合成后再查背包，按同名物品全部槽位求和；"
            "额外新做数量按本次背包增量核验，不能拿原有工具冒充。"
            "取消/失败先核对 crafting 光标和格子，未确认收尾不能继续；最多重试一次，"
            "受阻时报告事实而非要求玩家重复试错。\n"
            "worker 返回后只按实际入包、服务器方块与终态汇报；预算耗尽、partial、blocked、"
            "通用计划完成都不是游戏任务成功证据。保留用户限制，不擅自采集、挖掘、丢弃或另起任务。"
        ),
    )
