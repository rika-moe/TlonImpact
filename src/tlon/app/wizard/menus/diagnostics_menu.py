"""Configuration diagnostics menu.

Interactive menu for running diagnostics and viewing
configuration health reports.
"""

from typing import TYPE_CHECKING

from ..diagnostics import ConfigDiagnostics, IssueSeverity

if TYPE_CHECKING:
    from rich.console import Console


class DiagnosticsMenu:
    """Menu for configuration diagnostics."""

    def __init__(
        self,
        console: "Console",
        positions_dir: str | None = None,
        agents_dir: str | None = None,
    ):
        """Initialize the diagnostics menu.

        Args:
            console: Rich console for output
            positions_dir: Directory for position configs (caller should pass explicit path)
            agents_dir: Directory for agent configs (caller should pass explicit path)
        """
        self.console = console
        self.diagnostics = ConfigDiagnostics(positions_dir, agents_dir)

        try:
            import questionary

            self.questionary = questionary
        except ImportError:
            self.questionary = None

    def show(self) -> None:
        """Show the diagnostics menu."""
        if not self.questionary:
            self.console.print(
                "[red]Wizard requires 'questionary'. Install with: pip install questionary[/red]"
            )
            return

        while True:
            self.console.print("\n[bold]🔍 配置诊断[/bold]\n")

            choice = self.questionary.select(
                "请选择操作:",
                choices=[
                    self.questionary.Choice("🔍 运行完整诊断", value="run"),
                    self.questionary.Choice("📊 查看引用统计", value="stats"),
                    self.questionary.Separator(),
                    self.questionary.Choice("← 返回", value="back"),
                ],
            ).ask()

            if choice == "run":
                self.run_diagnostics()
            elif choice == "stats":
                self.show_stats()
            else:
                break

    def run_diagnostics(self) -> None:
        """Run full diagnostics and display report."""
        self.console.print(
            "\n[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]"
        )
        self.console.print("[bold cyan]🔍 配置诊断[/bold cyan]")
        self.console.print(
            "[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]\n"
        )

        self.console.print("[dim]正在运行诊断...[/dim]\n")

        report = self.diagnostics.run_all_checks()

        # Display results by category
        self._display_issues(report)

        # Summary
        self.console.print("\n[bold]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold]")

        if report.has_errors():
            self.console.print(
                f"[bold red]诊断结果: ✗ 发现 {len(report.get_errors())} 个错误[/bold red]"
            )
        elif report.has_warnings():
            self.console.print(f"[bold yellow]诊断结果: ⚠ {report.get_summary()}[/bold yellow]")
        else:
            self.console.print("[bold green]诊断结果: ✅ 所有检查通过[/bold green]")

        self.console.print("[bold]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold]")

        self.questionary.press_any_key_to_continue("\n按回车继续...").ask()

    def _display_issues(self, report) -> None:
        """Display issues from report.

        Args:
            report: DiagnosticReport instance
        """

        # Group issues by category
        errors_by_cat = {}
        warnings_by_cat = {}
        infos_by_cat = {}

        for issue in report.issues:
            if issue.severity == IssueSeverity.ERROR:
                errors_by_cat.setdefault(issue.category, []).append(issue)
            elif issue.severity == IssueSeverity.WARNING:
                warnings_by_cat.setdefault(issue.category, []).append(issue)
            else:
                infos_by_cat.setdefault(issue.category, []).append(issue)

        # Display errors
        if errors_by_cat:
            self.console.print("[bold red]✗ 错误[/bold red]\n")
            for category, issues in sorted(errors_by_cat.items()):
                self.console.print(f"[bold]{category}[/bold]")
                for issue in issues:
                    self.console.print(f"  • {issue.message}")
                    if issue.file_path:
                        self.console.print(f"    [dim]{issue.file_path}[/dim]")
                    if issue.details:
                        self.console.print(f"    [dim]{issue.details}[/dim]")
                self.console.print()

        # Display warnings
        if warnings_by_cat:
            self.console.print("[bold yellow]⚠ 警告[/bold yellow]\n")
            for category, issues in sorted(warnings_by_cat.items()):
                self.console.print(f"[bold]{category}[/bold]")
                for issue in issues:
                    self.console.print(f"  • {issue.message}")
                    if issue.file_path:
                        self.console.print(f"    [dim]{issue.file_path}[/dim]")
                self.console.print()

        # Display info
        if infos_by_cat:
            self.console.print("[bold blue]ℹ 信息[/bold blue]\n")
            for category, issues in sorted(infos_by_cat.items()):
                self.console.print(f"[bold]{category}[/bold]")
                for issue in issues:
                    self.console.print(f"  • {issue.message}")
                    if issue.file_path:
                        self.console.print(f"    [dim]{issue.file_path}[/dim]")
                self.console.print()

        if not report.issues:
            self.console.print("[green]未发现任何问题[/green]\n")

    def show_stats(self) -> None:
        """Show position usage statistics."""
        from rich.table import Table

        self.console.print("\n[bold]📊 岗位引用统计[/bold]\n")

        stats = self.diagnostics.get_position_usage_stats()

        if not stats:
            self.console.print("[yellow]暂无 Agent 配置[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        # Load positions for names
        from ..position_store import PositionStore

        position_store = PositionStore()
        positions = position_store.load_all_positions()

        table = Table()
        table.add_column("岗位标识", style="cyan")
        table.add_column("岗位名称", style="green")
        table.add_column("Agent 数量", style="yellow")

        for pos_type, count in sorted(stats.items()):
            position = positions.get(pos_type)
            title = position.title if position else pos_type
            table.add_row(pos_type, title, str(count))

        self.console.print(table)
        self.console.print(f"\n[dim]共 {len(stats)} 个岗位被引用[/dim]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()
