# 错误跟踪

[`nonebot-plugin-sentry`](https://github.com/cscs181/nonebot-plugin-sentry) 提供 [Sentry](https://sentry.io/) 集成，可以自动捕获并上报 NoneBot 运行时的异常和错误信息，便于线上监控和问题排查。

## 安装

```bash
# nb-cli
nb plugin install nonebot-plugin-sentry

# pip
pip install nonebot-plugin-sentry

# poetry
poetry add nonebot-plugin-sentry

# pdm
pdm add nonebot-plugin-sentry
```

## 快速开始

1. 在 [Sentry](https://sentry.io/) 上创建一个 Python 项目并获取 DSN。
2. 在 `.env` 文件中配置 DSN：

```dotenv
SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0
```

3. 加载插件：

```python
# 在 pyproject.toml 或 bot.py 中加载
nonebot.load_plugin("nonebot_plugin_sentry")
```

插件加载后会自动初始化 Sentry SDK，所有未捕获的异常都会被上报到 Sentry 平台。

## 配置项

在 `.env` 文件中设置，所有配置项如下：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `sentry_dsn` | `str` | `""` | Sentry DSN 地址（**必填**） |
| `sentry_debug` | `bool` | `False` | 是否开启 Sentry SDK 调试模式 |
| `sentry_release` | `str\|None` | `None` | 发布版本标识，用于关联 Source Map 和 Commit |
| `sentry_environment` | `str\|None` | `None` | 部署环境标识（如 `production`、`staging`、`development`） |
| `sentry_server_name` | `str\|None` | `None` | 服务器名称标识 |
| `sentry_sample_rate` | `float` | `1.0` | 错误事件采样率（0.0 ~ 1.0），`1.0` 表示上报所有错误 |
| `sentry_max_breadcrumbs` | `int` | `100` | 最大面包屑（上下文日志）数量 |
| `sentry_attach_stacktrace` | `bool` | `False` | 是否在非异常事件中也附带调用栈 |
| `sentry_send_default_pii` | `bool` | `False` | 是否发送用户的个人身份信息（PII） |
| `sentry_in_app_include` | `list[str]` | `[]` | 额外标记为 in-app 的模块路径前缀 |
| `sentry_in_app_exclude` | `list[str]` | `[]` | 排除标记为 in-app 的模块路径前缀 |
| `sentry_request_bodies` | `str` | `"medium"` | 请求体捕获策略：`"never"`、`"small"`、`"medium"`、`"always"` |
| `sentry_with_locals` | `bool` | `True` | 是否在异常帧中包含局部变量 |
| `sentry_ca_certs` | `str\|None` | `None` | 自定义 CA 证书路径 |
| `sentry_before_send` | `Callable\|None` | `None` | 发送前处理函数，可用于过滤或修改事件 |
| `sentry_before_breadcrumb` | `Callable\|None` | `None` | 面包屑添加前处理函数 |
| `sentry_transport` | `type\|None` | `None` | 自定义传输类 |
| `sentry_http_proxy` | `str\|None` | `None` | HTTP 代理地址 |
| `sentry_https_proxy` | `str\|None` | `None` | HTTPS 代理地址 |
| `sentry_shutdown_timeout` | `int` | `2` | SDK 关闭时等待发送完成的超时秒数 |
| `sentry_integrations` | `list\|None` | `None` | 自定义集成列表 |
| `sentry_default_integrations` | `bool` | `True` | 是否加载默认集成 |
| `sentry_dist` | `str\|None` | `None` | 分发标识，配合 release 使用 |
| `sentry_traces_sample_rate` | `float\|None` | `None` | 性能追踪采样率（0.0 ~ 1.0） |
| `sentry_traces_sampler` | `Callable\|None` | `None` | 自定义性能追踪采样函数 |
| `sentry_profiles_sample_rate` | `float\|None` | `None` | 性能分析采样率 |
| `sentry_propagate_traces` | `bool` | `True` | 是否传播追踪上下文到下游服务 |
| `sentry_enable_tracing` | `bool\|None` | `None` | 是否启用性能追踪 |

## 配置示例

### 基础配置

```dotenv
# .env
SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=mybot@1.0.0
```

### 开发环境配置

```dotenv
# .env.dev
SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0
SENTRY_ENVIRONMENT=development
SENTRY_DEBUG=true
SENTRY_SAMPLE_RATE=1.0
SENTRY_WITH_LOCALS=true
SENTRY_ATTACH_STACKTRACE=true
```

### 生产环境配置

```dotenv
# .env.prod
SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=mybot@1.2.0
SENTRY_SERVER_NAME=bot-server-01
SENTRY_SAMPLE_RATE=0.5
SENTRY_SEND_DEFAULT_PII=false
SENTRY_WITH_LOCALS=false
SENTRY_TRACES_SAMPLE_RATE=0.1
SENTRY_SHUTDOWN_TIMEOUT=5
```

### 代理配置

```dotenv
SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0
SENTRY_HTTP_PROXY=http://proxy.example.com:8080
SENTRY_HTTPS_PROXY=http://proxy.example.com:8080
```

## 过滤事件

通过 `sentry_before_send` 可以在代码中配置事件过滤：

```python
import sentry_sdk


def before_send(event, hint):
    # 过滤特定异常
    if "exc_info" in hint:
        exc_type, exc_value, tb = hint["exc_info"]
        if isinstance(exc_value, KeyboardInterrupt):
            return None  # 丢弃事件

    # 移除敏感数据
    if "request" in event:
        event["request"]["headers"] = {}

    return event


sentry_sdk.init(before_send=before_send)
```

## 手动捕获

除了自动捕获异常外，也可以手动上报：

```python
import sentry_sdk


# 手动捕获异常
try:
    risky_operation()
except Exception as e:
    sentry_sdk.capture_exception(e)

# 手动发送消息
sentry_sdk.capture_message("某个重要操作完成")

# 设置上下文信息
sentry_sdk.set_user({"id": "12345", "username": "test_user"})
sentry_sdk.set_tag("plugin", "my_plugin")
sentry_sdk.set_context("bot_info", {"adapter": "onebot_v11", "groups": 42})
```

## 性能追踪

启用后可以监控各类操作的耗时：

```dotenv
SENTRY_ENABLE_TRACING=true
SENTRY_TRACES_SAMPLE_RATE=0.2
```

```python
import sentry_sdk


with sentry_sdk.start_transaction(op="task", name="process_message"):
    with sentry_sdk.start_span(op="db.query", description="查询用户数据"):
        user_data = query_user(user_id)

    with sentry_sdk.start_span(op="ai.inference", description="AI 推理"):
        result = await ai_model.generate(user_data)
```
