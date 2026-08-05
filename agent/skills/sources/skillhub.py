"""SkillHub 技能源 — skillhub.cn 商店的可插拔接入模块。

自包含设计：技能系统核心零依赖本模块；删除本文件（登记项导入失败会自动
跳过）即完成卸载。搜索走公开 HTTP API（免鉴权），安装走 skillhub CLI。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from agent.skills.skill_store import SkillStore
from agent.skills.sources.base import ExternalSkill, InstallResult, SkillSource
from core.log import log

_SEARCH_URL = "https://api.skillhub.cn/api/skills"
_CLI_INSTALL_HINT = (
    "skillhub CLI 未安装，可执行以下命令安装后重试：\n"
    "curl -fsSL https://skillhub-1388575217.cos.ap-guangzhou.myqcloud.com"
    "/install/install.sh | bash -s -- --cli-only"
)


class SkillHubSource(SkillSource):
    """SkillHub 商店（专为中国用户优化的 AI Skills 社区，10 万+ 技能）。"""

    key = "skillhub"
    display_name = "SkillHub"
    description = "SkillHub 技能商店（skillhub.cn）：专为中国用户优化的 AI Skills 社区"
    categories = (
        "office-efficiency", "content-creation", "dev-programming", "data-analysis",
        "design-media", "ai-agent", "knowledge-management", "business-ops",
        "education", "professional", "it-ops-security", "life-service",
    )

    def install_hint(self) -> str:
        return _CLI_INSTALL_HINT

    async def search(self, query: str, category: str = "", top_k: int = 5) -> List[ExternalSkill]:
        """搜索 SkillHub 商店（接口为分词搜索，宜用同义/上位词多次检索）。"""
        if category and category not in self.categories:
            raise ValueError(
                f"SkillHub 不支持分类 '{category}'，可选: {', '.join(self.categories)}"
            )
        import httpx

        params: dict = {
            "keyword": query,
            "sortBy": "score",
            "pageSize": max(1, min(top_k, 20)),
        }
        if category:
            params["category"] = category
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(_SEARCH_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"SkillHub 接口返回错误: {payload.get('message', payload)}")
        results: List[ExternalSkill] = []
        for item in payload.get("data", {}).get("skills", []):
            namespace = item.get("namespace") or {}
            handle = namespace.get("handle") or item.get("ownerName") or ""
            slug = item.get("slug") or ""
            results.append(ExternalSkill(
                name=item.get("name") or slug,
                slug=slug,
                namespace=handle,
                description=item.get("description_zh") or item.get("description") or "",
                category=item.get("category") or "",
                downloads=item.get("downloads") or 0,
                installs=item.get("installs") or 0,
                requires_api_key=(item.get("labels") or {}).get("requires_api_key") == "true",
                homepage=f"https://skillhub.cn/skills/{handle}/{slug}" if handle and slug else "",
                source=self.key,
            ))
        return results

    def install(self, slug: str, namespace: str, skills_dir: Path) -> InstallResult:
        """通过 skillhub CLI 安装技能。

        CLI 原生布局为 ``<dir>/@<namespace>/<slug>/``，与本地技能库的平铺
        结构（``<skills_dir>/<slug>/SKILL.md``）不一致，故先装到临时目录再
        扁平化迁移，保证 SkillStore 能直接发现。
        """
        cli = self._find_cli()
        if not cli:
            return InstallResult(ok=False, error="skillhub CLI 未安装", hint=_CLI_INSTALL_HINT)
        with tempfile.TemporaryDirectory(prefix="skillhub-install-") as tmp:
            cmd = [cli, "install", slug, "--dir", tmp]
            if namespace:
                cmd += ["--namespace", namespace]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired:
                return InstallResult(ok=False, error="安装超时（>180s）", hint="网络较慢时可稍后重试")
            except OSError as exc:
                return InstallResult(ok=False, error=f"CLI 执行失败: {exc}", hint=_CLI_INSTALL_HINT)
            output = (proc.stdout + proc.stderr).strip()
            if proc.returncode != 0:
                log(f"skillhub install 失败 ({slug}): {output}", "WARNING", tag="技能")
                return InstallResult(ok=False, error=output or f"exit code {proc.returncode}")

            src = self._locate_installed_skill(Path(tmp), slug, namespace)
            if src is None:
                return InstallResult(ok=False, error="安装包中未找到 SKILL.md", hint=output)
            dst = skills_dir / SkillStore.normalize_name(slug)
            if dst.exists():
                shutil.rmtree(dst)
            shutil.move(str(src), str(dst))
        log(f"📦 SkillHub 技能已安装: {slug} -> {dst}", tag="技能")
        return InstallResult(ok=True, path=str(dst))

    @staticmethod
    def _find_cli() -> Optional[str]:
        """定位 skillhub CLI 可执行文件（PATH 优先，兼顾常见安装位置）。"""
        cli = shutil.which("skillhub")
        if cli:
            return cli
        candidate = Path.home() / ".local" / "bin" / "skillhub"
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None

    @staticmethod
    def _locate_installed_skill(tmp_dir: Path, slug: str, namespace: str) -> Optional[Path]:
        """在 CLI 安装产物中定位技能目录（优先精确路径，兜底全局搜索）。"""
        candidates = [tmp_dir / f"@{namespace}" / slug, tmp_dir / slug]
        for candidate in candidates:
            if (candidate / "SKILL.md").is_file():
                return candidate
        for skill_md in sorted(tmp_dir.rglob("SKILL.md")):
            return skill_md.parent
        return None


def get_source() -> SkillSource:
    """模块入口：sources 注册表按约定调用。"""
    return SkillHubSource()
