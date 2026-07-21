"""Configuration diagnostics for Agent architecture.

Validates agent configuration files and reports issues.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from tlon.base.logger import get_logger

logger = get_logger(__name__)


class IssueSeverity(Enum):
    """Severity level for diagnostic issues."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class DiagnosticIssue:
    """A single diagnostic issue."""

    severity: IssueSeverity
    category: str
    message: str
    file_path: str | None = None
    details: str | None = None


@dataclass
class DiagnosticReport:
    """Complete diagnostic report."""

    issues: list[DiagnosticIssue] = field(default_factory=list)

    def add(
        self,
        severity: IssueSeverity,
        category: str,
        message: str,
        file_path: str | None = None,
        details: str | None = None,
    ) -> None:
        """Add an issue to the report."""
        self.issues.append(
            DiagnosticIssue(
                severity=severity,
                category=category,
                message=message,
                file_path=file_path,
                details=details,
            )
        )

    def has_errors(self) -> bool:
        """Check if report has any errors."""
        return any(i.severity == IssueSeverity.ERROR for i in self.issues)

    def has_warnings(self) -> bool:
        """Check if report has any warnings."""
        return any(i.severity == IssueSeverity.WARNING for i in self.issues)

    def get_errors(self) -> list[DiagnosticIssue]:
        """Get all error issues."""
        return [i for i in self.issues if i.severity == IssueSeverity.ERROR]

    def get_warnings(self) -> list[DiagnosticIssue]:
        """Get all warning issues."""
        return [i for i in self.issues if i.severity == IssueSeverity.WARNING]

    def get_summary(self) -> str:
        """Get a summary string."""
        errors = len(self.get_errors())
        warnings = len(self.get_warnings())
        infos = len([i for i in self.issues if i.severity == IssueSeverity.INFO])

        parts = []
        if errors:
            parts.append(f"{errors} error(s)")
        if warnings:
            parts.append(f"{warnings} warning(s)")
        if infos:
            parts.append(f"{infos} info")

        if not parts:
            return "All checks passed"
        return ", ".join(parts)


class ConfigDiagnostics:
    """Diagnostics for Agent configuration.

    Validates agents and their configuration files.
    """

    def __init__(self, agents_dir: str | None = None):
        """Initialize diagnostics.

        Args:
            agents_dir: Tlon home directory (for backward compat; ignored).
                Agents are found at ~/.tlon/{id}/config/agent.yaml.
        """
        from tlon.base.paths import TlonPaths

        self.tlon_home = TlonPaths().tlon_home() if agents_dir is None else Path(agents_dir)
        self.report = DiagnosticReport()

    def run_all_checks(self) -> DiagnosticReport:
        """Run all diagnostic checks.

        Returns:
            DiagnosticReport with all issues
        """
        self.report = DiagnosticReport()

        logger.info("diagnostics_started", tlon_home=str(self.tlon_home))

        self._check_directories()
        self.validate_agents()

        logger.info("diagnostics_completed", summary=self.report.get_summary())
        return self.report

    def _check_directories(self) -> None:
        """Check if configuration directories exist."""
        if not self.tlon_home.exists():
            self.report.add(
                IssueSeverity.WARNING,
                "filesystem",
                f"Tlon home directory does not exist: {self.tlon_home}",
                str(self.tlon_home),
            )

    def validate_agents(self) -> DiagnosticReport:
        """Validate all agent configurations.

        Returns:
            DiagnosticReport with agent issues
        """
        if not self.tlon_home.exists():
            return self.report

        from tlon.base.paths import TlonPaths

        p = TlonPaths(home=self.tlon_home)
        yaml_files = []
        for agent_dir in sorted(self.tlon_home.iterdir()):
            if not agent_dir.is_dir() or agent_dir.name == "shared":
                continue
            yaml_file = p.agent_config_file(agent_dir.name)
            if yaml_file.exists():
                yaml_files.append(yaml_file)

        if not yaml_files:
            self.report.add(
                IssueSeverity.WARNING,
                "agents",
                "No agent configuration files found",
                str(self.tlon_home),
            )
            return self.report

        for yaml_file in yaml_files:
            self._validate_single_agent(yaml_file)

        return self.report

    def _validate_single_agent(self, file_path: Path) -> None:
        """Validate a single agent file.

        Args:
            file_path: Path to agent YAML file
        """
        file_str = str(file_path)

        try:
            with open(file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            self.report.add(IssueSeverity.ERROR, "agents", "Invalid YAML syntax", file_str, str(e))
            return
        except Exception as e:
            self.report.add(IssueSeverity.ERROR, "agents", "Failed to read file", file_str, str(e))
            return

        if not data:
            self.report.add(IssueSeverity.ERROR, "agents", "Empty configuration file", file_str)
            return

        if "agent" not in data:
            self.report.add(IssueSeverity.ERROR, "agents", "Missing 'agent' root key", file_str)
            return

        agent = data["agent"]

        required_fields = ["id", "name"]
        for field1 in required_fields:
            if field1 not in agent or not agent[field1]:
                self.report.add(
                    IssueSeverity.ERROR, "agents", f"Missing required field: {field1}", file_str
                )

        if "id" in agent:
            expected_filename = "agent.yaml"
            if file_path.name != expected_filename:
                self.report.add(
                    IssueSeverity.WARNING,
                    "agents",
                    f"ID '{agent['id']}' does not match filename",
                    file_str,
                    f"Expected: {expected_filename}, Got: {file_path.name}",
                )

        if "llm" in agent and agent["llm"]:
            llm = agent["llm"]
            if "model_name" not in llm or not llm["model_name"]:
                self.report.add(
                    IssueSeverity.WARNING, "agents", "LLM config missing model_name", file_str
                )

        if "docker" in agent and agent["docker"]:
            docker = agent["docker"]
            if "image" not in docker or not docker["image"]:
                self.report.add(
                    IssueSeverity.WARNING, "agents", "Docker config missing image", file_str
                )

    def _validate_jinja2_template(self, template: str, file_path: str, category: str) -> None:
        """Validate Jinja2 template syntax.

        Args:
            template: Template string to validate
            file_path: Path for error reporting
            category: Category for error reporting
        """
        try:
            from jinja2 import Environment

            env = Environment()
            env.parse(template)
        except Exception as e:
            self.report.add(
                IssueSeverity.ERROR, category, "Invalid Jinja2 template syntax", file_path, str(e)
            )
