"""Position management menu.

Interactive menu for managing Position configurations
with CRUD operations and template support.
"""

from typing import TYPE_CHECKING

from ..position_store import PositionStore

if TYPE_CHECKING:
    from rich.console import Console


class PositionMenu:
    """Menu for position management."""

    def __init__(self, console: "Console", positions_dir: str = "config/positions"):
        """Initialize the position menu.

        Args:
            console: Rich console for output
            positions_dir: Directory for position configs
        """
        self.console = console
        self.store = PositionStore(positions_dir)

        # Import questionary here for optional dependency
        try:
            import questionary

            self.questionary = questionary
        except ImportError:
            self.questionary = None

    def show(self) -> None:
        """Show the position management menu."""
        if not self.questionary:
            self.console.print(
                "[red]Wizard requires 'questionary'. Install with: pip install questionary[/red]"
            )
            return

        while True:
            self.console.print("\n[bold]🏢 岗位管理[/bold]\n")

            choice = self.questionary.select(
                "请选择操作:",
                choices=[
                    self.questionary.Choice("📋 列出所有岗位", value="list"),
                    self.questionary.Choice("🔍 查看岗位详情", value="view"),
                    self.questionary.Choice("➕ 创建岗位", value="create"),
                    self.questionary.Choice("✏️  编辑岗位", value="edit"),
                    self.questionary.Choice("🗑️  删除岗位", value="delete"),
                    self.questionary.Separator(),
                    self.questionary.Choice("← 返回", value="back"),
                ],
            ).ask()

            if choice == "list":
                self.list_positions()
            elif choice == "view":
                self.view_position()
            elif choice == "create":
                self.create_position()
            elif choice == "edit":
                self.edit_position()
            elif choice == "delete":
                self.delete_position()
            else:
                break

    def list_positions(self) -> None:
        """List all positions in a table."""
        from rich.table import Table

        positions = self.store.load_all_positions()

        if not positions:
            self.console.print("[yellow]暂无岗位配置[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        table = Table(title="岗位列表")
        table.add_column("标识", style="cyan")
        table.add_column("名称", style="green")
        table.add_column("部门", style="yellow")
        table.add_column("启动模式", style="blue")

        for _pos_type, pos in sorted(positions.items()):
            mode = "常驻" if pos.proactive else "延迟"
            table.add_row(
                pos.type,
                pos.title,
                pos.department or "-",
                mode,
            )

        self.console.print(table)
        self.console.print(f"\n[dim]共 {len(positions)} 个岗位[/dim]")
        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def view_position(self) -> None:
        """View position details."""
        positions = self.store.load_all_positions()

        if not positions:
            self.console.print("[yellow]暂无岗位可查看[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        # Select position
        choices = [
            self.questionary.Choice(f"{p.title} ({p.type})", value=p.type)
            for p in sorted(positions.values(), key=lambda x: x.title)
        ]
        choices.append(self.questionary.Choice("← 返回", value="back"))

        pos_type = self.questionary.select("选择岗位:", choices=choices).ask()

        if pos_type == "back":
            return

        pos = positions.get(pos_type)
        if not pos:
            return

        # Display details
        from rich.panel import Panel

        mode = "常驻启动" if pos.proactive else "延迟启动"

        content = f"""[bold]基本信息[/bold]
标识: {pos.type}
名称: {pos.title}
部门: {pos.department or "-"}
启动模式: {mode}

[bold]职责描述 (JD)[/bold]
{pos.jd}

[bold]协作规则[/bold]
"""
        if pos.rules:
            for i, rule in enumerate(pos.rules, 1):
                content += f"{i}. {rule}\n"
        else:
            content += "暂无规则\n"

        if pos.prompt_template:
            content += f"\n[bold]Prompt 模板[/bold]\n[dim]{pos.prompt_template[:200]}...[/dim]"

        self.console.print(Panel(content, title=f"岗位详情: {pos.title}"))
        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def create_position(self) -> None:
        """Create a new position."""
        self.console.print("\n[bold]➕ 创建新岗位[/bold]\n")

        # Choose creation method
        method = self.questionary.select(
            "选择创建方式:",
            choices=[
                self.questionary.Choice("从预设模板创建", value="template"),
                self.questionary.Choice("完全自定义", value="custom"),
                self.questionary.Choice("← 取消", value="cancel"),
            ],
        ).ask()

        if method == "cancel":
            return

        if method == "template":
            self._create_from_template()
        else:
            self._create_custom()

    def _create_from_template(self) -> None:
        """Create position from template."""
        templates = self.store.get_default_templates()

        choices = [
            self.questionary.Choice(f"{t['title']} - {t['jd'][:30]}...", value=key)
            for key, t in sorted(templates.items(), key=lambda x: x[1]["title"])
        ]
        choices.append(self.questionary.Choice("← 返回", value="back"))

        template_key = self.questionary.select("选择模板:", choices=choices).ask()

        if template_key == "back":
            return

        # Load template and allow customization
        template = templates.get(template_key, {})

        self.console.print(f"\n[bold]基于模板: {template.get('title')}[/bold]\n")

        # Allow customization of key fields
        pos_type = self.questionary.text(
            "岗位标识 (type):",
            default=template.get("type", ""),
        ).ask()

        if not pos_type:
            self.console.print("[red]岗位标识不能为空[/red]")
            return

        # Check if exists
        if self.store.position_exists(pos_type):
            self.console.print(f"[red]岗位 '{pos_type}' 已存在[/red]")
            return

        title = self.questionary.text(
            "显示名称 (title):",
            default=template.get("title", ""),
        ).ask()

        department = self.questionary.text(
            "部门:",
            default=template.get("department", ""),
        ).ask()

        proactive = self.questionary.confirm(
            "常驻启动?",
            default=template.get("proactive", False),
        ).ask()

        # Create position
        from tlon.organization.positions.models import Position

        position = Position(
            type=pos_type,
            title=title or pos_type,
            jd=template.get("jd", ""),
            department=department,
            proactive=proactive,
            rules=template.get("rules", []),
            prompt_template=template.get("prompt_template"),
        )

        # Save
        success, msg = self.store.save_position(position)
        if success:
            self.console.print(f"[green]✓ {msg}[/green]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def _create_custom(self) -> None:
        """Create custom position from scratch."""
        # Basic info
        pos_type = self.questionary.text("岗位标识 (type):").ask()

        if not pos_type:
            self.console.print("[red]岗位标识不能为空[/red]")
            return

        # Validate type format
        if not pos_type.replace("_", "").isalnum():
            self.console.print("[red]岗位标识只能包含字母、数字和下划线[/red]")
            return

        if self.store.position_exists(pos_type):
            self.console.print(f"[red]岗位 '{pos_type}' 已存在[/red]")
            return

        title = self.questionary.text("显示名称 (title):").ask()
        if not title:
            self.console.print("[red]显示名称不能为空[/red]")
            return

        department = self.questionary.text("部门:").ask()

        proactive = self.questionary.confirm(
            "常驻启动?",
            default=False,
        ).ask()

        # JD
        self.console.print("\n[bold]职责描述 (JD)[/bold]")
        jd = self.questionary.text(
            "请输入职责描述:",
            multiline=True,
        ).ask()

        if not jd:
            self.console.print("[red]职责描述不能为空[/red]")
            return

        # Rules
        rules = self._edit_rules([])

        # Create and save
        from tlon.organization.positions.models import Position

        position = Position(
            type=pos_type,
            title=title,
            jd=jd,
            department=department,
            proactive=proactive,
            rules=rules,
        )

        success, msg = self.store.save_position(position)
        if success:
            self.console.print(f"[green]✓ {msg}[/green]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def _edit_rules(self, current_rules: list[str]) -> list[str]:
        """Interactive rule editor.

        Args:
            current_rules: Current list of rules

        Returns:
            Updated list of rules
        """
        rules = list(current_rules)

        while True:
            self.console.print("\n[bold]当前规则:[/bold]")
            if rules:
                for i, rule in enumerate(rules, 1):
                    self.console.print(f"  {i}. {rule}")
            else:
                self.console.print("  (暂无规则)")

            action = self.questionary.select(
                "操作:",
                choices=[
                    self.questionary.Choice("➕ 添加规则", value="add"),
                    self.questionary.Choice("🗑️  删除规则", value="remove" if rules else None),
                    self.questionary.Choice("✓ 完成", value="done"),
                ],
            ).ask()

            if action == "add":
                rule = self.questionary.text("请输入规则:").ask()
                if rule:
                    rules.append(rule)
            elif action == "remove":
                choices = [self.questionary.Choice(rule, value=i) for i, rule in enumerate(rules)]
                idx = self.questionary.select("选择要删除的规则:", choices=choices).ask()
                if idx is not None:
                    rules.pop(idx)
            else:
                break

        return rules

    def edit_position(self) -> None:
        """Edit an existing position."""
        positions = self.store.load_all_positions()

        if not positions:
            self.console.print("[yellow]暂无岗位可编辑[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        # Select position
        choices = [
            self.questionary.Choice(f"{p.title} ({p.type})", value=p.type)
            for p in sorted(positions.values(), key=lambda x: x.title)
        ]
        choices.append(self.questionary.Choice("← 返回", value="back"))

        pos_type = self.questionary.select("选择要编辑的岗位:", choices=choices).ask()

        if pos_type == "back":
            return

        pos = positions.get(pos_type)
        if not pos:
            return

        self.console.print(f"\n[bold]✏️  编辑岗位: {pos.title}[/bold]\n")

        # Warning about impact
        self.console.print("[yellow]⚠️  修改岗位会影响所有使用该岗位的 Agent[/yellow]\n")

        # Edit fields
        pos.title = (
            self.questionary.text(
                "显示名称:",
                default=pos.title,
            ).ask()
            or pos.title
        )

        pos.department = (
            self.questionary.text(
                "部门:",
                default=pos.department,
            ).ask()
            or ""
        )

        pos.proactive = self.questionary.confirm(
            "常驻启动?",
            default=pos.proactive,
        ).ask()

        # Edit JD
        new_jd = self.questionary.text(
            "职责描述:",
            default=pos.jd,
            multiline=True,
        ).ask()
        if new_jd:
            pos.jd = new_jd

        # Edit rules
        self.console.print("\n[bold]编辑协作规则[/bold]")
        pos.rules = self._edit_rules(pos.rules)

        # Save
        success, msg = self.store.save_position(pos)
        if success:
            self.console.print(f"[green]✓ {msg}[/green]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def delete_position(self) -> None:
        """Delete a position."""
        # Need agent store to check references
        from ..agent_store import AgentStore

        agent_store = AgentStore()
        positions = self.store.load_all_positions()

        if not positions:
            self.console.print("[yellow]暂无岗位可删除[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        # Select position
        choices = []
        for p in sorted(positions.values(), key=lambda x: x.title):
            # Get usage count
            count = self.store.get_position_usage_count(p.type, agent_store)
            usage_info = f"[{count}个Agent]" if count > 0 else ""
            choices.append(
                self.questionary.Choice(f"{p.title} ({p.type}) {usage_info}", value=p.type)
            )
        choices.append(self.questionary.Choice("← 返回", value="back"))

        pos_type = self.questionary.select("选择要删除的岗位:", choices=choices).ask()

        if pos_type == "back":
            return

        # Check references
        count = self.store.get_position_usage_count(pos_type, agent_store)
        if count > 0:
            self.console.print(f"[red]❌ 无法删除: {count} 个 Agent 正在使用此岗位[/red]")
            self.console.print("[dim]请先为这些 Agent 更换岗位后再删除[/dim]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        # Confirm
        confirm = self.questionary.text(
            f'确认删除岗位 "{pos_type}"? [输入 "{pos_type}" 确认]:',
        ).ask()

        if confirm != pos_type:
            self.console.print("[yellow]已取消删除[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        # Delete
        success, msg = self.store.delete_position(pos_type)
        if success:
            self.console.print(f"[green]✓ {msg}[/green]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()
