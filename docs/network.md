# 网络路由与代理 SDK

`core/network` 负责代理地址、站点规则、请求级路由与 HTTP 连接池。实体从
`entities._sdk` 导入，频道从 `agent.channel` 导入，内核直接依赖 `core.network`。
业务模块不写进程 `HTTP_PROXY`，不维护独立代理地址。

## 配置与规则

配置中心的 `network/proxy` 与 AI 配置工具共用同一校验、读写入口：

- `proxy_enabled`：全局开关。
- `http_proxy`：全局地址，支持 `http://`、`https://`、`socks5://`、`socks5h://`，必须包含端口。
- `https_proxy`：可选的 HTTPS 专用地址，留空复用 `http_proxy`。
- `proxy_default_mode`：`fallback`（默认，直连失败后代理）、`direct` 或 `proxy`。
- `proxy_rules`：一行一条，按顺序首条命中；空行和 `#` 开头的注释忽略。
- `proxy_connect_timeout`：fallback 直连建立连接的等待上限，不改变读取和长轮询超时。

Jev 推理请求与连通检查均使用全局规则。无推理 HEAD 检查按路由候选数量分配总等待预算，避免直连超时在代理回退前耗尽整个检查；正常服务不周期探测，检测不携带模型密钥。可用 `DOMAIN,api.typesafe.ai,PROXY` 让官方服务直接采用代理，本地模型仍按内网直连规则处理。

```text
DOMAIN-SUFFIX,telegram.org,PROXY
DOMAIN,api.example.com,PROXY
DOMAIN-SUFFIX,internal.example.com,DIRECT
IP-CIDR,10.0.0.0/8,DIRECT
```

规则类型是 `DOMAIN`、`DOMAIN-SUFFIX`、`IP-CIDR`（含 IPv6），动作是 `DIRECT`、
`PROXY`、`FALLBACK`。后缀匹配包含域名本身和子域名，不匹配伪造的相似后缀。
IP 规则只匹配 URL 中的 IP，不为路由额外查询 DNS。未命中规则的回环、私网 IP、
单标签主机名和 `.local` 域名默认直连。规则设计参考
[Mihomo 的顺序匹配与域名规则](https://wiki.metacubex.one/config/rules/)。

组件或单次调用可以选择 `proxy_mode`：

| 模式 | 行为 |
| --- | --- |
| `system` | 全局开关 → 站点规则 → 内网直连 → 默认策略 |
| `direct` | 明确直连，不受站点规则影响 |
| `proxy` | 使用全局地址；未启用或未配置时明确报错，不偷偷改走直连 |

配置下一次请求生效。供应商和 Telegram 只保存策略，地址只在全局管理；原有
`retrieval_proxy`、`plugin_proxy`、供应商 `proxy_url`、Telegram `proxy_host/proxy_port`
不再读取，需要将所需地址集中配置到 `network/proxy`。检索、供应商、频道和插件页面
提供全局配置入口，编辑模型时在新标签页打开配置，保留当前草稿。

## 按需连接检测

配置中心 `network/proxy` 提供「代理与连接检测」；AI 使用 `check_network_proxy`。
两者经 `core.network.diagnose_network(NetworkProbeRequest(...))` 执行相同检测，
读取已经保存的生效配置，不修改开关、地址或规则。默认比较 system/direct/proxy 三条路径，
每条采样三次；可以指定检测 URL，AI 也可只选一条路径。

检测从 Anelf 服务端发起 HEAD 请求，独立连接、不携带目标认证、不跟随跳转、不下载正文。
目标 URL 不接受凭据或查询参数；需要检测某个供应商时使用不含密钥的地址。
返回路径、规则序号/来源、回退次数、HTTP 状态、每次耗时、中位数与极差。
未配置代理明确显示不可用，不以直连成功替代代理检测；HTTP 403/405/5xx 单独标记，
不能仅凭该状态判断是目标限制还是代理故障。检测期间配置变化会提示重新采样。

采样次数 1–5、每次总截止 1–10 秒，取消会关闭连接。Web API 为
`POST /api/network/diagnose`，由现有 Web 认证边界保护；结果不持久化、不包含代理凭据。
这是当前目标上的有限连接检查，不是下载、流式长连接或长期可用性的保证。

## HTTP 客户端

```python
from entities._sdk import async_network_client, network_client

async with async_network_client(proxy_mode="system", timeout=30) as client:
    response = await client.get("https://example.com/data")
    response.raise_for_status()
    route = response.extensions["network_route"]

with network_client(proxy_mode="direct", timeout=10) as client:
    response = client.get("http://localhost:8080/status")
```

客户端归调用方管理，异步客户端不能跨事件循环复用。长期客户端复用连接池；规则变更
逐请求读取，地址变更不关闭正在读取的旧响应，旧空闲池在后续请求时回收。模型的
Anthropic handler 与 OpenAI 客户端共用此路由，保留缓存连接亲和，不用环境变量租约。
LiteLLM 的 SDK 通道通过 `aclient_session` 注入无连接资源的转接客户端，使用 ContextVar
选择当前模型拥有的连接池，避免 LiteLLM 缓存的 SDK 客户端串用其他模型的网络策略；
模型上下文之外直用 litellm 的组件（如 cognee 记忆投影）回退到全局代理策略的共享
传输，共享传输按事件循环缓存。原生 HTTP 通道使用请求级 handler。流式读取只在获取
事件时绑定上下文，不泄漏给消费方。

需要自定义 HTTPX 客户端时使用 `NetworkTransport` / `AsyncNetworkTransport`，同时设置
`trust_env=False`，并将连接池限制等传输参数传给 transport。MCP 的 HTTPX2 接口通过
`core.network.httpx2.AsyncNetworkTransport2` 转接同一实现。

其他 SDK 可用 `resolve_network_route(url, proxy_mode)` 获取有序候选地址：`None` 表示直连。
`get_proxy_url(url, proxy_mode)` 只返回首选路由地址，**不会替 SDK 实现失败回退**；
接入方应按协议判断重试是否安全。不要把 `fallback` 的第二项直接当作默认代理。

## 回退与安全边界

SSH 使用实体配置 `ssh_proxy_mode`，初始为 `direct`。Web 配置中心与 AI 的统一配置工具均可修改；
`ssh_connect/ssh_exec/ssh_upload/ssh_download` 可通过同名参数覆盖单次调用，Web 命令执行面板也可选择。
SSH 的 `system` 模式按目标主机匹配全局规则；`proxy` 使用全局 `http_proxy` 地址。SSH 不继承机器
`~/.ssh/config` 的隐式代理设置，显式连接档案是主机、身份及密钥的来源。

非 HTTP 协议通过 `resolve_socket_route(host, port, proxy_mode)` 与 `open_proxy_socket(...)` 接入。
TCP 隧道支持 HTTP CONNECT、SOCKS5/SOCKS5H；TLS 封装的 HTTPS 代理不支持，配置此类地址会明确报错。
SSH 不同路由分别复用连接，单次覆盖不会让后续直连命令借用代理连接，也不会中断其他路线的在途命令。
断开主机时释放全部路线。fallback 仅在建连失败时换路，认证失败及已启动命令的失败不换路重放。

- 连接建立失败可以切换代理；无请求体的 GET/HEAD/OPTIONS 在收到响应头前发生读取失败也可回退。
- POST 等写请求发送后的超时、业务错误、HTTP 401/403/429、证书验证错误不自动换路重放。
- 开始消费响应正文后不重放流。失败原因交给调用方处理，不拼接两次下载或生成结果。
- 代理失败不会再悄悄回退直连。重定向的下一跳重新匹配规则。
- 应用不读取进程代理环境来决定路由；自定义 CA 环境配置仍由 HTTPX TLS 层处理。
- 检索保留 SSRF 校验，每跳都验证目标，包括 robots.txt；本地 DNS 无法验证目标时明确失败，
  不因代理能解析域名而绕过防护。可修复 DNS 或选择远程网页读取提供者。
- `response.extensions["network_route"]` 只有路径、规则来源和尝试次数，不包含代理凭据。
- Git 的代理仅注入当前子进程配置，HTTP 拉取连接失败才尝试代理，SSH 不作 HTTP 代理转换。

传输扩展点与连接失败语义参考 [HTTPX transports](https://www.python-httpx.org/advanced/transports/)。
