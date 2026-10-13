"""wcauto 桥 — Windows 侧微信个人号桥服务。

把 wechatauto-replica（微信 4.x Windows 客户端自动化库）包装成 WebSocket
服务，供 AnelfAgent 的 wcauto 频道远程对接：

- 收消息：wechatauto.db.Listener 轮询解密数据库（不操作界面，锁屏可用）
- 发消息：wechatauto.guia.WeChatGUI（UIA 主路径 + OCR/坐标兜底，需窗口可见）
- 媒体：MediaDownloader（图片 .dat 解密 / SILK 语音 / 视频 / 文件）
- 朋友圈：MomentDB 直读 + UIA 点赞/评论
- 防撤回：RecallGuard 镜像 + 撤回检测

协议见 protocol.py。本包自包含（仅依赖 aiohttp 与 wechatauto），
可整目录拷贝到 Windows 独立运行；wechatauto 为惰性导入，
无微信环境下 protocol/config/server 仍可导入与测试。
"""

__version__ = "1.0.0"
