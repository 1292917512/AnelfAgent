"""微信频道配置向导 — 扫码登录 iLink 并写入频道配置。

用法::

    uv run python scripts/weixin_setup.py

流程：加载项目网络与频道配置 → 终端显示二维码 → 微信扫码并确认 → account_id/token 写入
统一频道配置（凭据同时备份到 ``workspace/weixin/accounts/``）
→ 交互选择私聊/群聊策略。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.channel.config import register_channel_schema, set_channel_config  # noqa: E402
from channels.weixin.config import WeixinConfig  # noqa: E402
from channels.weixin.qr_login import qr_login  # noqa: E402
from core.config import ConfigManager  # noqa: E402


def _ask(prompt: str, options: list[str], default: str) -> str:
    opts = "/".join(f"[{o}]" if o == default else o for o in options)
    while True:
        answer = input(f"{prompt} ({opts}): ").strip().lower()
        if not answer:
            return default
        if answer in options:
            return answer
        print(f"请输入 {' / '.join(options)} 之一")


def main() -> None:
    print("=" * 56)
    print("  AnelfAgent 微信频道配置向导（iLink 扫码登录）")
    print("=" * 56)

    if not ConfigManager.initialize() or not register_channel_schema("weixin"):
        print("项目或微信频道配置加载失败，请检查配置后重试。")
        sys.exit(1)

    cred = asyncio.run(qr_login())
    if not cred:
        print("\n登录失败，请重试。")
        sys.exit(1)

    defaults = WeixinConfig()
    cfg: dict[str, str | bool] = {
        "account_id": cred["account_id"], "token": cred["token"],
        "base_url": cred.get("base_url") or str(ConfigManager.get("weixin_base_url", defaults.base_url)),
    }

    print()
    cfg["dm_policy"] = _ask(
        "私聊访问策略：open=所有人可聊 / allowlist=仅白名单 / disabled=禁用",
        ["open", "allowlist", "disabled"],
        str(ConfigManager.get("weixin_dm_policy", defaults.dm_policy)),
    )
    if cfg["dm_policy"] == "allowlist":
        cfg["allow_from"] = input(
            "私聊白名单用户 ID（逗号分隔，可留空稍后填）: "
        ).strip()

    cfg["group_policy"] = _ask(
        "群聊访问策略（注意：iLink bot 身份通常收不到普通群消息，建议 disabled）",
        ["disabled", "open", "allowlist"],
        str(ConfigManager.get("weixin_group_policy", defaults.group_policy)),
    )
    if cfg["group_policy"] == "allowlist":
        cfg["group_allow_from"] = input(
            "群聊白名单群 ID（逗号分隔，可留空稍后填）: "
        ).strip()

    enable = _ask("是否立即启用微信频道", ["yes", "no"], "yes")
    cfg["enabled"] = enable == "yes"

    set_channel_config("weixin", **cfg)
    print("\n配置已保存到微信频道配置。")
    if cfg["enabled"]:
        print("重启 AnelfAgent 后微信频道即可生效。")
    else:
        print("稍后可在 Web 通道管理中启用微信频道。")


if __name__ == "__main__":
    main()
