# 飞书任务委派 Agent 执行 — AnelfAgent 接入手册

把 AnelfAgent 注册为飞书「任务委派 Agent 执行」的执行智能体：在飞书任务里
委派任务，本机 AnelfAgent 守护进程执行，进度与结果回写到任务评论/步骤/状态。

接入走**官方启动器**（`@larktask/aamp-feishu-task-agent`）：飞书侧一切
（Bot 应用创建授权、任务事件订阅、任务回写）由官方桥完成；AnelfAgent 侧
通过自带的标准 **ACP**（Agent Client Protocol）适配器接入。

## 架构链路

```
飞书任务（委派） → 官方 feishu-bridge（用户自建 Bot 应用）
                → AAMP 中继（meshmail.ai）
                → 官方 acp-bridge → acpx CLI
                → scripts/anelf-acp acp serve（ACP，本仓库）
                → 守护进程 http_api 频道（127.0.0.1，SSE 流式）
                → Mind 执行 → 流式回写任务
```

每个飞书任务对应一个 ACP 会话（官方桥按 `feishu-task:{guid}` 命名会话），
会话历史持久化在守护进程侧（scope `user_http_api:acp_{sessionId}`）；
跨任务的语义记忆/画像由守护进程记忆系统全局共享。

## 前置条件

1. **Node.js ≥ 22**（acpx 要求；`node --version` 确认）
2. **AnelfAgent 守护进程运行中**，且 `http_api` 频道已启用
   （面板频道页确认；配置文件 `channels/http_api/channel_config.json`）
3. 守护进程加载了本仓库当前版本代码（SSE 流端点为本次新增，旧进程需重启）
4. 项目 venv 已同步（`.venv/bin/python` 存在；`uv sync`）

## 就绪自检

```bash
# 三项检查全 info 即就绪（守护进程可达性 / 配置解析）
scripts/anelf-acp doctor --json
```

## 绑定（一次性）

官方一键脚本 + 指定智能体为 traecli 形态、可执行文件指到我们的 ACP 适配器：

```bash
export AAMP_TRAECODE_CLI_BIN="$PWD/scripts/anelf-acp"
curl -fsSL https://registry.npmjs.org/@larktask/aamp-feishu-task-agent/-/aamp-feishu-task-agent-0.1.1-dev.12.tgz \
  | tar -xZO package/bootstrap/aamp-feishu-task-agent-bootstrap.sh \
  | bash -s -- --agent traecli
```

交互说明：

- 脚本会先做 **Bot 应用授权**（lark-cli 流程）：按终端提示在浏览器完成
  飞书登录授权，应用由脚本自动创建（名字/头像自定，飞书侧任务智能体的
  展示身份就是这个 Bot）
- 随后列出检测到的智能体（我们的适配器以 TraeCode CLI 形态出现），
  `--agent traecli` 已固定选择，直接确认
- 就绪探测 = `anelf-acp acp serve --help` 帮助文本 + `doctor --json`
  健康 JSON + 一次 ACP 会话配对，全部由适配器原生满足
- macOS 绑定完成后自动转入后台服务（launchd），关终端不影响

已安装过启动器时追加绑定：

```bash
AAMP_TRAECODE_CLI_BIN="$PWD/scripts/anelf-acp" feishu-task-agent add --agent traecli
```

## 验证

```bash
feishu-task-agent status   # 桥运行状态
feishu-task-agent list     # 已保存的 智能体↔Bot 绑定对
scripts/anelf-acp doctor --json
```

然后在飞书任务侧边栏把一个任务委派给该 Bot，观察任务评论区的执行进度。

## 排障

| 现象 | 入口 |
|------|------|
| 绑定/启动失败 | `feishu-task-agent logs`；详细运行日志 `~/.aamp/logs/runs/` |
| 任务执行失败 | `aamp-logs collect --task-guid <飞书任务guid>`；守护进程侧查 AnelfAgent 日志 |
| doctor 报守护进程不可达 | 确认守护进程运行、http_api 频道启用、端口未被占用 |
| 绑定后任务无响应 | `acpx` 会话残留可 `feishu-task-agent restart` 重建 |

## 维护边界

- **仓库目录搬迁**会使绑定里的绝对路径失效：重新执行一次 `add` 即可
- **守护进程重启**不影响绑定（ACP 适配器按需被 acpx 重新拉起，
  会话历史在守护进程侧无损续接）
- 中继依赖 meshmail.ai（AAMP 服务），其可达性决定整体可用性
- 危险工具审批沿用守护进程现有审批中心（任务可能因等待人工而变慢）
