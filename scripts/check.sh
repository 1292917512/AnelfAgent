#!/bin/bash
# 本地 CI 镜像门禁：与 .github/workflows/ci.yml 同口径，push 前一键验证。
#
# 用法：
#   scripts/check.sh              # 静态 + 全量测试 + 前端 lint/单测/浏览器/构建
#   scripts/check.sh --fast       # 仅静态门禁（ruff + lint-imports + mypy）
#   scripts/check.sh --skip-fe    # 跳过前端门禁
#   scripts/check.sh --skip-browser # 跳过浏览器回归（其余前端检查仍执行）
#   scripts/check.sh --skip-test  # 跳过后端测试
#   scripts/check.sh -- <pytest 参数>  # 透传 pytest 参数（如 -k xxx 或路径收窄）

set -euo pipefail
cd "$(dirname "$0")/.."

RUN_TEST=1
RUN_FE=1
RUN_BROWSER=1
PYTEST_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --fast) RUN_TEST=0; RUN_FE=0 ;;
    --skip-fe) RUN_FE=0 ;;
    --skip-browser) RUN_BROWSER=0 ;;
    --skip-test) RUN_TEST=0 ;;
    --) ;;
    *) PYTEST_ARGS+=("$arg") ;;
  esac
done

step() { printf '\n\033[1;34m== %s ==\033[0m\n' "$1"; }

step "工作区指令大小"
AGENTS_LINES=$(wc -l < AGENTS.md)
if [ "$AGENTS_LINES" -gt 500 ]; then
  echo "AGENTS.md 超过 500 行（当前 $AGENTS_LINES），请把长期说明迁移到 docs/" >&2
  exit 1
fi
step "ruff"
uv run ruff check .

step "lint-imports（分层依赖守卫）"
uv run lint-imports

step "mypy core/（linux / darwin / win32 三平台）"
for plat in linux darwin win32; do
  uv run mypy core/ --platform "$plat"
done

step "mypy 全部生产模块"
uv run mypy

if [ "$RUN_TEST" -eq 1 ]; then
  step "pytest 全量"
  uv run pytest -n auto ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}
fi

if [ "$RUN_FE" -eq 1 ]; then
  if [ -d web/frontend/node_modules ]; then
    step "前端 ESLint + 模块/组件回归 + 构建"
    (
      cd web/frontend
      npm run lint
      npm run test:modules
      npm test
      npm run build
      if [ "$RUN_BROWSER" -eq 1 ]; then
        CI=1 npm run test:e2e
      fi
    )
  else
    echo "无法执行前端门禁：请先在 web/frontend 运行 npm ci，或显式指定 --skip-fe" >&2
    exit 1
  fi
fi

printf '\n\033[1;32m全部门禁通过\033[0m\n'
