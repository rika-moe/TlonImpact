#!/usr/bin/env bash
# 质量门：一条命令跑完全部检查，CI 三个 workflow 与 pre-commit 均调本脚本分组。
# 分组：--static（静态检查 + TS 门禁）/ --unit / --integration，可组合；无参 = 全部。
# 每项独立执行（不 fail-fast，一次看清所有红灯），末尾汇总，任一失败退出码为 1。
set -uo pipefail

cd "$(dirname "$0")/.."

want_static=0
want_unit=0
want_integration=0
if [ $# -eq 0 ]; then
    want_static=1
    want_unit=1
    want_integration=1
else
    for arg in "$@"; do
        case "$arg" in
            --static) want_static=1 ;;
            --unit) want_unit=1 ;;
            --integration) want_integration=1 ;;
            *) echo "未知参数: $arg（可用：--static --unit --integration）" >&2; exit 2 ;;
        esac
    done
fi

failures=()
total=0

run() {
    local name="$1"
    shift
    total=$((total + 1))
    printf '\n\033[1m==> %s\033[0m\n' "$name"
    if "$@"; then
        printf '✅ %s\n' "$name"
    else
        printf '❌ %s\n' "$name"
        failures+=("$name")
    fi
}

# TS 应用门禁：node_modules 缺失时跳过并提示（本地可选；CI 必先 npm ci，恒执行）。
ts_run() {
    local name="$1" app="$2" script="$3"
    if [ ! -d "apps/$app/node_modules" ]; then
        printf '\n\033[1m==> %s\033[0m\n' "$name"
        printf '⚠️  %s 跳过：apps/%s/node_modules 不存在，先 cd apps/%s && npm install\n' "$name" "$app" "$app"
        return
    fi
    run "$name" npm --prefix "apps/$app" run "$script" --silent
}

if [ "$want_static" -eq 1 ]; then
    run "ruff"              uv run ruff check src/
    run "architecture"      uv run python scripts/check_architecture.py
    run "no-reexport"       uv run python scripts/check_no_reexport.py
    run "stage-purity"      uv run python scripts/check_stage_purity.py
    run "mypy"              uv run mypy src/tlon src/tlosure src/tlon_server src/tlon_client src/tlon_launcher
    run "lint-imports"      uv run lint-imports
    run "docs-freshness"    uv run python scripts/gen_reference_docs.py --check
    ts_run "webui-tests"    webui test
    ts_run "webui-build"    webui build
    ts_run "operator-tests" operator test
    ts_run "operator-build" operator build
    run "verify-package"    bash scripts/verify_package.sh
fi

if [ "$want_unit" -eq 1 ]; then
    run "pytest-unit"       uv run pytest src/tests/ --ignore=src/tests/integration -q
fi

if [ "$want_integration" -eq 1 ]; then
    run "pytest-integration" uv run pytest src/tests/integration/ -q
fi

printf '\n\033[1m======== 质量门汇总 ========\033[0m\n'
if [ ${#failures[@]} -eq 0 ]; then
    echo "✅ ${total}/${total} 全绿"
else
    echo "❌ ${#failures[@]}/${total} 失败：${failures[*]}"
    exit 1
fi
