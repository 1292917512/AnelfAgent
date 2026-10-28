# AnelfAgent 0.4.0

独立桌面安装与管理程序：按步骤完成项目获取、运行环境准备和前端构建，安装完成后直接管理服务。

- 支持 GitHub / Gitee 项目来源，以及国内、国际环境下载源；优先复用已有 uv 和兼容工具。
- Python 依赖由 uv 管理，显示安装阶段、包下载进度和日志；取消后可切换镜像继续安装。
- 支持启动、停止、更新重启、Web 控制台入口、局域网访问、端口和可选访问密码配置。
- APP、Web 与命令行共用服务控制状态；关闭窗口收起到托盘，退出时可选择安全停止服务。
- 紧凑深色界面、中英文切换、按需展开详情；自动检测空间和端口占用。

## 安装包

| 系统 | 文件 |
| --- | --- |
| Windows x64 | `AnelfAgent-0.4.0-win-x64.exe` |
| macOS Intel | `AnelfAgent-0.4.0-mac-x64.dmg` |
| macOS Apple Silicon | `AnelfAgent-0.4.0-mac-arm64.dmg` |
| Linux x64（AppImage） | `AnelfAgent-0.4.0-linux-x64.AppImage` |
| Ubuntu / Debian x64 | `AnelfAgent-0.4.0-linux-x64.deb` |

安装包附 SHA-256 校验清单。构建产物仅作为 Release 附件，不提交源码仓库。
包体尚未配置 Windows 代码签名及 Apple 公证，系统可能提示来源未经验证。
各平台均经过原生 CI 构建与自动化回归；实际桌面安装已在 Windows 人工验证，macOS / Linux 的设备实测仍有待覆盖。
首次安装需要联网获取项目及依赖；无桌面环境的 Linux 可继续使用 `start.sh`。

官网：[anelf.cn](https://anelf.cn) · [安装与维护文档](https://github.com/1292917512/AnelfAgent/blob/main/app/README.md)
