# 嵌套插件

NoneBot 支持在插件内部加载子插件，形成插件层级结构。这有助于将复杂的大型插件拆分成多个独立的子模块，提升可维护性。

## 基本概念

嵌套插件是指一个插件包（Python 包）内部包含的子插件。父插件在初始化时加载这些子插件，子插件与父插件共享同一命名空间。

## 目录结构

### 典型的嵌套插件结构

```
my_plugin/
├── __init__.py          # 父插件入口，负责加载子插件
├── config.py            # 插件配置
├── plugins/             # 子插件目录
│   ├── __init__.py
│   ├── feature_a.py     # 子插件 A
│   ├── feature_b.py     # 子插件 B
│   └── feature_c/       # 子插件 C（包形式）
│       ├── __init__.py
│       └── utils.py
└── utils.py             # 共享工具模块
```

## 加载子插件

### 使用 load_plugins()

在父插件的 `__init__.py` 中使用 `load_plugins()` 加载整个目录下的子插件：

```python
# my_plugin/__init__.py
import nonebot
from pathlib import Path
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="我的插件",
    description="包含多个子功能的综合插件",
    usage="见各子插件说明",
)

# 获取当前文件所在目录
sub_plugins = nonebot.load_plugins(
    str(Path(__file__).parent / "plugins")
)
```

### 使用 load_plugin()

加载单个子插件：

```python
# my_plugin/__init__.py
from nonebot import load_plugin

load_plugin("my_plugin.plugins.feature_a")
load_plugin("my_plugin.plugins.feature_b")
```

### 使用 load_all_plugins()

同时指定模块路径和插件目录：

```python
from pathlib import Path
from nonebot import load_all_plugins

load_all_plugins(
    module_path={"my_plugin.plugins.feature_a"},
    plugin_dir={str(Path(__file__).parent / "plugins")},
)
```

## 子插件示例

### 父插件

```python
# my_plugin/__init__.py
from pathlib import Path
import nonebot
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="游戏中心",
    description="提供多种小游戏",
    usage="/game list — 查看游戏列表",
    type="application",
)

sub_plugins = nonebot.load_plugins(
    str(Path(__file__).parent / "plugins")
)
```

### 子插件 A

```python
# my_plugin/plugins/dice.py
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
import random

__plugin_meta__ = PluginMetadata(
    name="掷骰子",
    description="掷骰子小游戏",
    usage="/dice [面数]",
)

dice = on_command("dice", aliases={"骰子"})

@dice.handle()
async def handle_dice(args: Message = CommandArg()):
    sides = 6
    if text := args.extract_plain_text().strip():
        try:
            sides = int(text)
        except ValueError:
            await dice.finish("请输入有效的面数")

    result = random.randint(1, sides)
    await dice.finish(f"🎲 掷出了 {result}（{sides}面骰）")
```

### 子插件 B

```python
# my_plugin/plugins/guess.py
from nonebot import on_command
from nonebot.typing import T_State
from nonebot.plugin import PluginMetadata
import random

__plugin_meta__ = PluginMetadata(
    name="猜数字",
    description="猜数字游戏",
    usage="/guess — 开始猜数字",
)

guess = on_command("guess", aliases={"猜数字"})

@guess.handle()
async def start_guess(state: T_State):
    state["answer"] = random.randint(1, 100)
    await guess.send("我想了一个 1-100 的数字，猜猜看！")

@guess.got("number", prompt="请输入你猜的数字：")
async def handle_guess(state: T_State):
    try:
        num = int(state["number"])
    except (ValueError, TypeError):
        await guess.reject("请输入有效数字")
        return

    answer = state["answer"]
    if num < answer:
        await guess.reject("太小了，再猜！")
    elif num > answer:
        await guess.reject("太大了，再猜！")
    else:
        await guess.finish(f"🎉 恭喜，答案就是 {answer}！")
```

## 插件层级关系

### 访问父子关系

```python
import nonebot

# 获取父插件
parent = nonebot.get_plugin("my_plugin")
print(parent.sub_plugins)  # 所有子插件的 Plugin 对象集合

# 子插件可以访问父插件
child = nonebot.get_plugin("my_plugin:dice")
print(child.parent_plugin)  # 父插件的 Plugin 对象
```

## 共享代码

子插件可以导入父插件包中的共享模块：

```python
# my_plugin/utils.py
def format_score(score: int) -> str:
    return f"得分: {score} 分"
```

```python
# my_plugin/plugins/dice.py
from my_plugin.utils import format_score

@dice.handle()
async def handle():
    result = random.randint(1, 6)
    await dice.finish(format_score(result))
```

> **注意**：共享模块不应包含事件响应器定义，否则会被当作独立插件处理。

## 注意事项

1. **避免循环依赖**：子插件之间、子插件与父插件之间不应存在循环导入
2. **加载顺序**：`load_plugins()` 不保证子插件的加载顺序
3. **跨子插件调用**：子插件之间的调用应通过 `require()` 声明依赖（详见[跨插件访问](requiring.md)）
4. **元数据继承**：子插件应各自定义 `PluginMetadata`，不会自动继承父插件的元数据
5. **路径问题**：`load_plugins()` 需要传入绝对路径字符串，建议使用 `Path(__file__).parent` 构建
