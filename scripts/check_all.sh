#!/usr/bin/env bash
# 质量门：一条命令跑完全部检查，CI 与 pre-commit 均调本脚本分组。
# 分组：--static（静态检查 + TS 门禁）/ --unit / --integration，可组合；无参 = 全部。
# --windows-locks 是 Windows 原生适配第一阶段专项；无参全量已包含这些单测。
# 每项独立执行（不 fail-fast，一次看清所有红灯），末尾汇总，任一失败退出码为 1。
set -uo pipefail

cd "$(dirname "$0")/.."

want_static=0
want_unit=0
want_integration=0
want_windows_locks=0
want_windows_smoke=0
want_windows_workspace=0
want_windows_portable=0
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
            --windows-smoke) want_windows_smoke=1 ;;
            --windows-workspace) want_windows_workspace=1 ;;
            --windows-portable) want_windows_portable=1 ;;
            --windows-locks) want_windows_locks=1 ;;
            *) echo "未知参数: $arg（可用：--static --unit --integration --windows-locks --windows-smoke --windows-workspace --windows-portable）" >&2; exit 2 ;;
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
    run "no-reexport"       uv run python scripts/check_no_reexport.py
    run "mypy"              uv run mypy src/tlon src/tlosure src/tlon_server src/tlon_client src/tlon_launcher
    run "lint-imports"      uv run lint-imports
    run "runtime-identity"  uv run python scripts/gen_runtime_identity.py --check
    run "docs-freshness"    uv run python scripts/gen_reference_docs.py --check
    run "dev-proxy"         uv run python scripts/check_dev_proxy.py
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
    run "pytest-integration" uv run pytest src/tests/integration/ -vv --durations=10
fi

if [ "$want_windows_locks" -eq 1 ]; then
    run "pytest-windows-locks" uv run pytest -q \
        src/tests/base/test_directory_lock.py \
        src/tests/agents/test_review_prototype.py \
        src/tests/agents/test_staging_prototype.py \
        src/tests/operations/test_evolution_operations.py
fi

if [ "$want_windows_smoke" -eq 1 ]; then
    run "pytest-windows-smoke" uv run pytest -q \
        src/tests/server/test_server_lifecycle.py \
        src/tests/server/test_serverapp_shutdown.py \
        src/tests/integration/test_subprocess_cleanup.py \
        src/tests/integration/test_mail_smoke.py
fi

if [ "$want_windows_workspace" -eq 1 ]; then
    run "mypy-windows-workspace" uv run mypy --platform win32 \
        src/tlon/sandbox/_windows_handles.py \
        src/tlon/sandbox/workspace_files_windows.py \
        src/tlon/sandbox/windows_paths.py
    run "pytest-windows-workspace" uv run pytest -q \
        src/tests/sandbox/test_windows_workspace_files.py \
        src/tests/sandbox/test_workspace_files.py \
        src/tests/sandbox/test_author_files.py \
        src/tests/agents/test_assembly.py::test_workspace_operations_survive_unavailable_docker \
        src/tests/integration/test_windows_workspace.py
fi

if [ "$want_windows_portable" -eq 1 ]; then
    run "portable-windows-types" uv run --group dev mypy --platform win32 \
        src/tlon_launcher/portable.py src/tlon_launcher/portable_paths.py \
        src/tlon_launcher/portable_instance.py src/tlon_launcher/portable_runtime.py \
        src/tlon_launcher/portable_tray.py
    run "portable-native-unit" uv run --group dev pytest -q \
        src/tests/launcher/test_portable.py src/tests/server/test_portable_model_setup.py
    run "portable-build" uv run --no-dev --group portable-build python scripts/build_windows_portable.py
    run "portable-frozen-smoke" env TLON_PORTABLE_ZIP="$(pwd)/dist/windows-portable/Tlon-windows-x64.zip" \
        uv run --group dev pytest -q src/tests/integration/test_portable_artifact.py
fi

printf '\n\033[1m======== 质量门汇总 ========\033[0m\n'
if [ ${#failures[@]} -eq 0 ]; then
    echo "✅ ${total}/${total} 全绿"
else
    echo "❌ ${#failures[@]}/${total} 失败：${failures[*]}"
    exit 1
fi
