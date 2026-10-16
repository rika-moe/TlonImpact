#!/usr/bin/env python3
"""Dev-proxy drift guard: vite dev 代理清单必须覆盖全部 REST 路由前缀。

`apps/webui/vite.config.ts` 的 ``API_PATHS`` 是手工维护的清单，曾经漏掉
``/members``——dev 模式下 MailView 观察成员信箱必 404（issue #144）。
本脚本把清单变成机器强制的规则：从
``src/tlon_server/api/rest/app.py`` 的路由注册（AST）提取全部首段前缀，
断言每个前缀要么在 ``API_PATHS`` 里、要么在下方的 ``EXCLUDED`` 里
（附不代理的理由）；反向也校验——``API_PATHS`` 里的条目必须是真实
路由前缀（防止清单腐烂）。

新增路由时本脚本会红：代理它（加进 API_PATHS）或在 EXCLUDED 里写明
不代理的理由，二选一，不许静默漂移。
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REST_FILE = ROOT / "src/tlon_server/api/rest/app.py"
VITE_CONFIG = ROOT / "apps/webui/vite.config.ts"

#: aiohttp router registration methods whose first arg is a route path.
_ROUTE_METHODS = {"add_get", "add_post", "add_patch", "add_delete", "add_static"}

#: Route prefixes deliberately NOT proxied in dev, with the reason.
#: 加条目必须写明理由——这是「不代理」的唯一合法位置。
EXCLUDED: dict[str, str] = {
    "/ui": "dev 下由 vite 自己托管前端，不能代理给服务端",
    "/webhooks": "GitHub → 服务端的入向回调，浏览器从不直接调",
    "/test": "debug 回声路由（注册本身带条件），前端不使用",
}


def route_prefixes() -> set[str]:
    """Extract the first segment of every registered route path (AST)."""
    tree = ast.parse(REST_FILE.read_text(encoding="utf-8"), filename=str(REST_FILE))
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            not isinstance(func, ast.Attribute)
            or func.attr not in _ROUTE_METHODS
            or not node.args
        ):
            continue
        path_arg = node.args[0]
        if not isinstance(path_arg, ast.Constant) or not isinstance(path_arg.value, str):
            continue
        path = path_arg.value
        if path.startswith("/"):
            segment = path.split("/")[1]
            if segment and not segment.startswith("{"):
                prefixes.add(f"/{segment}")
    return prefixes


def vite_proxy_paths() -> set[str]:
    """Extract the API_PATHS entries from vite.config.ts.

    Line and block comments are stripped first — a commented-out entry
    must not count as proxied.
    """
    text = VITE_CONFIG.read_text(encoding="utf-8")
    match = re.search(r"API_PATHS\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if match is None:
        raise SystemExit(f"{VITE_CONFIG}: 找不到 API_PATHS 数组")
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.DOTALL)
    body = re.sub(r"//[^\n]*", "", body)
    return set(re.findall(r'["\'](/[^"\']+)["\']', body))


def main() -> int:
    routes = route_prefixes()
    if not routes:
        print(f"{REST_FILE}: 没提取到任何路由，提取器可能失效", file=sys.stderr)
        return 1
    proxied = vite_proxy_paths()

    uncovered = routes - proxied - set(EXCLUDED)
    stale = proxied - routes
    ok = True
    for prefix in sorted(uncovered):
        print(
            f"❌ 路由 {prefix} 未被 dev 代理覆盖：加进 vite.config.ts 的 API_PATHS，"
            f"或在 {Path(__file__).name} 的 EXCLUDED 里写明不代理的理由",
            file=sys.stderr,
        )
        ok = False
    for prefix in sorted(stale):
        print(
            f"❌ API_PATHS 条目 {prefix} 不是真实路由前缀（腐烂条目，删掉）",
            file=sys.stderr,
        )
        ok = False

    if ok:
        print(
            f"✅ dev 代理清单与服务端路由对齐：{len(proxied)} 代理 + "
            f"{len(set(EXCLUDED) & routes)} 显式豁免"
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
