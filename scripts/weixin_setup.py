"""微信频道配置向导 — 扫码登录 iLink 并写入频道配置。

用法::

    uv run python scripts/weixin_setup.py

流程：终端显示二维码 → 微信扫码并确认 → account_id/token 自动写入
``channels/weixin/channel_config.json``（凭据同时备份到
``workspace/weixin/accounts/``）→ 交互选择私聊/群聊策略。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from channels.weixin.qr_login import qr_login  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "channels" / "weixin" / "channel_config.json"


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

    cred = asyncio.run(qr_login())
    if not cred:
        print("\n登录失败，请重试。")
        sys.exit(1)

    # 读取现有配置并更新凭据
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["account_id"] = cred["account_id"]
    cfg["token"] = cred["token"]
    cfg["base_url"] = cred.get("base_url") or cfg.get("base_url") or "https://ilinkai.weixin.qq.com"
    cfg.setdefault("cdn_base_url", "https://novac2c.cdn.weixin.qq.com/c2c")

    print()
    cfg["dm_policy"] = _ask(
        "私聊访问策略：open=所有人可聊 / allowlist=仅白名单 / disabled=禁用",
        ["open", "allowlist", "disabled"],
        cfg.get("dm_policy") or "open",
    )
    if cfg["dm_policy"] == "allowlist":
        cfg["allow_from"] = input(
            "私聊白名单用户 ID（逗号分隔，可留空稍后填）: "
        ).strip()

    cfg["group_policy"] = _ask(
        "群聊访问策略（注意：iLink bot 身份通常收不到普通群消息，建议 disabled）",
        ["disabled", "open", "allowlist"],
        cfg.get("group_policy") or "disabled",
    )
    if cfg["group_policy"] == "allowlist":
        cfg["group_allow_from"] = input(
            "群聊白名单群 ID（逗号分隔，可留空稍后填）: "
        ).strip()

    enable = _ask("是否立即启用微信频道", ["yes", "no"], "yes")
    cfg["enabled"] = enable == "yes"

    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    print(f"\n配置已写入 {CONFIG_PATH}")
    if cfg["enabled"]:
        print("重启 AnelfAgent 后微信频道即可生效。")
    else:
        print("稍后可将 channel_config.json 中 enabled 改为 true 并重启。")


if __name__ == "__main__":
    main()
