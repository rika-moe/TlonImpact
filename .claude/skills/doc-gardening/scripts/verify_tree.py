#!/usr/bin/env python3
"""
验证文档树完整性。

检查：
1. 所有文档可从 AGENTS.md 到达
2. 无孤立文档
3. 链接有效性
"""

import argparse
import re
import sys
from collections import deque
from pathlib import Path


class DocTreeVerifier:
    """文档树验证器。"""

    def __init__(self, docs_dir: Path, root_file: str = "AGENTS.md"):
        self.docs_dir = docs_dir
        self.root_file = root_file
        self.visited: set[Path] = set()
        self.broken_links: list[tuple[str, str]] = []  # (from_file, link)
        self.orphan_docs: list[Path] = []

    def verify(self) -> bool:
        """运行验证。"""
        root_path = self.docs_dir.parent / self.root_file
        if not root_path.exists():
            print(f"❌ 根文档不存在: {root_path}")
            return False

        print(f"🔍 验证文档树，根节点: {root_path}")
        print()

        # BFS 遍历文档树
        self._bfs_traversal(root_path)

        # 查找孤立文档
        self._find_orphans()

        return len(self.broken_links) == 0 and len(self.orphan_docs) == 0

    def _bfs_traversal(self, start_path: Path):
        """广度优先遍历文档树。"""
        queue = deque([start_path])

        while queue:
            current = queue.popleft()
            if current in self.visited:
                continue

            self.visited.add(current)

            try:
                content = current.read_text(encoding="utf-8")
            except Exception as e:
                print(f"⚠️  无法读取 {current}: {e}")
                continue

            # 提取 Markdown 链接
            # 匹配 [text](path) 或 [text](./path) 或 [text](../path)
            links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)

            for _text, link in links:
                # 忽略外部链接
                if link.startswith(("http://", "https://", "#", "mailto:")):
                    continue

                # 解析相对路径
                resolved = self._resolve_link(current, link)
                if resolved and resolved.suffix == ".md":
                    if resolved.exists():
                        if resolved not in self.visited:
                            queue.append(resolved)
                    else:
                        self.broken_links.append((str(current), link))

    def _resolve_link(self, from_file: Path, link: str) -> Path:
        """解析链接为绝对路径。"""
        # 移除锚点
        if "#" in link:
            link = link.split("#")[0]

        if not link:
            return None

        # 相对路径
        if link.startswith("./") or link.startswith("../"):
            return (from_file.parent / link).resolve()

        # 绝对路径（相对于 docs 目录）
        if link.startswith("/"):
            return (self.docs_dir.parent / link.lstrip("/")).resolve()

        # 简单文件名，尝试在相同目录查找
        return (from_file.parent / link).resolve()

    def _find_orphans(self):
        """查找孤立文档（无法从根节点到达）。"""
        all_docs = set(self.docs_dir.rglob("*.md"))

        # 排除 archive 目录，并转换为绝对路径以便比较
        all_docs = {d.resolve() for d in all_docs if "archive" not in str(d)}

        unreachable = all_docs - self.visited

        # 排除已知的历史文档
        known_historical = {
            "brand-narrative-v2.md",
        }

        self.orphan_docs = [d for d in unreachable if d.name not in known_historical]

    def report(self):
        """输出报告。"""
        print("=" * 60)
        print("📋 文档树完整性报告")
        print("=" * 60)
        print()

        print(f"📄 已访问文档: {len(self.visited)} 个")
        print()

        if self.broken_links:
            print(f"🔴 断链 ({len(self.broken_links)} 个):")
            for from_file, link in self.broken_links:
                print(f"  {from_file} -> [{link}]")
            print()

        if self.orphan_docs:
            print(f"🟡 孤立文档 ({len(self.orphan_docs)} 个):")
            for doc in self.orphan_docs:
                print(f"  {doc}")
            print()
            print("💡 孤立文档处理建议:")
            print("  1. 如果文档已过时，移动到 docs/archive/")
            print("  2. 如果文档仍然有效，添加从 AGENTS.md 的链接")
            print()

        if not self.broken_links and not self.orphan_docs:
            print("✅ 文档树完整性良好！")
            print()
            print(f"  - 所有文档可从 {self.root_file} 到达")
            print("  - 无断链")
            print("  - 无孤立文档")


def main():
    parser = argparse.ArgumentParser(description="验证文档树完整性")
    parser.add_argument(
        "--docs-dir", type=Path, default=Path("./docs"), help="文档目录路径 (默认: ./docs)"
    )
    parser.add_argument(
        "--root", type=str, default="AGENTS.md", help="根文档文件名 (默认: AGENTS.md)"
    )

    args = parser.parse_args()

    if not args.docs_dir.exists():
        print(f"❌ 文档目录不存在: {args.docs_dir}")
        sys.exit(1)

    verifier = DocTreeVerifier(args.docs_dir, args.root)
    success = verifier.verify()
    verifier.report()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
