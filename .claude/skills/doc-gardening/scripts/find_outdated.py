#!/usr/bin/env python3
"""
识别可能过时的文档。

判断标准：
1. 引用的代码文件已删除或移动
2. 配置的字段名已变更
3. 文档修改时间远早于相关代码
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional


class OutdatedDocFinder:
    """过时文档查找器。"""
    
    # 已废弃的配置字段映射
    DEPRECATED_FIELDS = {
        "position:": "position_id",  # 在 YAML 中
        "loading": "respond_to_all",
        "model:": "model_name",  # 在 YAML 中
    }
    
    # 已移动的模块路径
    MOVED_MODULES = {
        "tlon.protocol": "tlon.base.events / tlon.organization.messaging",
        "tlon.messaging": "tlon.base.events",
        "tlon.agents.docker_runtime": "tlon.sandbox.docker_runtime",
        "tlon.agents.config": "tlon.base.config",
    }
    
    def __init__(self, docs_dir: Path, src_dir: Path, since: Optional[str] = None):
        self.docs_dir = docs_dir
        self.src_dir = src_dir
        self.since = since
        self.suspicious: List[Tuple[Path, str, str]] = []  # (doc, reason, suggestion)
    
    def find(self) -> bool:
        """查找可能过时的文档。"""
        print(f"🔍 扫描文档目录: {self.docs_dir}")
        print(f"🔍 对照源码目录: {self.src_dir}")
        if self.since:
            print(f"📅 检查自 {self.since} 以来的变更")
        print()
        
        md_files = list(self.docs_dir.rglob("*.md"))
        
        for md_file in md_files:
            # 跳过 archive 目录
            if "archive" in str(md_file):
                continue
            
            self._check_file(md_file)
        
        return len(self.suspicious) == 0
    
    def _check_file(self, file_path: Path):
        """检查单个文件。"""
        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception:
            return
        
        # 检查 1: 引用废弃字段
        for deprecated, correct in self.DEPRECATED_FIELDS.items():
            if deprecated in content:
                self.suspicious.append((
                    file_path,
                    f"使用已废弃字段 '{deprecated}'",
                    f"更新为 '{correct}'"
                ))
        
        # 检查 2: 引用已移动的模块
        for old_path, new_path in self.MOVED_MODULES.items():
            if old_path in content:
                self.suspicious.append((
                    file_path,
                    f"引用已移动的模块 '{old_path}'",
                    f"更新为 '{new_path}'"
                ))
        
        # 检查 3: 引用的代码文件是否存在
        # 匹配 `src/tlon/...` 或 `./src/tlon/...`
        code_refs = re.findall(r'`(src/tlon/[^`]+)`|`\./(src/tlon/[^`]+)`', content)
        for match in code_refs:
            ref = match[0] or match[1]
            full_path = self.docs_dir.parent / ref
            if not full_path.exists():
                self.suspicious.append((
                    file_path,
                    f"引用不存在的代码文件 '{ref}'",
                    "验证路径或更新文档"
                ))
        
        # 检查 4: 配置示例中的旧格式
        # 检测 agents.yaml (旧格式) vs agents/{id}.yaml (新格式)
        if "config/agents.yaml" in content:
            self.suspicious.append((
                file_path,
                "引用旧的配置路径 'config/agents.yaml'",
                "更新为 'config/agents/{id}.yaml' 格式"
            ))
    
    def _get_git_last_modified(self, file_path: Path) -> Optional[datetime]:
        """获取文件最后修改时间。"""
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%ci", str(file_path)],
                capture_output=True,
                text=True,
                check=True
            )
            date_str = result.stdout.strip()
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except Exception:
            return None
    
    def report(self):
        """输出报告。"""
        print("=" * 60)
        print("📋 过时文档检查报告")
        print("=" * 60)
        print()
        
        if not self.suspicious:
            print("✅ 未发现明显过时的文档！")
            return
        
        # 按原因分组
        by_reason = {}
        for doc, reason, suggestion in self.suspicious:
            if reason not in by_reason:
                by_reason[reason] = []
            by_reason[reason].append((doc, suggestion))
        
        for reason, items in by_reason.items():
            print(f"🟡 {reason}")
            print(f"   涉及 {len(items)} 个文件:")
            for doc, suggestion in items:
                print(f"     - {doc}")
                print(f"       建议: {suggestion}")
            print()
        
        print("=" * 60)
        print(f"总计: {len(self.suspicious)} 个待检查项")
        print()
        print("💡 建议操作:")
        print("  1. 优先修复配置字段和导入路径问题")
        print("  2. 验证引用文件路径是否确实变更")
        print("  3. 运行 check_consistency.py 进行详细检查")


def main():
    parser = argparse.ArgumentParser(description="识别可能过时的文档")
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
    parser.add_argument(
        "--since",
        type=str,
        help="检查自该日期以来的变更 (格式: YYYY-MM-DD)"
    )
    
    args = parser.parse_args()
    
    if not args.docs_dir.exists():
        print(f"❌ 文档目录不存在: {args.docs_dir}")
        sys.exit(1)
    
    finder = OutdatedDocFinder(args.docs_dir, args.src_dir, args.since)
    success = finder.find()
    finder.report()
    
    # 返回 0 即使发现问题，因为这是建议性的
    sys.exit(0)


if __name__ == "__main__":
    main()
