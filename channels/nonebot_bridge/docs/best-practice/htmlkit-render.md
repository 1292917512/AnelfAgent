# 轻量化 HTML 绘图

[`nonebot-plugin-htmlkit`](https://github.com/nonebot/plugin-htmlkit) 是一个基于 [litehtml](http://www.nicnet.net/litehtml/) 的轻量化 HTML 渲染插件，可以将 HTML、纯文本、Markdown 模板渲染为图片，无需浏览器环境。

## 安装

```bash
# nb-cli
nb plugin install nonebot-plugin-htmlkit

# pip
pip install nonebot-plugin-htmlkit

# poetry
poetry add nonebot-plugin-htmlkit

# pdm
pdm add nonebot-plugin-htmlkit
```

## 快速开始

```python
from nonebot import require

require("nonebot_plugin_htmlkit")

from nonebot_plugin_htmlkit import html_to_pic, md_to_pic, text_to_pic
```

## API 参考

### html_to_pic

将 HTML 字符串渲染为图片。

```python
async def html_to_pic(
    html: str,
    *,
    width: int = 600,
    css: str = "",
    img_fetch_fn: Callable[[str], Awaitable[bytes]] | None = None,
    css_fetch_fn: Callable[[str], Awaitable[str]] | None = None,
) -> bytes:
    ...
```

**参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `html` | `str` | 必填 | HTML 内容字符串 |
| `width` | `int` | `600` | 渲染视口宽度（像素） |
| `css` | `str` | `""` | 额外的 CSS 样式 |
| `img_fetch_fn` | `Callable\|None` | `None` | 自定义图片资源加载函数 |
| `css_fetch_fn` | `Callable\|None` | `None` | 自定义 CSS 资源加载函数 |

**返回**：`bytes` — PNG 图片的二进制数据。

**示例**：

```python
from nonebot import on_command, require

require("nonebot_plugin_htmlkit")

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot_plugin_htmlkit import html_to_pic

render = on_command("render")


@render.handle()
async def handle_render():
    html = """
    <div style="padding: 20px; background: linear-gradient(135deg, #667eea, #764ba2); border-radius: 12px;">
        <h1 style="color: white; margin: 0;">Hello World</h1>
        <p style="color: rgba(255,255,255,0.8);">这是一条由 HTML 渲染的图片消息</p>
        <ul style="color: white;">
            <li>支持丰富的 CSS 样式</li>
            <li>无需浏览器环境</li>
            <li>轻量高效</li>
        </ul>
    </div>
    """
    pic = await html_to_pic(html, width=400)
    await render.finish(MessageSegment.image(pic))
```

带自定义 CSS：

```python
html = "<div class='card'><h2>用户信息</h2><p>ID: 12345</p></div>"
css = """
.card {
    padding: 16px;
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    font-family: "Microsoft YaHei", sans-serif;
}
h2 { color: #333; margin-top: 0; }
p { color: #666; }
"""
pic = await html_to_pic(html, width=300, css=css)
```

### text_to_pic

将纯文本渲染为图片。

```python
async def text_to_pic(
    text: str,
    *,
    width: int = 600,
    css: str = "",
) -> bytes:
    ...
```

**参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `text` | `str` | 必填 | 纯文本内容 |
| `width` | `int` | `600` | 渲染视口宽度（像素） |
| `css` | `str` | `""` | 额外的 CSS 样式 |

**返回**：`bytes` — PNG 图片的二进制数据。

**示例**：

```python
from nonebot_plugin_htmlkit import text_to_pic

text = """
系统状态报告
================
CPU 使用率: 45%
内存使用率: 62%
磁盘使用率: 78%
运行时间: 72小时

活跃 Bot: 3
已处理消息: 12,456
"""
pic = await text_to_pic(text, width=400)
```

自定义文本样式：

```python
css = """
body {
    font-family: "Courier New", monospace;
    font-size: 14px;
    color: #00ff00;
    background: #1a1a2e;
    padding: 16px;
}
"""
pic = await text_to_pic(text, width=500, css=css)
```

### md_to_pic

将 Markdown 文本渲染为图片。

```python
async def md_to_pic(
    md: str,
    *,
    width: int = 600,
    css: str = "",
) -> bytes:
    ...
```

**参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `md` | `str` | 必填 | Markdown 文本内容 |
| `width` | `int` | `600` | 渲染视口宽度（像素） |
| `css` | `str` | `""` | 额外的 CSS 样式 |

**返回**：`bytes` — PNG 图片的二进制数据。

**示例**：

```python
from nonebot_plugin_htmlkit import md_to_pic

md = """
# 帮助文档

## 可用命令

| 命令 | 说明 | 示例 |
|------|------|------|
| /help | 显示帮助 | /help |
| /天气 | 查询天气 | /天气 北京 |
| /签到 | 每日签到 | /签到 |

## 注意事项

- 命令前缀为 `/`
- **加粗文本** 和 *斜体文本*
- `代码文本`

```python
print("支持代码块高亮")
```
"""
pic = await md_to_pic(md, width=500)
```

### template_to_pic

使用 Jinja2 模板引擎渲染 HTML 模板为图片。

```python
async def template_to_pic(
    template_path: str,
    templates: dict,
    *,
    pages: dict | None = None,
    width: int = 600,
    css: str = "",
    img_fetch_fn: Callable[[str], Awaitable[bytes]] | None = None,
    css_fetch_fn: Callable[[str], Awaitable[str]] | None = None,
) -> bytes:
    ...
```

**参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `template_path` | `str` | 必填 | 模板文件所在目录路径 |
| `templates` | `dict` | 必填 | 模板变量字典，必须包含 `"html"` 键指定模板文件名 |
| `pages` | `dict\|None` | `None` | 分页配置 |
| `width` | `int` | `600` | 渲染视口宽度（像素） |
| `css` | `str` | `""` | 额外的 CSS 样式 |
| `img_fetch_fn` | `Callable\|None` | `None` | 自定义图片资源加载函数 |
| `css_fetch_fn` | `Callable\|None` | `None` | 自定义 CSS 资源加载函数 |

**返回**：`bytes` — PNG 图片的二进制数据。

**示例**：

模板文件 `templates/user_card.html`：

```html
<!DOCTYPE html>
<html>
<head>
    <style>
        .card {
            width: 100%;
            padding: 20px;
            background: white;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            font-family: "Microsoft YaHei", sans-serif;
        }
        .avatar { width: 64px; height: 64px; border-radius: 50%; }
        .name { font-size: 20px; font-weight: bold; color: #333; }
        .info { color: #666; margin-top: 8px; }
        .stats { display: flex; gap: 20px; margin-top: 16px; }
        .stat-item { text-align: center; }
        .stat-value { font-size: 24px; font-weight: bold; color: #5b7dff; }
        .stat-label { font-size: 12px; color: #999; }
    </style>
</head>
<body>
    <div class="card">
        <div class="name">{{ username }}</div>
        <div class="info">ID: {{ user_id }} | 等级: Lv.{{ level }}</div>
        <div class="stats">
            <div class="stat-item">
                <div class="stat-value">{{ points }}</div>
                <div class="stat-label">积分</div>
            </div>
            <div class="stat-item">
                <div class="stat-value">{{ sign_days }}</div>
                <div class="stat-label">签到天数</div>
            </div>
            <div class="stat-item">
                <div class="stat-value">{{ msg_count }}</div>
                <div class="stat-label">发言数</div>
            </div>
        </div>
    </div>
</body>
</html>
```

渲染代码：

```python
from pathlib import Path

from nonebot_plugin_htmlkit import template_to_pic

template_dir = str(Path(__file__).parent / "templates")

pic = await template_to_pic(
    template_path=template_dir,
    templates={
        "html": "user_card.html",
        "username": "Alice",
        "user_id": "10001",
        "level": 15,
        "points": 2580,
        "sign_days": 42,
        "msg_count": 1024,
    },
    width=400,
)
```

## 自定义资源加载

### img_fetch_fn

当 HTML 中包含 `<img>` 标签时，默认会通过 HTTP 下载图片。可以自定义图片加载函数：

```python
import httpx


async def custom_img_fetch(url: str) -> bytes:
    """自定义图片加载（支持代理、缓存等）"""
    async with httpx.AsyncClient(proxy="http://proxy:8080") as client:
        resp = await client.get(url, timeout=10)
        return resp.content


pic = await html_to_pic(
    '<img src="https://example.com/avatar.png">',
    img_fetch_fn=custom_img_fetch,
)
```

从本地文件加载：

```python
from pathlib import Path


async def local_img_fetch(url: str) -> bytes:
    """从本地文件系统加载图片"""
    if url.startswith("file://"):
        path = Path(url.replace("file://", ""))
        return path.read_bytes()
    # 回退到 HTTP 下载
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.content


pic = await html_to_pic(
    '<img src="file:///data/images/logo.png">',
    img_fetch_fn=local_img_fetch,
)
```

### css_fetch_fn

自定义外部 CSS 文件的加载：

```python
async def custom_css_fetch(url: str) -> str:
    """自定义 CSS 加载"""
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.text


pic = await html_to_pic(
    '<link rel="stylesheet" href="https://example.com/style.css"><div>内容</div>',
    css_fetch_fn=custom_css_fetch,
)
```

## Fontconfig 配置

litehtml 使用 fontconfig 查找字体。在 Linux 系统上可能需要安装中文字体：

### Ubuntu / Debian

```bash
# 安装中文字体
sudo apt-get install fonts-noto-cjk fonts-noto-cjk-extra

# 刷新字体缓存
fc-cache -fv
```

### Alpine (Docker)

```dockerfile
RUN apk add --no-cache fontconfig font-noto-cjk
RUN fc-cache -fv
```

### 自定义 fontconfig

创建 `fonts.conf`：

```xml
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
    <dir>/usr/share/fonts</dir>
    <dir>/usr/local/share/fonts</dir>
    <dir>~/.fonts</dir>

    <!-- 默认中文字体 -->
    <match target="pattern">
        <test name="family">
            <string>sans-serif</string>
        </test>
        <edit name="family" mode="prepend">
            <string>Noto Sans CJK SC</string>
        </edit>
    </match>

    <!-- 等宽字体 -->
    <match target="pattern">
        <test name="family">
            <string>monospace</string>
        </test>
        <edit name="family" mode="prepend">
            <string>Noto Sans Mono CJK SC</string>
        </edit>
    </match>
</fontconfig>
```

在 CSS 中指定字体：

```css
body {
    font-family: "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
}
code {
    font-family: "Noto Sans Mono CJK SC", "Courier New", monospace;
}
```

## 支持的平台

| 平台 | 架构 | 支持状态 |
|------|------|----------|
| Linux | x86_64 | 完全支持 |
| Linux | aarch64 | 完全支持 |
| Windows | x86_64 | 完全支持 |
| macOS | x86_64 | 完全支持 |
| macOS | aarch64 (M1/M2) | 完全支持 |

## 实用示例

### 排行榜图片

```python
from nonebot_plugin_htmlkit import html_to_pic


async def render_leaderboard(data: list[dict]) -> bytes:
    rows = ""
    for i, item in enumerate(data, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, str(i))
        rows += f"""
        <tr>
            <td style="text-align:center;">{medal}</td>
            <td>{item['name']}</td>
            <td style="text-align:right;">{item['score']}</td>
        </tr>"""

    html = f"""
    <div style="padding:16px; background:#fff; border-radius:12px; font-family:'Microsoft YaHei',sans-serif;">
        <h2 style="text-align:center; color:#333; margin-bottom:16px;">积分排行榜</h2>
        <table style="width:100%; border-collapse:collapse;">
            <thead>
                <tr style="background:#f5f5f5;">
                    <th style="padding:8px; width:50px;">排名</th>
                    <th style="padding:8px; text-align:left;">用户</th>
                    <th style="padding:8px; text-align:right;">积分</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
    </div>
    """
    return await html_to_pic(html, width=400)
```

### 帮助文档图片

```python
from nonebot_plugin_htmlkit import md_to_pic


async def render_help() -> bytes:
    md = """
# Bot 帮助

## 基础命令
- `/help` - 显示帮助
- `/签到` - 每日签到
- `/积分` - 查看积分

## 娱乐命令
- `/猜数字` - 猜数字游戏
- `/抽签` - 今日运势

## 管理命令
- `/ban @用户` - 封禁用户
- `/unban @用户` - 解封用户
    """

    css = """
    body {
        font-family: "Microsoft YaHei", sans-serif;
        padding: 20px;
        background: #f9f9f9;
    }
    h1 { color: #5b7dff; border-bottom: 2px solid #5b7dff; padding-bottom: 8px; }
    h2 { color: #333; margin-top: 16px; }
    li { margin: 4px 0; }
    code { background: #e8e8e8; padding: 2px 6px; border-radius: 4px; }
    """
    return await md_to_pic(md, width=450, css=css)
```
