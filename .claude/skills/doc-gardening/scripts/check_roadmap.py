#!/usr/bin/env python3
"""
验证 Roadmap 结构规范。

检查项：
1. README.md 存在且包含当前里程碑状态
2. 里程碑文件使用统一命名格式（milestone-N-*.md）
3. 状态标记一致性（✅🔄⏳🗂️）
4. 已完成的里程碑已归档到 completed/
5. 无孤立里程碑文件
6. 当前里程碑进度可追踪
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class MilestoneInfo:
    """里程碑信息。"""
    name: str
    path: Path
    status: str  # ✅ 🔄 ⏳ 🗂️
    progress: Tuple[int, int] = (0, 0)  # (完成数, 总数)
    risks: List[str] = field(default_factory=list)


class RoadmapChecker:
    """Roadmap 结构检查器。"""

    STATUS_COMPLETED = "✅"
    STATUS_IN_PROGRESS = "🔄"
    STATUS_PLANNED = "⏳"
    STATUS_ARCHIVED = "🗂️"

    def __init__(self, roadmap_dir: Path):
        self.roadmap_dir = roadmap_dir
        self.issues: List[Tuple[str, str]] = []
        self.milestones: List[MilestoneInfo] = []
        self.readme_content: str = ""

    def check(self) -> bool:
        """运行所有检查。"""
        if not self.roadmap_dir.exists():
            self.issues.append(("存在性", f"Roadmap 目录不存在: {self.roadmap_dir}"))
            return False

        print(f"🔍 检查 Roadmap 目录: {self.roadmap_dir}")
        print()

        self._check_readme()
        self._check_milestones()
        self._check_archived()
        self._check_orphans()

        return len(self.issues) == 0

    def _check_readme(self):
        """检查 README.md。"""
        readme_path = self.roadmap_dir / "README.md"

        if not readme_path.exists():
            self.issues.append(("README", "README.md 不存在"))
            return

        self.readme_content = readme_path.read_text(encoding='utf-8')

        # 检查是否包含当前里程碑状态
        current_patterns = ['当前里程碑', 'Current Milestone', '🔄', '进行中']
        has_current = any(p in self.readme_content for p in current_patterns)

        if not has_current:
            self.issues.append(
                ("README", "README.md 未包含当前里程碑状态（使用 🔄 或'当前里程碑'标记）")
            )
        else:
            print("✅ README.md: 包含当前里程碑状态")

    def _check_milestones(self):
        """检查里程碑文件。"""
        milestone_files = list(self.roadmap_dir.glob("milestone-*.md"))

        if not milestone_files:
            print("⚠️ 未发现里程碑文件（milestone-*.md）")
            return

        print(f"📄 发现 {len(milestone_files)} 个里程碑文件")

        for ms_path in sorted(milestone_files):
            info = self._parse_milestone(ms_path)
            self.milestones.append(info)

    def _parse_milestone(self, path: Path) -> MilestoneInfo:
        """解析里程碑文件。"""
        content = path.read_text(encoding='utf-8')

        # 提取状态
        status = self.STATUS_PLANNED
        if self.STATUS_COMPLETED in content[:500]:
            status = self.STATUS_COMPLETED
        elif self.STATUS_IN_PROGRESS in content[:500]:
            status = self.STATUS_IN_PROGRESS
        elif self.STATUS_ARCHIVED in content[:500]:
            status = self.STATUS_ARCHIVED

        # 解析进度（从任务列表）
        completed = len(re.findall(r'^[\s]*-[\s]*\[[xX]\]', content, re.MULTILINE))
        total = len(re.findall(r'^[\s]*-[\s]*\[[\s]\]', content, re.MULTILINE)) + completed

        # 查找风险点
        risks = []
        risk_section = re.search(
            r'##?\s*风险|##?\s*Risk.*?(?=##|\Z)',
            content,
            re.DOTALL | re.IGNORECASE
        )
        if risk_section:
            risk_lines = re.findall(r'^[\s]*-[\s]+(.+)$', risk_section.group(), re.MULTILINE)
            risks = [r.strip() for r in risk_lines if r.strip()]

        # 检查命名格式
        name = path.stem
        if not re.match(r'milestone-\d+-.+', name):
            self.issues.append(
                ("命名", f"{name}.md 不符合命名规范（应为 milestone-N-*.md）")
            )

        return MilestoneInfo(
            name=name,
            path=path,
            status=status,
            progress=(completed, total),
            risks=risks
        )

    def _check_archived(self):
        """检查已完成里程碑的归档。"""
        completed_dir = self.roadmap_dir / "completed"

        completed_ms = [m for m in self.milestones if m.status == self.STATUS_COMPLETED]

        for ms in completed_ms:
            archived_path = completed_dir / f"{ms.name}.md"
            if not archived_path.exists():
                self.issues.append(
                    ("归档", f"已完成的里程碑 {ms.name}.md 未归档到 completed/")
                )

        if completed_dir.exists():
            archived = list(completed_dir.glob("milestone-*.md"))
            if archived:
                print(f"✅ 归档目录: {len(archived)} 个已归档里程碑")

    def _check_orphans(self):
        """检查孤立的里程碑文件。"""
        # 所有在 roadmap 目录下的 md 文件
        all_md = set(self.roadmap_dir.rglob("*.md"))
        # 排除 README 和已知的里程碑文件
        expected = {self.roadmap_dir / "README.md"}
        for ms in self.milestones:
            expected.add(ms.path)
            expected.add(self.roadmap_dir / "completed" / f"{ms.name}.md")

        orphans = all_md - expected

        for orphan in orphans:
            if orphan.name != "README.md":
                self.issues.append(
                    ("孤立文件", f"{orphan.relative_to(self.roadmap_dir)} 不是标准里程碑文件")
                )

    def report(self):
        """输出报告。"""
        print()
        print("=" * 60)
        print("📋 Roadmap 结构检查报告")
        print("=" * 60)
        print()

        # 里程碑概览
        if self.milestones:
            completed = sum(1 for m in self.milestones if m.status == self.STATUS_COMPLETED)
            in_progress = sum(1 for m in self.milestones if m.status == self.STATUS_IN_PROGRESS)
            planned = sum(1 for m in self.milestones if m.status == self.STATUS_PLANNED)

            print(f"里程碑总览: {len(self.milestones)} 个")
            print(f"  {self.STATUS_COMPLETED} 已完成: {completed}")
            print(f"  {self.STATUS_IN_PROGRESS} 进行中: {in_progress}")
            print(f"  {self.STATUS_PLANNED} 待开始: {planned}")
            print()

            # 当前里程碑详情
            current = [m for m in self.milestones if m.status == self.STATUS_IN_PROGRESS]
            if current:
                print("当前里程碑:")
                for ms in current:
                    done, total = ms.progress
                    pct = (done / total * 100) if total > 0 else 0
                    print(f"  {ms.name}")
                    print(f"    进度: {done}/{total} ({pct:.0f}%)")
                    if ms.risks:
                        print(f"    风险: {len(ms.risks)} 个")
                        for risk in ms.risks[:3]:
                            print(f"      • {risk[:50]}...")
                print()

        # 问题报告
        if self.issues:
            print("-" * 60)
            print(f"发现 {len(self.issues)} 个问题:")
            for category, detail in self.issues:
                print(f"  [{category}] {detail}")
            print()
            print("💡 建议操作:")
            print("  1. 确保 README.md 包含当前里程碑状态")
            print("  2. 使用统一命名格式: milestone-N-*.md")
            print("  3. 已完成的里程碑移至 completed/ 目录")
            print("  4. 删除或重命名非标准里程碑文件")
        else:
            print("✅ Roadmap 结构符合规范！")


def main():
    parser = argparse.ArgumentParser(description="验证 Roadmap 结构规范")
    parser.add_argument(
        "--roadmap-dir",
        type=Path,
        default=Path("./docs/roadmap"),
        help="Roadmap 目录路径 (默认: ./docs/roadmap)"
    )

    args = parser.parse_args()

    checker = RoadmapChecker(args.roadmap_dir)
    success = checker.check()
    checker.report()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
