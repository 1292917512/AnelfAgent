"""通过与 Web、AI 相同的接口管理本地检索服务，保留配置及数据卷。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "up", "connect", "update", "rollback", "stop", "status"])
    parser.add_argument("--crawl4ai-image")
    parser.add_argument("--searxng-image")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.retrieval.deployment import RetrievalDeployment
    from core.config import ConfigManager
    if not ConfigManager.initialize():
        parser.exit(1, "无法加载 Anelf 配置\n")
    manager = RetrievalDeployment(Path(__file__).resolve().parent)
    images = {key: value for key, value in {"crawl4ai": args.crawl4ai_image, "searxng": args.searxng_image}.items() if value}
    try:
        if args.action == "status" and images:
            parser.error("status 不接受镜像参数")
        if args.action != "status":
            manager.start(args.action, images)
            while manager.snapshot()["operation"]["status"] == "running":
                await asyncio.sleep(0.2)
        result = await manager.inspect()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(bool(result.get("error") or (args.action != "status" and result["operation"]["status"] != "succeeded")))
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, str(exc) + "\n")
    finally:
        await manager.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
