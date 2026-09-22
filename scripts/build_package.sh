#!/usr/bin/env bash
# 打包验证：uv build 出 wheel 并校验内容（五包齐全、原型 DSL 数据文件、WebUI 静态资源）。
# apps/webui/dist 存在时打进 tlon_server/webui_dist/ 包数据（安装态 /ui/ 开箱可用）；
# CI 恒有 dist（static 组先跑 webui-build），本地缺 node_modules 时跳过打包并提示。
# 构建产物（dist/、src/tlon_server/webui_dist/）退出时清理，不留在工作区。
set -euo pipefail

cd "$(dirname "$0")/.."

WEBUI_SRC="apps/webui/dist"
WEBUI_PKG="src/tlon_server/webui_dist"

cleanup() { rm -rf dist "$WEBUI_PKG"; }
trap cleanup EXIT

bundle=0
if [ -f "$WEBUI_SRC/index.html" ]; then
    rm -rf "$WEBUI_PKG"
    cp -R "$WEBUI_SRC" "$WEBUI_PKG"
    bundle=1
else
    echo "⚠️  $WEBUI_SRC 不存在，wheel 将不含 WebUI（本地可接受；CI 必含）"
fi

uv build >/dev/null
wheel=$(ls dist/*.whl)

fail=0
check() { # check <字符串> <描述>：wheel 清单里应能找到
    # 不用 grep -q：-q 提前退出会让上游 unzip 吃 SIGPIPE，pipefail 下误判为失败
    if unzip -l "$wheel" | grep "$1" >/dev/null; then
        printf '✅ %s\n' "$2"
    else
        printf '❌ %s\n' "$2"
        fail=1
    fi
}

for pkg in tlon tlosure tlon_server tlon_client tlon_launcher; do
    check "$pkg/__init__.py" "wheel 含 $pkg 包"
done
check "behavior.tlon" "wheel 含原型 DSL 数据文件"
if [ "$bundle" -eq 1 ]; then
    check "tlon_server/webui_dist/index.html" "wheel 含 WebUI 静态资源"
fi

exit $fail
