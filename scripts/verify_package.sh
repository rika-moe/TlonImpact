#!/usr/bin/env bash
# 打包内容验证：校验 wheel 该含的东西都含了（五包齐全、原型 DSL 数据文件、初始社会人物资源、WebUI 静态资源）。
# 检查项的唯一事实源——正式打包脚本（scripts/build_package.sh）与质量门
# （scripts/check_all.sh 的 verify-package 组）都调本脚本，检查项只在这里定义。
#
# 用法：
#   bash scripts/verify_package.sh            # 质量门模式：临时构建一个 wheel 验证，打完即清理。
#                                             # 本地缺 apps/webui/dist 时 WebUI 检查降级为跳过
#                                             #（本地可接受；CI 恒有 dist）。
#   bash scripts/verify_package.sh <wheel>    # 产物模式：直接验证指定 wheel，要求必含 WebUI，
#                                             # 缺了即失败（build_package.sh 自检走这条路）。
set -euo pipefail

cd "$(dirname "$0")/.."

WEBUI_SRC="apps/webui/dist"
WEBUI_PKG="src/tlon_server/webui_dist"

wheel="${1:-}"
bundle=1

if [ -z "$wheel" ]; then
    bundle=0
    # 构建产物（dist/、src/tlon_server/webui_dist/）退出时清理，不留在工作区。
    # webui_dist 必须清：tlon_server/api/rest/app.py 的 packaged 布局优先，
    # 残留会让 dev server 持续服务这份陈旧拷贝。
    cleanup() { rm -rf dist "$WEBUI_PKG"; }
    trap cleanup EXIT

    # build_package.sh 会保留 dist/（正式产物），这里先清掉再构建，
    # 保证 wheel 定位无歧义（ls dist/*.whl 只能命中一个）
    rm -rf dist

    if [ -f "$WEBUI_SRC/index.html" ]; then
        rm -rf "$WEBUI_PKG"
        cp -R "$WEBUI_SRC" "$WEBUI_PKG"
        bundle=1
    else
        echo "⚠️  $WEBUI_SRC 不存在，wheel 将不含 WebUI（本地可接受；CI 必含）"
    fi

    uv build >/dev/null
    wheel=$(ls dist/*.whl)
fi

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
# Verify the complete catalog rather than merely one matching Markdown file.
if uv run python - "$wheel" <<'PYCODE'
import json
import sys
from zipfile import ZipFile

with ZipFile(sys.argv[1]) as archive:
    root = "tlon/initial_society/"
    manifest = json.loads(archive.read(root + "manifest.json"))
    members = manifest["members"]
    assert len(members) == 30, "initial society must contain 30 members"
    names = set(archive.namelist())
    for member in members:
        for filename in ("IDENTITY.md", "SOUL.md", "RULES.md"):
            path = root + member["key"] + "/" + filename
            assert path in names, f"missing packaged persona: {path}"
            assert archive.read(path).strip(), f"empty packaged persona: {path}"
print("✅ wheel 含初始社会清单与全部 90 份中文人格文件")
PYCODE
then
    :
else
    echo "❌ wheel 初始社会资源不完整"
    fail=1
fi
if [ "$bundle" -eq 1 ]; then
    check "tlon_server/webui_dist/index.html" "wheel 含 WebUI 静态资源"
fi

exit $fail
