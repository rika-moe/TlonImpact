#!/usr/bin/env bash
# 正式打包：构建含 WebUI 的可发布产物（wheel + sdist）到 dist/，产物保留。
# 发布流程（打 tag、上传 PyPI）不在本脚本内，由 release 流程在拿到 dist/ 后执行。
#
# 质量门的内容验证在 scripts/verify_package.sh（检查项唯一事实源）；
# 本脚本构建后调它对产物自检（产物模式：必含 WebUI，缺了即失败）。
set -euo pipefail

cd "$(dirname "$0")/.."

uv run python scripts/gen_runtime_identity.py --check

WEBUI_PKG="src/tlon_server/webui_dist"

if [ ! -d apps/webui/node_modules ]; then
    echo "❌ apps/webui/node_modules 不存在，先 cd apps/webui && npm install" >&2
    exit 1
fi

# webui_dist 是构建期临时同步的包数据，用完即删：tlon_server/api/rest/app.py
# 的 packaged 布局优先，残留会让 dev server 持续服务这份陈旧拷贝。
cleanup() { rm -rf "$WEBUI_PKG"; }
trap cleanup EXIT

npm --prefix apps/webui run build --silent
rm -rf "$WEBUI_PKG"
cp -R apps/webui/dist "$WEBUI_PKG"

# 清掉旧产物再构建，保证 dist/ 里只有本次版本，wheel 定位无歧义
rm -rf dist
uv build

wheel=$(ls dist/*.whl)
bash scripts/verify_package.sh "$wheel"

echo "📦 产物就绪："
ls -lh dist/
