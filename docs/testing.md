# 测试与 CI

测试体系、防膨胀规约与 CI 矩阵的现状说明。写新测试或改 CI 前按需阅读。

## 测试体系

测试面分两类：

- **分层套件** `tests/`：`tests/unit/` 纯 mock/纯函数/tmp_path 快速单测，按被测层分目录
  core/agent/services/web；`tests/integration/` 真实应用组装或需外部凭证（需凭证的用例
  env-gated 自动 skip，如 `LLM_CACHE_E2E=1` 的缓存健康门）
- **模块内套件** `<模块>/tests/`：`entities/<name>/tests`、`channels/<id>/tests`，测试随模块目录走，
  删除模块即整体拔出零残留；跨模块共享件测试留在 `tests/unit/entities/`

目录归属由仓库根 `conftest.py` 自动打 `unit`/`integration` marker（模块内 tests/ 自动 unit）。
根 conftest 全局隔离 ConfigManager（指向 tmp_path），**新测试不得读写真实 `config/`**。

```powershell
uv run pytest                          # 全量
uv run pytest tests/unit               # 分层单测
uv run pytest entities/minimax/tests   # 单模块
uv run pytest -m integration           # 集成
```

加 `-n auto` 并行（已装 pytest-xdist，全量约 64s→26s，CI 已启用；单测调试/`--pdb` 时去掉）。

## 测试防膨胀规约（写新测试前逐条自查）

1. **先查共享层再动手**：think_loop 替身用 `tests/helpers/think_loop_fakes.py`
   （FakeMind/FakePfc/text_result/tool_result/run_think_loop），禁止在新文件复制这些样板；
   Mind 替身的特化行为以子类扩展实现
2. **fixture 分层复用**：`tests/unit/conftest.py` 提供 `store`（MemoryStore），
   `tests/unit/agent/conftest.py` 提供 `sqlite` 基座，`tests/unit/agent/mind/conftest.py` 提供
   `anything`/`deliver_mock`——同名需求直接注入，禁止本地重建同构 fixture
3. **同主题微测试并入既有文件**，不新建文件；实体/频道的测试放模块内 `<模块>/tests/`（随模块整体
   拔出），分层套件内跨目录测别的模块的测试放在被测模块目录下（如 think_loop 集成测试归 mind/）
4. **被新用例取代的旧用例必须删除**；死代码（生产零调用的函数）不保留测试覆盖
5. 合并 = 移动 + 去样板，断言语义不缩水；语义各异的替身/工厂不强行合并（合并产物比各自更复杂时
   不合并）

## CI（.github/workflows/ci.yml）

模块化三 job：

- `changes`（原生 shell 归类：push 对 HEAD^、PR 对目标分支基点对比）
- `lint`（ruff + import-linter + mypy core 三平台必过/全量观察，静态门禁与改动面无关始终全仓）
- `tests`（**模块动态矩阵**：实体/频道改动只跑对应 `模块/tests` 腿并附带跨实体共享套件；`core/`、
  `agent/`、`tests/`、根 `conftest.py`、依赖锁定等横切改动触发 `all` 全量腿；services/web 后端各有
  专属腿；`fail-fast: false` 保留完整失败面）+ `frontend`（lint/build；模块前端 `channels/*/frontend/`、
  实体 `*.tsx` 面板同属前端改动面）
- `docs`（AGENTS.md ≤500 行门禁——防止工作区指令再膨胀，长期指导迁移到 docs/）

文档类提交全跳过，`workflow_dispatch` 手动触发全量；各 job 均有 timeout-minutes 挂起护栏，覆盖率
产物按腿上传。本地一键镜像 `scripts/check.sh`（--fast 仅静态门禁 / --skip-fe / --skip-browser /
--skip-test / 透传 pytest 参数）。

## CI 与测试教训

| 教训 | 说明 |
|------|------|
| 分层契约的版本差异 | 实体在函数内 `from agent.approval.gate import ...` 本地 import-linter 旧版不检（惰性 in-function import 漏网），CI 新版检出违层——**实体访问 agent 一律收编 `_sdk` 惰性桥，且以新版 import-linter 验证** |
| 凭据依赖测试 | 凡触达凭据解析链的测试必须桩 `_resolve_api_key` 层——本机有凭据所以绿、CI 无凭据必 RuntimeError |
| CI 调试 | 仓库公开时 `actions/runs` API + check-runs annotations 无需登录即可读失败用例（ci.yml 的 ::error 注解设计）；容器复现要防代理注入（小写 http_proxy 污染 proxy 相关测试）与 slim 镜像缺 git 的假象 |
