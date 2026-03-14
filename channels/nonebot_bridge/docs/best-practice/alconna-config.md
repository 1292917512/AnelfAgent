# 配置项

`nonebot-plugin-alconna` 提供了以下配置项，可在 NoneBot 的 `.env` 文件或环境变量中设置。

## alconna_auto_send_output

- **类型**：`bool | None`
- **默认值**：`None`

是否全局启用输出信息自动发送，不启用则会在触发特殊内置选项后仍然将解析结果传递至响应器。

## alconna_use_command_start

- **类型**：`bool`
- **默认值**：`False`

是否读取 NoneBot 的配置项 `COMMAND_START` 来作为全局的 Alconna 命令前缀。

## alconna_global_completion

- **类型**：`CompConfig | None`
- **默认值**：`None`

全局的补全会话配置（不代表全局启用补全会话）。`CompConfig` 的定义参考 [补全会话](./alconna-matcher.md#补全会话-compconfig)。

## alconna_use_origin

- **类型**：`bool`
- **默认值**：`False`

是否全局使用原始消息（即未经过 `to_me` 等处理的），该选项会影响到 Alconna 的匹配行为。

## alconna_use_command_sep

- **类型**：`bool`
- **默认值**：`False`

是否读取 NoneBot 的配置项 `COMMAND_SEP` 来作为全局的 Alconna 命令分隔符。

## alconna_global_extensions

- **类型**：`list[str]`
- **默认值**：`[]`

全局加载的扩展，其读取路径以 `.` 分隔，如 `foo.bar.baz:DemoExtension`。

对于内置扩展，路径为 `nonebot_plugin_alconna.builtins.extensions` 下的模块名，如 `ReplyMergeExtension`，可以使用 `@` 来缩写路径，如 `@reply:ReplyMergeExtension`。

## alconna_context_style

- **类型**：`Optional[Literal["bracket", "parentheses"]]`
- **默认值**：`None`

全局命令上下文插值的风格：

| 值 | 说明 |
|---|---|
| `None` | 关闭上下文插值 |
| `"bracket"` | 使用 `{...}` 风格 |
| `"parentheses"` | 使用 `$(...)` 风格 |

## alconna_enable_saa_patch

- **类型**：`bool`
- **默认值**：`False`

是否启用 SAA (Send Anything Anywhere) 补丁。

## alconna_apply_filehost

- **类型**：`bool`
- **默认值**：`False`

是否启用文件托管。启用后可以将本地文件以 URL 形式提供给需要 URL 的适配器。

## alconna_apply_fetch_targets

- **类型**：`bool`
- **默认值**：`False`

是否启动时拉取一次[发送对象](./alconna-uniseg-utils.md#发送对象)列表。启用后，主动发送消息时可以自动选择 Bot 对象。

## alconna_builtin_plugins

- **类型**：`set[str]`
- **默认值**：`set()`

需要加载的[内置插件](./alconna-builtins.md)列表。可选值包括：`"echo"`、`"help"`、`"lang"`、`"switch"`、`"with"`。

## alconna_conflict_resolver

- **类型**：`Literal["raise", "default", "ignore", "replace"]`
- **默认值**：`"default"`

命令冲突解决策略，决定当不同插件之间或者同一插件之间存在两个以上相同的命令时的处理方式：

| 策略 | 说明 |
|---|---|
| `"replace"` | 替换较旧的命令 |
| `"ignore"` | 忽略较新的命令 |
| `"raise"` | 抛出异常 |
| `"default"` | 默认处理方式，保留两个命令 |

## alconna_response_self

- **类型**：`bool`
- **默认值**：`False`

是否让响应器处理由 bot 自身发送的消息。

## 配置汇总表

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `alconna_auto_send_output` | `bool \| None` | `None` | 自动发送输出信息 |
| `alconna_use_command_start` | `bool` | `False` | 使用 COMMAND_START 作为前缀 |
| `alconna_global_completion` | `CompConfig \| None` | `None` | 全局补全会话配置 |
| `alconna_use_origin` | `bool` | `False` | 使用原始消息 |
| `alconna_use_command_sep` | `bool` | `False` | 使用 COMMAND_SEP 作为分隔符 |
| `alconna_global_extensions` | `list[str]` | `[]` | 全局扩展列表 |
| `alconna_context_style` | `str \| None` | `None` | 上下文插值风格 |
| `alconna_enable_saa_patch` | `bool` | `False` | 启用 SAA 补丁 |
| `alconna_apply_filehost` | `bool` | `False` | 启用文件托管 |
| `alconna_apply_fetch_targets` | `bool` | `False` | 启动时拉取发送对象 |
| `alconna_builtin_plugins` | `set[str]` | `set()` | 内置插件列表 |
| `alconna_conflict_resolver` | `str` | `"default"` | 命令冲突解决策略 |
| `alconna_response_self` | `bool` | `False` | 响应自身消息 |
