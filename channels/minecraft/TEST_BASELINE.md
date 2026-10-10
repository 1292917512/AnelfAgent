# Minecraft 复审：全量测试基线

日期：2026-10-09。原始 HEAD：`87ee8b0e5169e1d1a4f80dd8a592f4e4dbd567ff`；
本批最终代码：`b4e27af`（含之前五笔通用修复）。本文记录失败证据，不是跳过名单。

## 全量结果与条件

| 检出 | 总用例 | 通过 | 失败 | 跳过 |
| --- | ---: | ---: | ---: | ---: |
| 干净原始 HEAD | 5393 | 5331 | 45 | 17 |
| 当前代码 | 5457 | 5392 | 45 | 20 |

同一 Windows 主机、Python 3.12.13、pytest 9.1.1、同一 `.venv`；使用仓库原有
`testpaths = tests, entities, channels`、严格标记和 120 秒超时，无 `-k` 或排除列表。
原始 HEAD 使用独立 Git worktree，运行前后 `git status --porcelain` 均为空。
两次全量顺序执行；不使用全量测试耗时评价 Minecraft 性能。

命令形式：`python -u -m pytest -vv --basetemp=<新的独立临时目录> --junitxml=<报告路径>`。
完整日志和 JUnit 在本机 `workspace/minecraft/diagnostics/review-20261009/`。
HEAD 的第一次运行中断、无结束报告，未计入；第二次完整报告统计 5393 项。
Windows 进程测试会向父进程带来控制信号；当前测试包装器等待 pytest 完整退出，记录退出码 1。

通过数相差 61：新增和改名后的测试净增 61 项。跳过数多 3 项：本地模型配置使已禁用的
LLM 缓存真实调用测试从 HEAD 的一个 NOTSET 占位展开成四个模型参数，全部仍跳过，未发真实模型请求。
没有把“总用例数变化”或“失败数同为 45”当成清单一致的证据。

此前文档的 5377 通过、44 失败来自当时的工作区全量；其 HEAD 对照当时仅重跑失败项。
本次补跑原始 HEAD 全量，历史 44 项全部复现。对方所说的“5 个失败”缺少对应原始报告、
平台和命令，因此目前无法解释其与 44 的精确差额，不能据此推断多出了 39 个回归。

## 逐项对照与时序差异

两次全量共同失败 44 项；这个集合包含下面历史清单中的 43 项及进程信号用例。
另两项结果变化如下，不能称失败清单完全相同：

| 用例 | HEAD 全量 | 当前全量 | 补充验证 |
| --- | --- | --- | --- |
| `entities/filesystem/tests/test_ops_context.py::TestDocs::test_doc_refresh_on_mtime_change` | 失败 | 通过 | 各重复 10 次：HEAD 8 次失败、当前 5 次失败；未修改的代码上均不稳定 |
| `tests/unit/agent/realtime/test_engine.py::TestSpeakArbitration::test_speak_different_text_still_spoken` | 通过 | 失败 | 原样各重复 30 次均通过；相同的 40ms 测试接收端延迟在两份代码上均使原断言失败 |

语音用例只等待 `lane.active is None`，随后立即断言两个 `audio_done` 已送达。
`SpeakLane.settled()` 标记生产结束；通知通过独立播放队列与 `RealtimeSession._writer_loop()` 送达。
因此生产结束并不保证接收端已收到通知，当前全量结果与此既有时序缺口一致。
语音实现和该测试在两个版本间完全无差异。40ms 是测试接收端的受控延迟，
不是正常全量条件；不把这个实验或重复测试通过宣称为修复完成。

重复命令使用 pytest 原生 `--keep-duplicates`，重复文件路径、按上述两个名称 `-k` 筛选，
每次独立创建 fixture。有效报告为 `repeat-head.xml` / `repeat-current.xml`（各 40 项）；
临时重复收集对象导致 fixture 错误的 `repeat-cases-*` 报告无效，不计入结论。
延迟实验见 `speech-delay-head.xml` / `speech-delay-current.xml`，仅改变测试接收端时序。

新增观察到的共同失败：
`tests/unit/core/test_instance_guard.py::TestAcquire::test_foreign_process_never_killed`。
堆栈在 `proc.wait()` 期间进入 `core/application.py::_win_handler`，访问已关闭的事件循环。
前序 `test_arm_signals_registers_handlers_and_requester` 在 Windows 调用不支持的
`loop.remove_signal_handler()` 失败，未恢复 `signal.signal()` 注册的处理器。
该跨测试残留在新旧全量中都存在，应单独修复信号测试的清理路径。

## 历史 44 项完整清单

HEAD 本次全部失败；当前有一项 mtime 用例通过，其余失败。保留完整 node ID，后续按项比较，
不得仅比较失败总数，也不得自动忽略此清单里的新报错形态。

| # | Pytest node ID | 当前全量 |
| ---: | --- | --- |
| 1 | `channels/qq/tests/test_qq_media.py::TestDownloadFileFallback::test_get_file_hit_stops_chain` | 失败 |
| 2 | `channels/qq/tests/test_qq_media.py::TestDownloadFileFallback::test_image_file_id_falls_back_to_get_image` | 失败 |
| 3 | `channels/qq/tests/test_qq_media.py::TestDownloadFileFallback::test_voice_file_id_falls_back_to_get_record` | 失败 |
| 4 | `entities/filesystem/tests/test_child_guard.py::TestSweep::test_current_boot_entry_kept` | 失败 |
| 5 | `entities/filesystem/tests/test_child_guard.py::TestSweep::test_dead_entry_cleaned_without_kill` | 失败 |
| 6 | `entities/filesystem/tests/test_child_guard.py::TestSweep::test_stale_group_swept` | 失败 |
| 7 | `entities/filesystem/tests/test_child_guard.py::TestTerminateAll::test_shutdown_terminates_live_children` | 失败 |
| 8 | `entities/filesystem/tests/test_file_edit_tools.py::TestEditFile::test_curly_quotes_matched_and_preserved` | 失败 |
| 9 | `entities/filesystem/tests/test_notebook.py::TestNotebookEdit::test_insert_cell` | 失败 |
| 10 | `entities/filesystem/tests/test_notebook.py::TestNotebookEdit::test_replace_cell` | 失败 |
| 11 | `entities/filesystem/tests/test_notes_guard.py::TestFilesystemGuards::test_hint_absolute_md_in_notes_root` | 失败 |
| 12 | `entities/filesystem/tests/test_notes_guard.py::TestFindShellViolation::test_absolute_path_into_notes_root` | 失败 |
| 13 | `entities/filesystem/tests/test_notes_guard.py::TestFindShellViolation::test_nonexistent_path_in_notes_root_blocked` | 失败 |
| 14 | `entities/filesystem/tests/test_notes_guard.py::TestFindShellViolation::test_notes_root_dir_itself` | 失败 |
| 15 | `entities/filesystem/tests/test_ops_context.py::TestDocs::test_doc_refresh_on_mtime_change` | 通过（时序不稳定） |
| 16 | `entities/filesystem/tests/test_paths_anchor.py::TestGetWorkspaceRoot::test_project_root_guard_falls_back[/]` | 失败 |
| 17 | `entities/filesystem/tests/test_paths_anchor.py::TestGetWorkspaceRoot::test_project_root_guard_falls_back[~]` | 失败 |
| 18 | `entities/filesystem/tests/test_paths_anchor.py::TestResolveWorkspacePath::test_relative_path_anchors_root` | 失败 |
| 19 | `entities/filesystem/tests/test_paths_anchor.py::TestResolveWorkspacePath::test_workspace_prefix_stripped` | 失败 |
| 20 | `entities/filesystem/tests/test_shell_background.py::TestLaunchBackground::test_returns_task_info_immediately` | 失败 |
| 21 | `entities/filesystem/tests/test_shell_background.py::TestLaunchBackground::test_timeout_alerts_without_killing` | 失败 |
| 22 | `entities/filesystem/tests/test_shell_background.py::TestToolIntegration::test_run_shell_command_background_explicit_timeout_alerts` | 失败 |
| 23 | `entities/filesystem/tests/test_shell_guard.py::TestCheckCommandSafety::test_benign_commands_allowed[echo x > /data/workspace/out.txt]` | 失败 |
| 24 | `entities/filesystem/tests/test_shell_guard.py::TestToolIntegration::test_check_disabled_by_config` | 失败 |
| 25 | `entities/filesystem/tests/test_shell_tool.py::TestMemoryKeyNote::test_absolute_path_into_notes_root_blocked` | 失败 |
| 26 | `entities/filesystem/tests/test_shell_tool.py::TestMissingModuleHint::test_note_in_failure_result` | 失败 |
| 27 | `entities/filesystem/tests/test_shell_tool.py::TestOutputPersistence::test_large_output_persisted` | 失败 |
| 28 | `entities/ssh/tests/test_ssh_store.py::TestPersistence::test_file_permission_600` | 失败 |
| 29 | `entities/vault/tests/test_service.py::TestMachineMode::test_machine_key_file_permissions` | 失败 |
| 30 | `tests/unit/agent/mind/test_background_output_cursor.py::TestReadTaskOutput::test_incremental_reads_return_only_new_output` | 失败 |
| 31 | `tests/unit/approval/test_path_normalization.py::TestBypassPrevention::test_absolute_glob_matches` | 失败 |
| 32 | `tests/unit/approval/test_path_normalization.py::TestBypassPrevention::test_dot_bypass_caught` | 失败 |
| 33 | `tests/unit/approval/test_path_normalization.py::TestBypassPrevention::test_dotdot_bypass_caught` | 失败 |
| 34 | `tests/unit/approval/test_path_normalization.py::TestBypassPrevention::test_relative_glob_still_matches` | 失败 |
| 35 | `tests/unit/approval/test_path_normalization.py::TestBypassPrevention::test_tilde_bypass_caught` | 失败 |
| 36 | `tests/unit/approval/test_path_normalization.py::TestPathResolution::test_dot_prefix_stripped` | 失败 |
| 37 | `tests/unit/approval/test_policy_arg_patterns.py::TestExtractMatchableArg::test_move_file_two_paths` | 失败 |
| 38 | `tests/unit/approval/test_policy_arg_patterns.py::TestPolicyMatching::test_tool_name_glob_with_arg_pattern` | 失败 |
| 39 | `tests/unit/core/test_application.py::test_arm_signals_registers_handlers_and_requester` | 失败 |
| 40 | `tests/unit/core/test_context_provider.py::TestFreshCollect::test_stale_cache_refreshes_inline` | 失败 |
| 41 | `tests/unit/core/test_context_provider.py::TestGetStatusScopeAggregation::test_panel_picks_most_recent_scope` | 失败 |
| 42 | `tests/unit/core/test_shell_env.py::TestRunCommandHygiene::test_defaults_injected` | 失败 |
| 43 | `tests/unit/core/test_shell_env.py::TestRunCommandHygiene::test_locale_defaults` | 失败 |
| 44 | `tests/unit/core/test_shell_env.py::TestRunCommandHygiene::test_user_value_wins` | 失败 |

失败包含 Windows 路径/glob、POSIX 命令与 `killpg`、权限位、GBK/换行、缓存时间和信号清理。
不是所有失败都可归为无害的测试适配：审批与路径守卫断言也在失败清单中，仍需单独处理。
本轮证明这些问题在原始 HEAD 上存在，没有修复它们，也没有宣称全仓测试全绿。

## 本批专项与证据

- 独立提交候选副本：规划 67 项、请求归属 36 项、总结恢复 91 项、频道策略 31 项通过。
  这些测试有重叠，不累加成总数。
- MCP 参数与技能契约 15 项通过；真实 MCP 握手注册 94 个工具，契约所需工具及参数检查通过。
- 最终 Node 离线测试 92 项通过；全部改动 Python 文件 Ruff 通过；新增契约相关 3 个生产文件
  mypy 通过；六条 import-linter 分层契约通过。执行器 JS 未在本轮修改，严格 JS 检查沿用 M1 验证。
- 技能执行契约只在 `channels/minecraft/reply_policy.py` 编辑，技能块由
  `python -m channels.minecraft.scripts.minecraft_contract` 生成并测试比对。已通过本地技能 API 更新运行中的正文，
  保留元数据，安装负载同步并回读确认；本轮没有新增游戏动作。
- M0 固定条件 P50/P95 仍未采样，M2 尚未开始；规程与 M4 前置条件见 DEVELOPMENT_PLAN.md。

证据文件的 SHA-256（完整日志留在本机，不向 Git 提交个人运行配置）：

| 文件 | SHA-256 |
| --- | --- |
| `head-full-v2.log` | `523a9312005325f7a377579e72de3e039d5c5ea21fab7bd8af84ae52ba28cd45` |
| `head-full-v2.xml` | `9d48e3477d5623fd05b05ed926195944de3995a4d30464ab155470bc786e8b13` |
| `current-full.log` | `f032208a600af0ca1a8896cbbe58bd1fe91fa84850ea911afbd9515bab88435d` |
| `current-full.xml` | `e7db395997173d740a8ffb30c47aa8fb3d987ad0d662fd702378c1e158b6d6f2` |
| `comparison.json` | `d68875a26753c92d73b35799d2b014e4e88dfa69ec4903480d43f0ab03c42c54` |
| `repeat-head.xml` | `086d70aca81c5aaf586007b773c69a2aacd316ad79398b577ccee1ddfc3a88b8` |
| `repeat-current.xml` | `cee4ab6a6c19d7c729d6ebdbd00bc048ba68b9469c3abefb09bb6fb986adf9f6` |
| `node-tests.log` | `0ed7a6c91bc030ad1c49d6960ef69a041d909300a1750d8623f9ff72d4f3b8c6` |

## 单频道目录归属验证

- MCP 包迁入 `channels/minecraft/mcp/`；逐文件核对运行代码、技能、清单与依赖锁文件未变。
- 迁移后的全量 Python 测试：5789 通过、21 跳过；Minecraft 与实时语音定向回归：306 通过。
- 新路径下 Node 回归：172 通过；补丁入口在隔离负载上重复执行保持幂等。
- Ruff、mypy（725 个源文件）、八条 import-linter 契约全部通过；文档相对链接有效。
- CI #160 的实时语音用例抢在消息投递前注入回复，已用延迟投递复现；改为等待统一入口
  收到消息后，同一延迟条件通过。语音生产代码未修改。
- 日志位于本地 `workspace/diagnostics/merge-review-20261010/`；本轮未连接真实游戏世界。
