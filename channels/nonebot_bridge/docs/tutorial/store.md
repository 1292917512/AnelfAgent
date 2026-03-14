<!-- source: https://nonebot.dev/docs/tutorial/store -->

# 获取商店内容

> 如果你暂时没有获取商店内容的需求，可以跳过本章节。

NoneBot 提供了一个 [商店](https://nonebot.dev/store/plugins)，商店内容均由社区开发者贡献。你可以在商店中查找你需要的适配器和插件等，进行安装或者参考其文档。

商店中每个内容的卡片都包含了其名称和简介等信息，点击卡片右上角链接图标即可跳转到其主页。

NB-CLI 也提供了一个 TUI 版本的商店界面，可通过以下命令进入：

```bash
nb adapter store   # 适配器商店
nb plugin store    # 插件商店
nb driver store    # 驱动器商店
```

## 安装插件

### 使用 nb-cli 命令安装

```bash
nb plugin install <插件名称>
```

### 使用交互式安装

```bash
nb plugin install
# 进入交互式界面选择插件
```

### 使用 pip 安装

```bash
pip install <插件包名>
```

> 使用 pip 安装时，你需要手动将插件添加到加载列表中。

### 查看插件列表

```bash
# 列出商店所有插件
nb plugin list

# 搜索商店插件
nb plugin search [可选关键词]
```

### 升级插件

```bash
# 使用 nb-cli
nb plugin update <插件名称>

# 使用 pip
pip install --upgrade <插件包名>
```

### 卸载插件

```bash
# 使用 nb-cli
nb plugin uninstall <插件名称>

# 使用 pip
pip uninstall <插件包名>
```

## 安装适配器

安装适配器与安装插件类似，只是将命令换为 `nb adapter`。

### 使用 nb-cli 命令安装

```bash
nb adapter install <适配器名称>
```

`nb-cli` 会自动安装适配器并将其添加到注册列表中。

### 使用 pip 安装

```bash
pip install <适配器包名>
```

### 查看适配器列表

```bash
# 列出商店所有适配器
nb adapter list

# 搜索商店适配器
nb adapter search [可选关键词]
```

### 升级和卸载适配器

```bash
# 升级
nb adapter update <适配器名称>

# 卸载
nb adapter uninstall <适配器名称>
```

## 安装驱动器

安装驱动器与安装插件同样类似，只是将命令换为 `nb driver`。

### 使用 nb-cli 命令安装

```bash
nb driver install <驱动器名称>
```

> **注意**：`nb-cli` 并不会在安装驱动器后修改项目所使用的驱动器，请自行在 `.env` 配置文件中修改 `DRIVER` 配置项。

### 使用 pip 安装

```bash
pip install <驱动器包名>
```

### 查看驱动器列表

```bash
# 列出商店所有驱动器
nb driver list

# 搜索商店驱动器
nb driver search [可选关键词]
```

### 升级和卸载驱动器

```bash
# 升级
nb driver update <驱动器名称>

# 卸载
nb driver uninstall <驱动器名称>
```

## nb-cli 命令速查表

| 操作 | 插件 | 适配器 | 驱动器 |
|------|------|--------|--------|
| 安装 | `nb plugin install <name>` | `nb adapter install <name>` | `nb driver install <name>` |
| 卸载 | `nb plugin uninstall <name>` | `nb adapter uninstall <name>` | `nb driver uninstall <name>` |
| 升级 | `nb plugin update <name>` | `nb adapter update <name>` | `nb driver update <name>` |
| 列表 | `nb plugin list` | `nb adapter list` | `nb driver list` |
| 搜索 | `nb plugin search [kw]` | `nb adapter search [kw]` | `nb driver search [kw]` |
| 商店 | `nb plugin store` | `nb adapter store` | `nb driver store` |
