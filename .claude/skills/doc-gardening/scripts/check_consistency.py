#!/usr/bin/env python3
"""
检查文档与代码一致性。

验证项：
1. 术语一致性（Tlon vs Minions）
2. 配置字段名（position_id, respond_to_all 等）
3. 导入路径正确性
4. 配置格式示例
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple


class DocConsistencyChecker:
    """文档一致性检查器。"""
    
    # 已废弃的术语
    DEPRECATED_TERMS = {
        "Minions": "Tlon",
    }
    
    # 正确的配置字段
    CORRECT_FIELDS = {
        "position_id": ["position:"],  # position: 可能被误用
        "respond_to_all": ["proactive", "loading"],
        "model_name": ["model:"],  # model: 可能被误用
    }
    
    # 废弃的导入路径
    DEPRECATED_IMPORTS = [
        "from tlon.protocol",
        "from tlon.messaging import EventBus",
        "from tlon.agents.docker_runtime",
    ]
    
    # 正确的导入路径示例
    CORRECT_IMPORTS = [
        "from tlon.base.events import EventBus",
        "from tlon.base.config import ConfigLoader",
        "from tlon.sandbox.docker_runtime import DockerRuntime",
    ]
    
    def __init__(self, docs_dir: Path, src_dir: Path):
        self.docs_dir = docs_dir
        self.src_dir = src_dir
        self.issues: List[Tuple[str, str, str]] = []  # (file, line, issue)
    
    def check_all(self) -> bool:
        """运行所有检查。"""
        print(f"🔍 检查文档目录: {self.docs_dir}")
        print(f"🔍 检查源码目录: {self.src_dir}")
        print()
        
        md_files = list(self.docs_dir.rglob("*.md"))
        # 排除归档目录
        md_files = [f for f in md_files if "archive" not in str(f)]
        print(f"📄 发现 {len(md_files)} 个 Markdown 文件 (已排除归档)")
        print()
        
        for md_file in md_files:
            self._check_file(md_file)
        
        return len(self.issues) == 0
    
    def _check_file(self, file_path: Path):
        """检查单个文件。"""
        try:
            content = file_path.read_text(encoding='utf-8')
            lines = content.split('\n')
        except Exception as e:
            self.issues.append((str(file_path), "0", f"无法读取: {e}"))
            return
        
        for line_no, line in enumerate(lines, 1):
            # 检查废弃术语
            for deprecated, correct in self.DEPRECATED_TERMS.items():
                if deprecated in line and file_path.name != "brand-narrative-v2.md":
                    # brand-narrative-v2.md 保留历史引用
                    self.issues.append((
                        str(file_path), 
                        str(line_no), 
                        f"使用废弃术语 '{deprecated}'，应使用 '{correct}'"
                    ))
            
            # 检查配置字段
            # position: (在 YAML 中) 可能是错误的
            if re.search(r'^\s*position:\s*"', line) and 'position_id' not in line:
                self.issues.append((
                    str(file_path),
                    str(line_no),
                    "可能使用错误的字段名 'position'，应使用 'position_id'"
                ))
            
            # 检查废弃导入路径
            for deprecated_import in self.DEPRECATED_IMPORTS:
                if deprecated_import in line:
                    self.issues.append((
                        str(file_path),
                        str(line_no),
                        f"使用废弃导入路径 '{deprecated_import}'"
                    ))
    
    def report(self):
        """输出报告。"""
        print("=" * 60)
        print("📋 文档一致性检查报告")
        print("=" * 60)
        print()
        
        if not self.issues:
            print("✅ 所有检查通过！未发现明显问题。")
            return
        
        # 按严重程度分组
        errors = [i for i in self.issues if "错误" in i[2] or "废弃" in i[2]]
        warnings = [i for i in self.issues if i not in errors]
        
        if errors:
            print(f"🔴 错误 ({len(errors)} 个):")
            for file, line, issue in errors:
                print(f"  {file}:{line} - {issue}")
            print()
        
        if warnings:
            print(f"🟡 警告 ({len(warnings)} 个):")
            for file, line, issue in warnings:
                print(f"  {file}:{line} - {issue}")
            print()
        
        print("=" * 60)
        print(f"总计: {len(self.issues)} 个问题")
        print()
        print("💡 建议操作:")
        print("  1. 修复错误级别的术语和导入路径")
        print("  2. 验证警告是否为误报")
        print("  3. 运行 verify_tree.py 检查文档树完整性")


def main():
    parser = argparse.ArgumentParser(description="检查文档与代码一致性")
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=Path("./docs"),
        help="文档目录路径 (默认: ./docs)"
    )
    parser.add_argument(
        "--src-dir",
        type=Path,
        default=Path("./src/tlon"),
        help="源码目录路径 (默认: ./src/tlon)"
    )
    
    args = parser.parse_args()
    
    if not args.docs_dir.exists():
        print(f"❌ 文档目录不存在: {args.docs_dir}")
        sys.exit(1)
    
    checker = DocConsistencyChecker(args.docs_dir, args.src_dir)
    success = checker.check_all()
    checker.report()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
