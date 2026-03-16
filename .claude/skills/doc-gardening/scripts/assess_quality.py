#!/usr/bin/env python3
"""
评估文档质量并追踪差距。

评估维度：
1. 完整性 - 是否包含必要章节
2. 时效性 - 验证状态标记
3. 可访问性 - 链接有效性
4. 一致性 - 术语和代码同步

输出：各领域评分和差距报告
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class DomainScore:
    """领域评分。"""
    name: str
    completeness: int = 0      # 完整性 0-100
    freshness: int = 0         # 时效性 0-100
    accessibility: int = 0     # 可访问性 0-100
    consistency: int = 0       # 一致性 0-100
    gaps: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (self.completeness + self.freshness +
                self.accessibility + self.consistency) // 4


class QualityAssessor:
    """文档质量评估器。"""

    # 设计文档必须包含的章节
    REQUIRED_SECTIONS = [
        "核心理念",
        "文档索引",
        "验证清单",
    ]

    # 状态标记
    STATUS_VERIFIED = "✅"
    STATUS_PENDING = "⚠️"
    STATUS_OUTDATED = "❌"

    def __init__(self, design_dir: Path, agents_path: Path):
        self.design_dir = design_dir
        self.agents_path = agents_path
        self.scores: Dict[str, DomainScore] = {}

    def assess(self) -> Dict[str, DomainScore]:
        """评估所有设计文档。"""
        print(f"🔍 评估设计文档目录: {self.design_dir}")
        print()

        if not self.design_dir.exists():
            print(f"⚠️ 设计文档目录不存在: {self.design_dir}")
            return self.scores

        design_files = list(self.design_dir.glob("*.md"))

        for doc_path in design_files:
            score = self._assess_single(doc_path)
            self.scores[score.name] = score

        return self.scores

    def _assess_single(self, doc_path: Path) -> DomainScore:
        """评估单个设计文档。"""
        name = doc_path.stem
        score = DomainScore(name=name)

        try:
            content = doc_path.read_text(encoding='utf-8')
        except Exception as e:
            score.gaps.append(f"无法读取: {e}")
            return score

        # 1. 完整性评估
        score.completeness = self._assess_completeness(content)

        # 2. 时效性评估
        score.freshness = self._assess_freshness(content)

        # 3. 可访问性评估
        score.accessibility = self._assess_accessibility(content, doc_path)

        # 4. 一致性评估（基础检查）
        score.consistency = self._assess_consistency(content)

        # 识别差距
        self._identify_gaps(score, content)

        return score

    def _assess_completeness(self, content: str) -> int:
        """评估完整性。"""
        score = 100
        missing = []

        for section in self.REQUIRED_SECTIONS:
            if section not in content:
                missing.append(section)
                score -= 25

        # 检查是否有核心理念
        if "## 核心理念" not in content and "# 核心理念" not in content:
            score -= 20

        return max(0, score)

    def _assess_freshness(self, content: str) -> int:
        """评估时效性。"""
        # 查找验证状态标记
        if self.STATUS_VERIFIED in content:
            return 100
        elif self.STATUS_PENDING in content:
            return 60
        elif self.STATUS_OUTDATED in content:
            return 20
        else:
            # 无状态标记，视为未知
            return 40

    def _assess_accessibility(self, content: str, doc_path: Path) -> int:
        """评估可访问性（链接有效性）。"""
        links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)

        broken = 0
        total = 0

        for text, link in links:
            # 忽略外部链接和锚点
            if link.startswith(('http://', 'https://', '#', 'mailto:')):
                continue

            total += 1
            resolved = self._resolve_link(doc_path, link)
            if resolved and not resolved.exists():
                broken += 1

        if total == 0:
            return 100

        return max(0, 100 - (broken * 20))

    def _assess_consistency(self, content: str) -> int:
        """评估一致性（基础术语检查）。"""
        score = 100

        # 检查废弃术语
        deprecated_terms = {
            "Minions": "应使用 Tlon",
        }

        for term, suggestion in deprecated_terms.items():
            if term in content:
                score -= 30

        return max(0, score)

    def _identify_gaps(self, score: DomainScore, content: str):
        """识别具体差距。"""
        # 检查缺失的章节
        for section in self.REQUIRED_SECTIONS:
            if section not in content:
                score.gaps.append(f"缺少章节: {section}")

        # 检查验证状态
        if self.STATUS_VERIFIED not in content:
            if self.STATUS_PENDING in content:
                score.gaps.append("状态: 待验证")
            elif self.STATUS_OUTDATED in content:
                score.gaps.append("状态: 已过期，需更新")
            else:
                score.gaps.append("状态: 未标记验证状态")

        # 检查核心理念数量
        core_ideas = re.findall(r'^\s*[\d\-\*]\s+.+$', content, re.MULTILINE)
        if len(core_ideas) < 3:
            score.gaps.append(f"核心理念过少 ({len(core_ideas)} 条，建议 3-5 条)")

    def _resolve_link(self, from_file: Path, link: str) -> Optional[Path]:
        """解析链接为绝对路径。"""
        if '#' in link:
            link = link.split('#')[0]

        if not link:
            return None

        if link.startswith('./') or link.startswith('../'):
            return (from_file.parent / link).resolve()

        if link.startswith('/'):
            return (self.design_dir.parent / link.lstrip('/')).resolve()

        return (from_file.parent / link).resolve()

    def report(self):
        """输出报告。"""
        print("=" * 70)
        print("📊 文档质量评估报告")
        print("=" * 70)
        print()

        if not self.scores:
            print("⚠️ 没有可评估的设计文档")
            print("   期望位置: docs/design/*.md")
            return

        # 表头
        print(f"{'领域':<15} {'总分':>6} {'完整':>6} {'时效':>6} {'访问':>6} {'一致':>6}  {'状态'}")
        print("-" * 70)

        total_score = 0

        for name, score in sorted(self.scores.items()):
            total_score += score.total

            # 确定状态标记
            if score.total >= 80:
                status = "✅"
            elif score.total >= 50:
                status = "⚠️"
            else:
                status = "❌"

            print(f"{name:<15} {score.total:>6} {score.completeness:>6} "
                  f"{score.freshness:>6} {score.accessibility:>6} "
                  f"{score.consistency:>6}  {status}")

        avg_score = total_score // len(self.scores) if self.scores else 0
        print("-" * 70)
        print(f"{'总体健康度':<15} {avg_score:>6}%")
        print()

        # 差距详情
        print("=" * 70)
        print("📋 差距详情")
        print("=" * 70)
        print()

        for name, score in sorted(self.scores.items()):
            if score.gaps:
                print(f"【{name}】")
                for gap in score.gaps[:5]:  # 最多显示5个差距
                    print(f"  • {gap}")
                if len(score.gaps) > 5:
                    print(f"  ... 还有 {len(score.gaps) - 5} 项")
                print()

        # 建议
        print("=" * 70)
        print("💡 优先修复建议")
        print("=" * 70)
        print()

        low_score_domains = [
            (n, s) for n, s in self.scores.items()
            if s.total < 60
        ]

        if low_score_domains:
            print("低分领域（优先修复）:")
            for name, score in sorted(low_score_domains, key=lambda x: x[1].total):
                print(f"  • {name}: {score.total}/100 - {score.gaps[0] if score.gaps else '无具体差距'}")
        else:
            print("✅ 所有领域评分良好（>=60）")

    def export_json(self, output_path: Path):
        """导出 JSON 报告。"""
        data = {
            "domains": {
                name: {
                    "total": score.total,
                    "completeness": score.completeness,
                    "freshness": score.freshness,
                    "accessibility": score.accessibility,
                    "consistency": score.consistency,
                    "gaps": score.gaps,
                }
                for name, score in self.scores.items()
            },
            "summary": {
                "total_domains": len(self.scores),
                "average_score": (
                    sum(s.total for s in self.scores.values()) // len(self.scores)
                    if self.scores else 0
                ),
            }
        }

        output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"📄 JSON 报告已保存: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="评估文档质量并追踪差距")
    parser.add_argument(
        "--design-dir",
        type=Path,
        default=Path("./docs/design"),
        help="设计文档目录 (默认: ./docs/design)"
    )
    parser.add_argument(
        "--agents-path",
        type=Path,
        default=Path("./AGENTS.md"),
        help="AGENTS.md 路径 (默认: ./AGENTS.md)"
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="导出 JSON 报告的路径"
    )

    args = parser.parse_args()

    assessor = QualityAssessor(args.design_dir, args.agents_path)
    assessor.assess()
    assessor.report()

    if args.json_output:
        assessor.export_json(args.json_output)

    sys.exit(0)


if __name__ == "__main__":
    main()
