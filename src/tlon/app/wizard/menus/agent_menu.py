"""Agent (personnel) management menu.

Interactive menu for managing Agent configurations with CRUD operations.
Supports IDENTITY.md / SOUL.md / RULES.md file-based persona editing.
"""

from typing import TYPE_CHECKING

from ..agent_store import AgentStore

if TYPE_CHECKING:
    from rich.console import Console


class AgentMenu:
    """Menu for agent (personnel) management."""

    def __init__(
        self,
        console: "Console",
        agents_dir: str | None = None,
    ):
        self.console = console
        self.agent_store = AgentStore()

        try:
            import questionary

            self.questionary = questionary
        except ImportError:
            self.questionary = None

    def show(self) -> None:
        if not self.questionary:
            self.console.print(
                "[red]Wizard requires 'questionary'. Install with: pip install questionary[/red]"
            )
            return

        while True:
            self.console.print("\n[bold]👤 人员管理[/bold]\n")

            choice = self.questionary.select(
                "请选择操作:",
                choices=[
                    self.questionary.Choice("📋 列出所有 Agent", value="list"),
                    self.questionary.Choice("🔍 查看 Agent 详情", value="view"),
                    self.questionary.Choice("➕ 招聘新人", value="create"),
                    self.questionary.Choice("🧬 从 Prototype 创建", value="create_prototype"),
                    self.questionary.Choice("🛡️  规约审批", value="review_staging"),
                    self.questionary.Choice("✏️  编辑 Agent", value="edit"),
                    self.questionary.Choice("🗑️  删除 Agent", value="delete"),
                    self.questionary.Separator(),
                    self.questionary.Choice("← 返回", value="back"),
                ],
            ).ask()

            if choice == "list":
                self.list_agents()
            elif choice == "view":
                self.view_agent()
            elif choice == "create":
                self.create_agent()
            elif choice == "create_prototype":
                self.create_from_prototype()
            elif choice == "review_staging":
                self.review_staging()
            elif choice == "edit":
                self.edit_agent()
            elif choice == "delete":
                self.delete_agent()
            else:
                break

    def list_agents(self) -> None:
        from rich.table import Table

        agents = self.agent_store.load_all_agents()
        if not agents:
            self.console.print("[yellow]暂无 Agent 配置[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        table = Table(title="Agent 列表")
        table.add_column("ID", style="cyan")
        table.add_column("姓名", style="green")
        table.add_column("启动模式", style="yellow")
        table.add_column("模型", style="magenta")

        for _agent_id, agent in sorted(agents.items()):
            mode = "常驻" if agent.proactive else "延迟"
            model = agent.llm.model_name if agent.llm else "default"
            table.add_row(agent.id, agent.name, mode, model)

        self.console.print(table)
        self.console.print(f"\n[dim]共 {len(agents)} 个 Agent[/dim]")
        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def view_agent(self) -> None:
        agents = self.agent_store.load_all_agents()
        if not agents:
            self.console.print("[yellow]暂无 Agent 可查看[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        choices = [
            self.questionary.Choice(f"{a.name} ({a.id})", value=a.id)
            for a in sorted(agents.values(), key=lambda x: x.name)
        ]
        choices.append(self.questionary.Choice("← 返回", value="back"))

        agent_id = self.questionary.select("选择 Agent:", choices=choices).ask()
        if agent_id == "back":
            return

        agent = agents.get(agent_id)
        if not agent:
            return

        from rich.panel import Panel

        def _truncate(text: str, length: int = 200) -> str:
            return (text[:length] + "...") if len(text) > length else text

        store = self.agent_store
        identity = store.read_persona_file(agent_id, "IDENTITY.md")
        soul = store.read_persona_file(agent_id, "SOUL.md")
        rules = store.read_persona_file(agent_id, "RULES.md")

        content = f"""[bold]基本信息[/bold]
ID: {agent.id}
姓名: {agent.name}
启动模式: {"常驻" if agent.proactive else "延迟"}

[bold]身份 (IDENTITY.md)[/bold]
{_truncate(identity) if identity else "[dim](未设置)[/dim]"}

[bold]灵魂 (SOUL.md)[/bold]
{_truncate(soul) if soul else "[dim](未设置)[/dim]"}

[bold]守则 (RULES.md)[/bold]
{_truncate(rules) if rules else "[dim](未设置)[/dim]"}

[bold]LLM 配置[/bold]
模型: {agent.llm.model_name if agent.llm else "default"}
Temperature: {agent.llm.temperature if agent.llm else "default"}

[bold]Docker 配置[/bold]
镜像: {agent.docker.image if agent.docker else "default"}
内存限制: {agent.docker.memory_limit if agent.docker else "default"}
"""

        self.console.print(Panel(content, title=f"Agent 详情: {agent.name}"))
        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def create_agent(self) -> None:
        self.console.print("\n[bold]➕ 招聘新人[/bold]\n")

        # Step 1: Basic info
        self.console.print("[bold]步骤 1/5: 基本信息[/bold]\n")
        name = self.questionary.text("姓名:").ask()
        if not name:
            self.console.print("[red]姓名不能为空[/red]")
            return

        # Step 2: Identity
        self.console.print("\n[bold]步骤 2/5: 身份 (IDENTITY.md)[/bold]\n")
        self.console.print("[dim]你是谁、你的角色定位和核心自我认知。[/dim]\n")
        identity = self.questionary.text("身份描述:", default="").ask() or ""

        # Step 3: Soul
        self.console.print("\n[bold]步骤 3/5: 灵魂 (SOUL.md)[/bold]\n")
        self.console.print("[dim]你的工作部门、职责、性格特质，其他同事会看到这份档案。[/dim]\n")
        soul = self.questionary.text("灵魂/档案描述:", default="").ask() or ""

        # Step 4: Rules
        self.console.print("\n[bold]步骤 4/5: 守则 (RULES.md)[/bold]\n")
        self.console.print("[dim]你的工作流程、响应规则、协作习惯和禁止事项。[/dim]\n")
        rules = self.questionary.text("行为守则:", default="").ask() or ""

        # Step 5: LLM + Docker + Proactive
        self.console.print("\n[bold]步骤 5/5: 运行时配置[/bold]\n")

        models = [
            "kimi-k2.5", "kimi-k2", "gpt-4o", "gpt-4",
            "gpt-4-turbo", "gpt-3.5-turbo", "claude-3-opus", "claude-3-sonnet",
        ]
        model = self.questionary.select("选择模型:", choices=models, default="kimi-k2.5").ask()

        temp_str = self.questionary.text("Temperature [1.0]:", default="1.0").ask()
        try:
            temperature = float(temp_str or "1.0")
        except ValueError:
            temperature = 1.0

        memory = self.questionary.text("内存限制 [512m]:", default="512m").ask()

        proactive = self.questionary.confirm(
            "是否设置为常驻启动?\n常驻: 自动启动，响应所有消息\n延迟: 手动唤醒，只响应@消息",
            default=False,
        ).ask()

        # Generate ID
        existing_ids = list(self.agent_store.load_all_agents().keys())
        prefix = name
        agent_id = self.agent_store.generate_agent_id(prefix, existing_ids)

        # Preview
        self.console.print("\n[bold]预览配置[/bold]\n")
        self.console.print(f"ID: {agent_id}")
        self.console.print(f"姓名: {name}")
        self.console.print(f"启动模式: {'常驻' if proactive else '延迟'}")
        self.console.print(f"模型: {model}")
        if identity:
            self.console.print("[dim]IDENTITY.md: 已填写[/dim]")
        if soul:
            self.console.print("[dim]SOUL.md: 已填写[/dim]")
        if rules:
            self.console.print("[dim]RULES.md: 已填写[/dim]")

        if not self.questionary.confirm("确认创建?").ask():
            self.console.print("[yellow]已取消[/yellow]")
            return

        from tlon.base.config import AgentConfig, DockerRuntimeConfig, LLMConfig

        agent = AgentConfig(
            id=agent_id,
            name=name,
            llm=LLMConfig(model_name=model, temperature=temperature),
            docker=DockerRuntimeConfig(image="ubuntu:22.04", memory_limit=memory or "512m"),
            proactive=proactive,
        )

        success, msg = self.agent_store.save_agent(agent)
        if success:
            self.agent_store.write_behavior_file(agent_id)
            if identity:
                self.agent_store.write_persona_file(agent_id, "IDENTITY.md", identity)
            if soul:
                self.agent_store.write_persona_file(agent_id, "SOUL.md", soul)
            if rules:
                self.agent_store.write_persona_file(agent_id, "RULES.md", rules)
            self.console.print(f"[green]✓ {msg}[/green]")
            self.console.print("[dim]提示: 重启 server 后新 Agent 生效[/dim]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def create_from_prototype(self) -> None:
        """Create an agent by instantiating a behavior prototype."""
        from tlon.agents.prototypes import (
            PrototypeError,
            instantiate,
            list_prototypes,
            seed_prototypes,
        )

        self.console.print("\n[bold]🧬 从 Prototype 创建[/bold]\n")

        # mirror 是独立进程，server 的种子可能尚未运行；幂等，放心调用
        seed_prototypes()

        prototypes = list_prototypes()
        if not prototypes:
            self.console.print("[yellow]暂无可用 Prototype[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        choices = [
            self.questionary.Choice(
                f"{proto.display_name} ({proto.name}) — {proto.description}",
                value=proto.name,
            )
            for proto in prototypes
        ]
        choices.append(self.questionary.Choice("← 返回", value="back"))

        prototype_name = self.questionary.select("选择 Prototype:", choices=choices).ask()
        if not prototype_name or prototype_name == "back":
            return

        name = self.questionary.text("姓名:").ask()
        if not name:
            self.console.print("[red]姓名不能为空[/red]")
            return

        default_id = self.agent_store.generate_agent_id(prototype_name)
        agent_id = self.questionary.text("Agent ID:", default=default_id).ask()
        if not agent_id:
            self.console.print("[yellow]已取消[/yellow]")
            return

        proactive = self.questionary.confirm(
            "是否设置为常驻启动?\n常驻: 自动启动，响应所有消息\n延迟: 手动唤醒，只响应@消息",
            default=False,
        ).ask()

        self.console.print("\n[bold]预览配置[/bold]\n")
        self.console.print(f"Prototype: {prototype_name}")
        self.console.print(f"ID: {agent_id}")
        self.console.print(f"姓名: {name}")
        self.console.print(f"启动模式: {'常驻' if proactive else '延迟'}")

        if not self.questionary.confirm("确认创建?").ask():
            self.console.print("[yellow]已取消[/yellow]")
            return

        try:
            agent = instantiate(
                prototype_name,
                agent_id=agent_id,
                agent_name=name,
                proactive=proactive,
            )
        except PrototypeError as e:
            self.console.print(f"[red]✗ 创建失败: {e}[/red]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        self.console.print(f"[green]✓ 已从 Prototype '{prototype_name}' 创建 Agent: {agent.name}[/green]")
        self.console.print("[dim]提示: 重启 server 后新 Agent 生效[/dim]")
        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def review_staging(self) -> None:
        """Approve or reject LLM-authored prototypes in the staging area."""
        from tlon.agents.prototypes import (
            BEHAVIOR_FILE,
            PERSONA_FILES,
            PrototypeError,
            approve_prototype,
            list_staging_prototypes,
            reject_prototype,
        )
        from tlon.base.paths import paths

        self.console.print("\n[bold]🛡️  规约审批[/bold]\n")
        self.console.print(
            "[dim]Agent 创作的规约先进入 staging 区（不可信），批准后才能被 spawn。[/dim]\n"
        )

        staged = list_staging_prototypes()
        if not staged:
            self.console.print("[yellow]staging 区暂无待审批的规约[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        choices = [
            self.questionary.Choice(
                f"{proto.display_name} ({proto.name})"
                f" — 作者: {proto.author or '未知'} — {proto.description}",
                value=proto.name,
            )
            for proto in staged
        ]
        choices.append(self.questionary.Choice("← 返回", value="back"))

        name = self.questionary.select("选择待审批的规约:", choices=choices).ask()
        if not name or name == "back":
            return

        # Preview behavior spec and persona files before deciding
        from rich.panel import Panel

        staging_dir = paths.prototypes_staging_dir() / name
        behavior = (staging_dir / BEHAVIOR_FILE).read_text(encoding="utf-8")
        self.console.print(Panel(behavior, title=f"behavior.tlon: {name}"))
        for filename in PERSONA_FILES:
            persona_file = staging_dir / filename
            if persona_file.exists():
                content = persona_file.read_text(encoding="utf-8")
                self.console.print(Panel(content, title=filename))

        decision = self.questionary.select(
            "审批决定:",
            choices=[
                self.questionary.Choice("✓ 批准（转为可信，可 spawn）", value="approve"),
                self.questionary.Choice("✗ 拒绝（删除该规约）", value="reject"),
                self.questionary.Choice("← 返回", value="back"),
            ],
        ).ask()

        try:
            if decision == "approve":
                info = approve_prototype(name)
                self.console.print(
                    f"[green]✓ 已批准: {info.display_name} ({info.name}) 转为可信[/green]"
                )
            elif decision == "reject":
                if self.questionary.confirm(f"确认删除规约 '{name}'?", default=False).ask():
                    reject_prototype(name)
                    self.console.print(f"[green]✓ 已拒绝并删除: {name}[/green]")
                else:
                    self.console.print("[yellow]已取消[/yellow]")
        except PrototypeError as e:
            self.console.print(f"[red]✗ 操作失败: {e}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def edit_agent(self) -> None:
        agents = self.agent_store.load_all_agents()
        if not agents:
            self.console.print("[yellow]暂无 Agent 可编辑[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        choices = [
            self.questionary.Choice(f"{a.name} ({a.id})", value=a.id)
            for a in sorted(agents.values(), key=lambda x: x.name)
        ]
        choices.append(self.questionary.Choice("← 返回", value="back"))

        agent_id = self.questionary.select("选择要编辑的 Agent:", choices=choices).ask()
        if agent_id == "back":
            return

        agent = agents.get(agent_id)
        if not agent:
            return

        store = self.agent_store

        def _preview(text: str, length: int = 30) -> str:
            return (text[:length] + "...") if len(text) > length else (text or "-")

        # Load current persona files
        identity = store.read_persona_file(agent_id, "IDENTITY.md")
        soul = store.read_persona_file(agent_id, "SOUL.md")
        rules = store.read_persona_file(agent_id, "RULES.md")

        while True:
            self.console.print(f"\n[bold]✏️  编辑 Agent: {agent.name}[/bold]\n")

            edit_choice = self.questionary.select(
                "选择要修改的项:",
                choices=[
                    self.questionary.Choice(f"🏷️  姓名 - 当前: {agent.name}", value="name"),
                    self.questionary.Choice(
                        f"🎭 IDENTITY.md - 当前: {_preview(identity)}", value="identity"
                    ),
                    self.questionary.Choice(
                        f"💼 SOUL.md - 当前: {_preview(soul)}", value="soul"
                    ),
                    self.questionary.Choice(
                        f"📋 RULES.md - 当前: {_preview(rules)}", value="rules"
                    ),
                    self.questionary.Choice(
                        f"🤖 模型 - 当前: {agent.llm.model_name if agent.llm else 'default'}",
                        value="model",
                    ),
                    self.questionary.Choice(
                        f"💾 内存 - 当前: {agent.docker.memory_limit if agent.docker else 'default'}",
                        value="memory",
                    ),
                    self.questionary.Choice(
                        f"🚀 主动响应 - 当前: {'是' if agent.proactive else '否'}",
                        value="proactive",
                    ),
                    self.questionary.Choice(
                        f"💤 激活模式 - 当前: {'常驻' if agent.activation == 'always' else '休眠'}",
                        value="activation",
                    ),
                    self.questionary.Separator(),
                    self.questionary.Choice("✓ 完成", value="done"),
                ],
            ).ask()

            if edit_choice == "done":
                break

            elif edit_choice == "name":
                new_name = self.questionary.text("新姓名:", default=agent.name).ask()
                if new_name:
                    agent.name = new_name
                    self.console.print("[green]✓ 姓名已更新[/green]")

            elif edit_choice == "identity":
                new_text = self.questionary.text("IDENTITY.md 内容:", default=identity).ask()
                if new_text is not None:
                    identity = new_text
                    store.write_persona_file(agent_id, "IDENTITY.md", new_text)
                    self.console.print("[green]✓ IDENTITY.md 已更新[/green]")

            elif edit_choice == "soul":
                new_text = self.questionary.text("SOUL.md 内容:", default=soul).ask()
                if new_text is not None:
                    soul = new_text
                    store.write_persona_file(agent_id, "SOUL.md", new_text)
                    self.console.print("[green]✓ SOUL.md 已更新[/green]")

            elif edit_choice == "rules":
                new_text = self.questionary.text("RULES.md 内容:", default=rules).ask()
                if new_text is not None:
                    rules = new_text
                    store.write_persona_file(agent_id, "RULES.md", new_text)
                    self.console.print("[green]✓ RULES.md 已更新[/green]")

            elif edit_choice == "model":
                models = [
                    "kimi-k2.5", "kimi-k2", "gpt-4o", "gpt-4",
                    "gpt-4-turbo", "gpt-3.5-turbo", "claude-3-opus", "claude-3-sonnet",
                ]
                current_model = agent.llm.model_name if agent.llm else "kimi-k2.5"
                new_model = self.questionary.select(
                    "选择新模型:", choices=models, default=current_model
                ).ask()
                if new_model:
                    if not agent.llm:
                        from tlon.base.config import LLMConfig

                        agent.llm = LLMConfig(model_name=new_model)
                    else:
                        agent.llm.model_name = new_model
                    self.console.print("[green]✓ 模型已更新[/green]")

            elif edit_choice == "memory":
                current_mem = agent.docker.memory_limit if agent.docker else "512m"
                new_mem = self.questionary.text("内存限制:", default=current_mem).ask()
                if new_mem:
                    if not agent.docker:
                        from tlon.base.config import DockerRuntimeConfig

                        agent.docker = DockerRuntimeConfig(image="ubuntu:22.04", memory_limit=new_mem)
                    else:
                        agent.docker.memory_limit = new_mem
                    self.console.print("[green]✓ 内存限制已更新[/green]")

            elif edit_choice == "proactive":
                new_proactive = self.questionary.confirm(
                    "是否主动响应 (proactive)?\n"
                    "主动响应: 没有@的普通消息也会响应\n"
                    "被动响应: 只响应@自己的消息",
                    default=agent.proactive,
                ).ask()
                agent.proactive = new_proactive
                mode_str = "主动响应" if agent.proactive else "被动响应"
                self.console.print(f"[green]✓ 响应模式已更新为: {mode_str}[/green]")

            elif edit_choice == "activation":
                new_activation = self.questionary.select(
                    "激活模式 (activation):",
                    choices=[
                        self.questionary.Choice(
                            "常驻 (always) - 系统启动即运行，永不休眠", value="always"
                        ),
                        self.questionary.Choice(
                            "休眠 (lazy) - 被来信唤醒，空闲自动休眠", value="lazy"
                        ),
                    ],
                    default=agent.activation,
                ).ask()
                if new_activation:
                    agent.activation = new_activation
                    mode_str = "常驻" if agent.activation == "always" else "休眠"
                    self.console.print(f"[green]✓ 激活模式已更新为: {mode_str}[/green]")

        success, msg = self.agent_store.save_agent(agent)
        if success:
            self.console.print("[green]✓ Agent 已更新[/green]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def delete_agent(self) -> None:
        agents = self.agent_store.load_all_agents()
        if not agents:
            self.console.print("[yellow]暂无 Agent 可删除[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        self.console.print("\n[bold red]⚠️  警告: 删除 Agent 将永久删除其所有数据！[/bold red]\n")

        choices = [
            self.questionary.Choice(f"{a.name} ({a.id})", value=a.id)
            for a in sorted(agents.values(), key=lambda x: x.name)
        ]
        choices.append(self.questionary.Choice("← 取消", value="cancel"))

        agent_id = self.questionary.select("选择要删除的 Agent:", choices=choices).ask()
        if agent_id == "cancel":
            return

        agent = agents.get(agent_id)
        if not agent:
            return

        self.console.print(f"\n即将删除 Agent: {agent.name} ({agent.id})")
        confirm = self.questionary.text(f'确认删除? [输入 Agent ID "{agent.id}" 确认]:').ask()

        if confirm != agent.id:
            self.console.print("[yellow]已取消删除[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        success, msg = self.agent_store.delete_agent(agent_id)
        if success:
            self.console.print(f"[green]✓ {msg}[/green]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()
