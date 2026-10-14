# 本地检索服务

Anelf 内置适配器通过能力接口调用独立的 Crawl4AI 与 SearXNG 服务。浏览器、搜索引擎及其依赖不进入
Anelf 的 Python 环境。两个服务可以分别升级、停用，已有商业提供者仍可按能力选择。

## 启动与接入

需要 Docker Engine / Desktop（Linux 容器）和 Compose v2。默认随 Anelf 后台启动，首次启动下载
固定版本镜像并初始化配置，不阻塞频道和 Web；Docker 缺失或启动失败会进入 AI 动态上下文，
可用 `retrieval_services(action="status")` 获取主机环境、安装指引和下一步操作。
AI 可使用现有系统工具按官方指引装配 Docker；系统权限、虚拟化或重启要求仍由宿主环境决定。
本集成不在启动时自动执行提权安装程序。以下命令也可在仓库根目录执行：

```sh
uv run python deploy/retrieval/manage.py init
uv run python deploy/retrieval/manage.py up
uv run python deploy/retrieval/manage.py status
```

初始化只创建缺失文件，不覆盖已有配置。随机 Crawl4AI 令牌保存在本目录 `.env`；SearXNG 配置保存在
`runtime/searxng/settings.yml`，已启用 JSON 搜索。两者均被 Git 忽略。默认监听宿主回环地址，
不向局域网或公网发布。配置与命名数据卷独立于容器，`stop` 不删除数据。

健康启动后自动补齐尚未配置的服务地址及令牌，不覆盖现有远端配置。也可在
「检索 → 本地服务」点击“连接本地服务”，明确将两项服务改为当前主机；不更改能力的选择。
在 Anelf「检索 → 能力提供者 → 服务配置」编辑：

| 提供者 | 配置 | 默认本机部署值 |
| --- | --- | --- |
| SearXNG | `retrieval_searxng_base_url` | `http://127.0.0.1:18080` |
| Crawl4AI | `retrieval_crawl4ai_base_url` | `http://127.0.0.1:11235` |
| Crawl4AI | `retrieval_crawl4ai_api_key` | `.env` 中 `CRAWL4AI_API_TOKEN` 的值 |

配置字段由模块注册，Web 与 AI 的统一配置工具共用同一入口。配置后使用页面上的能力测试确认
真实搜索与抓取成功，再选择 search=SearXNG、reader=Crawl4AI。未配置地址的提供者不参与自动选择。
普通 reader 的 auto 仍优先 builtin 轻量读取，避免每个静态页面都启动浏览器渲染；search 的 auto
优先已配置的 SearXNG。AI 也可通过 `retrieval_providers` 查看配置组、启停及切换能力。

「检索 → 本地服务」与 `retrieval_services` 共用部署接口，可启动、停止、更新、回退、查看状态。
写操作返回操作 ID，需查询 `status` 确认终态；页面仅在操作中轮询轻量状态，容器检测按需执行。
`retrieval/deployment` 配置组控制默认启动与异常上下文提示，Web 和 AI 均可修改。
停止保留配置和数据；若希望以后不自动启动，同时关闭 `retrieval_local_autostart`。
服务高级设置（如 SearXNG 的完整引擎定义）仍按上游格式编辑 `runtime/searxng/settings.yml`，
端口在 `.env` 修改后执行 up；Web 配置中心负责 Anelf 接入参数，不提供任意 Compose 文件执行入口。

Anelf 若也在容器内，`127.0.0.1` 指它自己的容器；应将 Anelf 接入同一 Docker 网络后使用服务名
`http://crawl4ai:11235`、`http://searxng:8080`，或显式配置可达的宿主地址。不要直接将无认证的
SearXNG 端口公开。

## 工具与接口

- `web_search(query=..., provider="searxng")`：结果保持现有引用格式，部分引擎异常另行标记；
  没有结果且引擎报错时返回失败，不误报为无相关内容。
- `web_fetch(url=..., provider="crawl4ai")`：浏览器渲染后提取 Markdown / text / raw，沿用正文分块。
- `web_crawl(url=..., css_selector="article", wait_for=".ready", scan_full_page=true)`：动态页面抓取，
  最多滚动 10 步，正文按字符预算返回，额外链接最多 30 条。仅配置可用的抓取提供者时向 AI 提供。
  超时为 1–45 秒，服务传输另留 5 秒余量。分块续读会重新抓取，动态页面内容可能变化。

动态抓取通过 `CrawlCap` 接口注册，与 SearchCap / ReaderCap / RepoCap 平行。实体从 `_sdk`
导入接口与注册函数，不跨层导入 agent。前端能力矩阵和连通测试随接口注册显示。

使用官方 `/crawl` JSON 协议，不启用额外 LLM 提取，不传任意服务端配置。Crawl4AI 0.9.4 的 HTTP
服务禁止请求级任意 JavaScript、持久会话 ID、浏览器代理等字段；因此本接口负责渲染读取，
不提供点击、表单提交或登录会话自动化。浏览器渲染也不能保证通过网站风控。

## 网络边界

到服务的连接与服务到网站的出站连接是两段网络：

- SearXNG 服务连接固定直连；搜索引擎代理由 `runtime/searxng/settings.yml` 的官方 `outgoing`
  配置管理。宿主 `127.0.0.1` 代理地址在容器中不可直接使用。
- Crawl4AI 的工具 `proxy_mode` 沿用核心网络 SDK，但只选择 **Anelf → Crawl4AI 服务** 的路线。
  返回的 `service_route` 也只描述这一段，不代表浏览器的出站代理。
- Crawl4AI 0.9.4 服务会覆盖调用方浏览器代理以执行自己的出站防护，不能将 Anelf 代理地址作为
  browser_config 参数强塞进去。需代理的抓取应按服务支持的部署网络方式配置。
- 页面目标沿用 Anelf SSRF 预检查；浏览器子请求与重定向由固定版本官方服务的出站防护处理。
  配置的本地服务地址是管理面地址，不作为待抓取网页，不需要关闭 SSRF 来接入。

## 更新与回退

默认镜像固定版本，重启不会偷偷跟随 `latest`。查看官方发行记录，明确选择版本或 digest：

```sh
uv run python deploy/retrieval/manage.py update --crawl4ai-image unclecode/crawl4ai:<目标版本>
uv run python deploy/retrieval/manage.py update --searxng-image docker.io/searxng/searxng:<目标版本>
uv run python deploy/retrieval/manage.py rollback
uv run python deploy/retrieval/manage.py stop
```

Web、AI 和 CLI 使用同一个管理器和跨进程操作锁。仅接受官方镜像的明确版本或 sha256，拒绝 latest。
先拉取选中的镜像，再重建对应服务并等待健康检查，成功后保存 `.env.previous`。健康启动失败会恢复旧镜像
配置并尝试重启旧服务；旧服务重启失败会明确退出，不报告成功。回退保留配置与数据卷，
不自动回退第三方数据格式；大版本升级前应备份配置与卷。精确复现建议使用镜像 digest。
服务健康通过只代表服务已启动；更新后还需在 Anelf 检索页分别执行搜索、读取、动态抓取测试，
验证引擎可达性、输出格式与站点兼容性。

AI 示例：`retrieval_services(action="update", crawl4ai_image="unclecode/crawl4ai:<版本>")`。
CLI、Web 与 AI 都不回显服务令牌。服务配置、操作日志与回退快照被 Git 忽略。关闭 Anelf 会终止
在途 Docker 命令并恢复版本配置，但已创建的容器仍可能需要下一次 up 收敛；状态明确标记中断。
版本回退只替换镜像，不恢复过期凭据或端口，也不回滚第三方服务的数据格式。

## 上游与维护

- [Crawl4AI](https://github.com/unclecode/crawl4ai)，Apache-2.0；
  [固定版本服务 API](https://github.com/unclecode/crawl4ai/blob/v0.9.4/deploy/docker/api.py)。
- [SearXNG](https://github.com/searxng/searxng)，AGPL-3.0；
  [搜索 API](https://docs.searxng.org/dev/search_api.html)、
  [容器安装](https://docs.searxng.org/admin/installation-docker.html)。

集成不复制上游源码，使用官方镜像；分发或修改服务时保留相应项目的许可证与声明。
