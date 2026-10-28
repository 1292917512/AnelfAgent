# 任务、心跳与规划

`agent/task/`、`agent/heartbeat/`、`agent/planning/` 的现状说明。修改任务定义、调度节奏或目标
规划时按需阅读。

## 心跳引擎（HeartbeatEngine）

```text
tick() 单次心跳：
  1. 内置维护：日志合并 + 实体计数持久化 + 记忆健康检查 + 实体画像分析
     + 空闲自动折叠（连续 conversation_fold_idle_beats 个心跳无新消息
     且积压 ≥ conversation_fold_idle_min 的会话 → 后台折叠 + 折后预热，
     把缓存断点移到无人时段）
     + 目标停滞概况（updated_at ≥7 天未更新的活跃目标一行事实，呈现给 AI
     决策续期/删除——目标规划适应长期工作，系统不做基于时间的自动清理）
     + cognee 压缩/空会话清扫等按期触发的维护项
  2. 遍历 task_schedules，递增 beat_count
  3. 选取一个到期任务 → TaskExecutor.run() → 结果记入心跳日志
  4. 持久化计数器到 config/heartbeat.json
```

- **心跳间隔单一真源 = `mind.heartbeat_interval`**（mind/core 配置组）：`assistant._heartbeat_loop`
  实际按其休眠，且 sleep 可被 ConfigManager 变更监听唤醒——间隔热更后立即按新值重排休眠。
  heartbeat.json 不存间隔副本（双源写读分离曾是「配置改了不生效 + 两处显示分裂」的根因）；
  引擎态势文案/调度节奏折算/scheduled 跨午夜窗口与 Web 心跳页展示统一读
  `heartbeat/config.current_interval_seconds()`；Web 保存 interval_seconds 经
  `HeartbeatService._save_interval_seconds` 路由 `save_mind_config`（与 PUT /config/meta 同
  coerce+clamp 纪律，下限 60s）
- **任务过期生命周期**（tick 维护段两级处理）：`_disable_expired_tasks` 过期即停（enabled=false
  写回 + 摘除调度；手动禁用先于过期的存量同样摘除——过期调度必然失效；定义文件保留供改期恢复）→
  `_delete_expired_tasks` 宽限期届满整体删除（定义文件+调度+执行历史+交接）。宽限期
  `task_expired_delete_grace_seconds`（默认 3 天、0=过期即删）、主开关 `task_expired_delete_enabled`，
  均在 heartbeat/maintenance 组
- **scheduled 槽位去重以执行历史为唯一事实源**：调度配置只存定义与节拍计数，不记 last_run 标记——
  `task_history.get_last_settled_runs()`（终态即原子落盘，success/no_output/incomplete 计入、error 保留重试）给出
  各任务最近一次已结算尝试的时间戳，调度重绑、执行期 reload 换配置对象、进程重启/取消都不再导致
  重复追跑。判定采用 occurrence 锚点语义：每个调度时刻取最近一个已到期 occurrence 与上次执行
  时间戳比较——多时刻槽位逐点独立（09:00 跑过不抑制 21:30）、停机只补最近一次不枚举积压、
  跨午夜补跑不吞今日正当槽位。连续失败达上限的定时任务经 `_task_giveup_dates` 台账当日放弃、
  跨日自动恢复；`HeartbeatConfig.set_schedule` 重绑时继承同名条目 beat_count（定义调整不抹进度），
  tick 收尾按任务名现取条目复位计数；无调度任务的失败/放弃台账随 `_prune_stale_runtime_state`
  对账清理。任务文件消失的孤儿调度、执行历史与交接文件随引擎构造与 reload 对账清理
  （`_prune_orphan_schedules` / `_prune_orphan_task_artifacts`；目录不可枚举的异常态跳过对账防误清）
- **完成事实与重试**：`TaskResult.completion_ready` 只接受明确完成。轮次耗尽、取消、未知终态及
  工具调用后的异常记为 `incomplete`，保留进展并提示核验，不当作完成记忆写入，也不自动重放同一
  调度槽位。计数型任务取消后复位当前配置中的节拍，等待下一周期；执行前失败保留原有重试。
  只有工具回执而无总结时中性报告回执，不能从调用次数推断操作已完成。
- **主 Driver 单航班执行**：tick 维护、到期任务、元决策周期与用户回复全部经
  `mind.driver`（`agent/mind/driver.py`）的常驻 worker 串行执行——同一时刻至多一个
  顶层思维工作；调用方（定时器/Web 手动/事件钩子/AI 工具）只做受理登记并立即返回，
  忙碌期间的唤醒被锁存、当前工作结束后继续 drain。`is_reply` 为真时 tick 仍只做维护
  与提醒、不启动新任务（让路语义不变，只是执行统一串行延后）
- **维护分段**：调度计数与任务选择留在 `HeartbeatEngine`，内置维护顺序位于
  `heartbeat/maintenance.py`。Driver 在阶段完成后串行处理已受理的用户周期，再恢复维护；
  一个 tick 只递增一次计数，手动 tick 的全部等待者仍共享终态。处理过用户消息的心跳
  不再追加一次自主元决策。不会中断正在执行的工具或并行运行多个主 Mind。
- **定时自主决策独立节奏**：`mind/core.autonomous_decision_interval` 默认 3600 秒，
  Driver 从创建时开始用单调时钟计时；心跳维护结束、没有用户周期需要让路且间隔到期，
  才认领并执行一次自主元决策。错过多个周期只执行一次，不补跑积压；配置热更在下个心跳
  按新间隔判断。决策失败也消费本次定时机会，已有待办继续沿用原异常恢复链路。
  `heartbeat_interval` 仍控制维护、到期任务检查和 heartbeat/idle 拍数；用户消息、后台
  通知与显式任务不受自主决策间隔限制，定时及空闲任务也不借此降频。
- **捕获让路与续跑**：材料经只读 `CaptureSource` 在组合根注入，记忆层不查找运行时或调度器。
  提取结果、每条记忆、反馈与关系阶段提交后才允许让路。
  捕获先释放会话锁，再调用调度回调，前台压缩可以刷新同一会话；恢复时重读检查点，
  已提交内容依靠出处键去重。持续有消息时，每段仍推进一个持久步骤；会话扫描轮转，
  避免前几个活跃频道长期占满捕获配额。关停不推进未完成批次的消息游标。
- **手动 tick 合并**：`MindDriver.submit_tick()` 在手动 tick 排队或执行期间收到的请求共用
  一次 `engine.tick()` 的结果或异常，不额外递增节拍或执行到期任务；单个等待者取消不影响
  其他等待者，关停取消全部等待者。该批次结束后再次提交才执行下一次手动 tick
- **心跳忙碌延后**（`assistant._heartbeat_loop`）：主 Driver 有正在执行或已受理的工作时
  不整轮跳过，按 `heartbeat_busy_defer_seconds`（默认 60s，热读取）短间隔轮询，空闲后
  立即补跑；被延后的 tick 不递增任何计数器
- **同任务排队去重**：引擎 `_task_inflight` 集合（asyncio 单线程 check-then-set 无竞态）——
  `claim_task` 在排队前认领（queued 与 running 共用同一集合），tick 选 task 时跳过 inflight
  名称，手动连点/Web trigger/AI 触发/事件触发/tick 五路径共用；关停时未开始的认领随
  `MindDriver.close()` 释放。出队后 `run_claimed_task` 重新读取最新任务定义；已删除或禁用的
  任务直接跳过，不进入执行器。完成、跳过、异常或取消都释放认领，之后重新启用可再次受理
- **心跳态势注入**（`_write_heartbeat_status` + 便签受管区块 AUTO:heartbeat-status，heartbeat 层）：
  AI 自我感知通道——心跳节奏、任务规模（**计数级**，具体内容经 list_tasks/task_history 按需取）、
  每条调度的节奏折算与最近一次执行、最近失败任务告警行。写入仅在内容变化时落盘（任务未执行期间
  字节冻结），刻意不含 total_ticks/beat_count 逐拍计数；任务/调度 CRUD 经 engine.reload() 即时刷新；
  lean 任务上下文不注入

## 触发模式

heartbeat（每 N 次心跳）/ scheduled（每天指定时间）/ idle / manual（仅手动）+ **事件触发**
（trigger_event 字段，经 LLM 钩子面注册 `task_event:<name>` 钩子，钩子经
`engine.submit_task` 提交主 Driver 并等待终态；与时间调度正交——时间调度管
「何时跑」，事件触发管「发生了什么之后跑」；不进 heartbeat.json 调度；详见
[思维分册·LLM 钩子面](mind-architecture.md)）。

**idle 空闲调度**：计数维度是「距上次思考的连续空闲心跳数」——`mind.last_activity_ts` 锚点
（`reply()`/`reflect()` 入口刷新，覆盖对话/任务/子代理/反思，含 idle 任务自身；心跳元决策的
LLM 调用不经 reflect 故不计）。本 tick 无确定性到期任务时才评估触发，空闲窗口让位给
scheduled/heartbeat；元决策 REFLECT 不立即执行，改为 `engine.mark_reflection_pending(reason)`
登记，由 idle 任务在空闲窗口消费（原因经 `TaskExecutor.run(extra_note=...)` 尾部追加注入，
缓存前缀不动）。写入侧 `validate_schedules` 强校验全局仅一条 idle，AI/Web 双路径同规则。

## 任务系统（agent/task/）

- **模型**（`model.py`）：TaskDefinition / TaskResult；任务产出允许的记忆类型集合单点定义
  （`TASK_MEMORY_TYPES`：reflection/semantic/episodic），from_dict 与 create/update_task 校验同源，
  非法值抛错显式暴露（不静默钳制）
- **注册表**（`registry.py`）：config/tasks/*.json 加载/CRUD；reload 跳过 `*.handoff.json` 等
  运行数据文件
- **执行器**（`executor.py`）：LLM 调用 + 结果存储；`task_lean_context` 精简上下文（人设+工具+
  永久记忆+任务指令，环境便签/召回/状态由任务按规则经工具取回——任务间共享稳定前缀、每轮
  prompt 更小）；`extra_note` 尾部追加动态备注（idle 反思原因注入不破前缀）
- **执行历史**（`history.py`）：每任务保留最近 N 条（开始时间/耗时/状态/触发来源/产出摘要），
  `<data_dir>/task_history.json`，executor 各终态唯一写入方；Web 任务列表 last_run 与 AI
  list_tasks/task_history 只读消费；任务删除时清理。也是 scheduled 去重的事实源（见上）
- **长任务交接**（`handoff.py`）：任务定义 `handoff: true` 时输出末尾 `# HANDOFF` 块持久化，
  下次运行注入（确定性接力）
- **工具面**（`tools.py`）：create_task / update_task / delete_task / set_task_schedule，
  与 Web 管理面同路径热重载

## 规划系统（agent/planning/）

- 目标 CRUD（create_goal/update_goal/delete_goal）：**终态即清**——update_goal 终态即删、全部
  步骤完成后自动收口、delete_goal 显式删除；目标关闭只由完成事实驱动，系统不做基于时间的自动
  清理；update_goal 越界 step_index/非法 step_status 返回 PARAM 错误（不静默 no-op）；
  not_found 错误附 `active_goals` 简报供 AI 一次自纠。步骤完成与下一步骤推进同次写入，
  基于读取版本提交；版本冲突返回明确错误并要求重读，不报告已更新
- **规划态势轮内注入**（`agent/planning/situation.py` 版本化快照 + provider `plan_ops`，priority 30）：
  活跃目标/计划快照（goal_id/标题/目标描述/步骤进度，以及最多两条待处理步骤备注，当前
  scope 的执行计划置顶）经 provider 层每轮注入。描述与备注有界且共用原字符预算；完整内容
  经 get_goal 读取。系统呈现已保存状态，由 AI 根据执行证据更新，不推测完成或自动改写目标。
  全部规划写路径变更后调 `situation.invalidate()`，快照按版本失配（+60s 再同步窗口兜底）单飞重建，
  稳态渲染零 I/O；reflect 前缀 scope（任务/子代理）不注入保持 lean 语义；配置
  `goals_inject_enabled`（planning/core 组），group=planning 随工具组启停联动
- **规划条目不投影 cognee**：goal upsert 不入队（见[记忆分册](memory-system.md)），原生检索
  已覆盖召回
