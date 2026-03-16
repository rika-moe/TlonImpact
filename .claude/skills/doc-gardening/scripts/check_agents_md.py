#!/usr/bin/env python3
"""
验证 AGENTS.md 符合地图层规范。

检查项：
1. 总行数 < 100
2. 包含文档索引表
3. 包含状态总览
4. 无详细实现描述（标记为⚠️）
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple


class AgentsMdChecker:
    """AGENTS.md 规范检查器。"""

    def __init__(self, agents_path: Path, max_lines: int = 100):
        self.agents_path = agents_path
        self.max_lines = max_lines
        self.issues: List[Tuple[str, str]] = []  # (检查项, 详情)
        self.content: str = ""
        self.lines: List[str] = []

    def check(self) -> bool:
        """运行所有检查。"""
        if not self.agents_path.exists():
            self.issues.append(("存在性", f"AGENTS.md 不存在: {self.agents_path}"))
            return False

        self.content = self.agents_path.read_text(encoding='utf-8')
        self.lines = self.content.split('\n')

        self._check_length()
        self._check_doc_index()
        self._check_status_overview()
        self._check_no_detailed_impl()

        return len(self.issues) == 0

    def _check_length(self):
        """检查行数。"""
        line_count = len(self.lines)
        if line_count > self.max_lines:
            self.issues.append(
                ("长度", f"AGENTS.md 有 {line_count} 行，超过 {self.max_lines} 行限制")
            )
        else:
            print(f"✅ 长度检查通过: {line_count} 行 (限制: {self.max_lines})")

    def _check_doc_index(self):
        """检查是否包含文档索引表。"""
        # 查找表格模式
        table_pattern = r'\|[^\n]+\|\n\|[-:|\s]+\|'
        if re.search(table_pattern, self.content):
            print("✅ 文档索引表: 已找到")
        else:
            self.issues.append(
                ("文档索引", "未找到文档索引表（Markdown 表格格式）")
            )

    def _check_status_overview(self):
        """检查是否包含状态总览。"""
        # 查找状态相关标记
        status_patterns = [
            r'状态|健康度|评分|quality|status',
            r'✅|⚠️|❌|🗂️',
        ]
        found = any(re.search(p, self.content, re.IGNORECASE) for p in status_patterns)
        if found:
            print("✅ 状态总览: 已找到")
        else:
            self.issues.append(
                ("状态总览", "未找到状态总览（使用 ✅⚠️❌ 标记或'状态'关键词）")
            )

    def _check_no_detailed_impl(self):
        """检查是否有详细实现描述（警告级别）。"""
        # 长代码块可能表示详细实现
        code_blocks = re.findall(r'```[\s\S]*?```', self.content)
        long_blocks = [b for b in code_blocks if len(b.split('\n')) > 15]

        if long_blocks:
            self.issues.append(
                ("详细实现", f"发现 {len(long_blocks)} 个长代码块（>15行），建议移至下级文档")
            )

        # 检查是否有深度标题
        deep_headers = re.findall(r'\n#{4,}[^#]', self.content)
        if deep_headers:
            self.issues.append(
                ("详细实现", f"发现 {len(deep_headers)} 个四级或更深标题，建议简化")
            )

    def report(self):
        """输出报告。"""
        print()
        print("=" * 60)
        print("📋 AGENTS.md 规范检查报告")
        print("=" * 60)
        print()

        if not self.issues:
            print("✅ AGENTS.md 符合地图层规范！")
            print()
            print(f"  - 行数: {len(self.lines)} / {self.max_lines}")
            print("  - 文档索引表: ✓")
            print("  - 状态总览: ✓")
            return

        errors = [i for i in self.issues if i[0] != "详细实现"]
        warnings = [i for i in self.issues if i[0] == "详细实现"]

        if errors:
            print(f"🔴 错误 ({len(errors)} 个):")
            for check, detail in errors:
                print(f"  [{check}] {detail}")
            print()

        if warnings:
            print(f"🟡 建议 ({len(warnings)} 个):")
            for check, detail in warnings:
                print(f"  [{check}] {detail}")
            print()

        print("=" * 60)
        print("💡 修复建议:")
        print("  1. 将详细实现移至 docs/design/ 或 docs/architecture/")
        print("  2. AGENTS.md 只保留索引和状态总览")
        print("  3. 使用链接指向下级文档")


def main():
    parser = argparse.ArgumentParser(description="验证 AGENTS.md 符合地图层规范")
    parser.add_argument(
        "--agents-path",
        type=Path,
        default=Path("./AGENTS.md"),
        help="AGENTS.md 路径 (默认: ./AGENTS.md)"
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=100,
        help="最大行数限制 (默认: 100)"
    )

    args = parser.parse_args()

    checker = AgentsMdChecker(args.agents_path, args.max_lines)
    success = checker.check()
    checker.report()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
