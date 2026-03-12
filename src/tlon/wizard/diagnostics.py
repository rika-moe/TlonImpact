"""Configuration diagnostics for Position-Agent architecture.

Validates configuration files, checks reference integrity,
and reports issues with positions and agents.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml

from ..logger import get_logger

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
    file_path: Optional[str] = None
    details: Optional[str] = None


@dataclass
class DiagnosticReport:
    """Complete diagnostic report."""
    issues: List[DiagnosticIssue] = field(default_factory=list)
    
    def add(self, severity: IssueSeverity, category: str, message: str, 
            file_path: Optional[str] = None, details: Optional[str] = None) -> None:
        """Add an issue to the report."""
        self.issues.append(DiagnosticIssue(
            severity=severity,
            category=category,
            message=message,
            file_path=file_path,
            details=details
        ))
    
    def has_errors(self) -> bool:
        """Check if report has any errors."""
        return any(i.severity == IssueSeverity.ERROR for i in self.issues)
    
    def has_warnings(self) -> bool:
        """Check if report has any warnings."""
        return any(i.severity == IssueSeverity.WARNING for i in self.issues)
    
    def get_errors(self) -> List[DiagnosticIssue]:
        """Get all error issues."""
        return [i for i in self.issues if i.severity == IssueSeverity.ERROR]
    
    def get_warnings(self) -> List[DiagnosticIssue]:
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
    """Diagnostics for Position-Agent configuration.
    
    Validates positions, agents, and their relationships.
    """
    
    def __init__(self, positions_dir: str = "config/positions", 
                 agents_dir: str = "config/agents"):
        """Initialize diagnostics.
        
        Args:
            positions_dir: Directory containing position YAML files
            agents_dir: Directory containing agent YAML files
        """
        self.positions_dir = Path(positions_dir)
        self.agents_dir = Path(agents_dir)
        self.report = DiagnosticReport()
    
    def run_all_checks(self) -> DiagnosticReport:
        """Run all diagnostic checks.
        
        Returns:
            DiagnosticReport with all issues
        """
        self.report = DiagnosticReport()
        
        logger.info("Running configuration diagnostics...")
        
        # Check directories exist
        self._check_directories()
        
        # Validate positions
        self.validate_positions()
        
        # Validate agents
        self.validate_agents()
        
        # Check references
        self.check_references()
        
        logger.info(f"Diagnostics complete: {self.report.get_summary()}")
        return self.report
    
    def _check_directories(self) -> None:
        """Check if configuration directories exist."""
        if not self.positions_dir.exists():
            self.report.add(
                IssueSeverity.WARNING,
                "filesystem",
                f"Positions directory does not exist: {self.positions_dir}",
                str(self.positions_dir)
            )
        
        if not self.agents_dir.exists():
            self.report.add(
                IssueSeverity.WARNING,
                "filesystem",
                f"Agents directory does not exist: {self.agents_dir}",
                str(self.agents_dir)
            )
    
    def validate_positions(self) -> DiagnosticReport:
        """Validate all position configurations.
        
        Returns:
            DiagnosticReport with position issues
        """
        if not self.positions_dir.exists():
            return self.report
        
        yaml_files = list(self.positions_dir.glob("*.yaml"))
        
        if not yaml_files:
            self.report.add(
                IssueSeverity.INFO,
                "positions",
                "No position configuration files found",
                str(self.positions_dir),
                "Will use built-in default templates"
            )
            return self.report
        
        for yaml_file in yaml_files:
            self._validate_single_position(yaml_file)
        
        return self.report
    
    def _validate_single_position(self, file_path: Path) -> None:
        """Validate a single position file.
        
        Args:
            file_path: Path to position YAML file
        """
        file_str = str(file_path)
        
        # Try to parse YAML
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            self.report.add(
                IssueSeverity.ERROR,
                "positions",
                f"Invalid YAML syntax",
                file_str,
                str(e)
            )
            return
        except Exception as e:
            self.report.add(
                IssueSeverity.ERROR,
                "positions",
                f"Failed to read file",
                file_str,
                str(e)
            )
            return
        
        # Check structure
        if not data:
            self.report.add(
                IssueSeverity.ERROR,
                "positions",
                f"Empty configuration file",
                file_str
            )
            return
        
        if "position" not in data:
            self.report.add(
                IssueSeverity.ERROR,
                "positions",
                f"Missing 'position' root key",
                file_str
            )
            return
        
        pos = data["position"]
        
        # Check required fields
        required_fields = ["type", "title", "jd"]
        for field in required_fields:
            if field not in pos or not pos[field]:
                self.report.add(
                    IssueSeverity.ERROR,
                    "positions",
                    f"Missing required field: {field}",
                    file_str
                )
        
        # Check type matches filename
        if "type" in pos:
            expected_filename = f"{pos['type']}.yaml"
            if file_path.name != expected_filename:
                self.report.add(
                    IssueSeverity.WARNING,
                    "positions",
                    f"Type '{pos['type']}' does not match filename",
                    file_str,
                    f"Expected: {expected_filename}, Got: {file_path.name}"
                )
        
        # Validate Jinja2 template
        if "prompt_template" in pos and pos["prompt_template"]:
            self._validate_jinja2_template(
                pos["prompt_template"], 
                file_str,
                "positions"
            )
        
        # Check rules is a list
        if "rules" in pos and not isinstance(pos["rules"], list):
            self.report.add(
                IssueSeverity.ERROR,
                "positions",
                f"Field 'rules' must be a list",
                file_str
            )
    
    def validate_agents(self) -> DiagnosticReport:
        """Validate all agent configurations.
        
        Returns:
            DiagnosticReport with agent issues
        """
        if not self.agents_dir.exists():
            return self.report
        
        yaml_files = list(self.agents_dir.glob("*.yaml"))
        
        if not yaml_files:
            self.report.add(
                IssueSeverity.WARNING,
                "agents",
                "No agent configuration files found",
                str(self.agents_dir)
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
        
        # Try to parse YAML
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            self.report.add(
                IssueSeverity.ERROR,
                "agents",
                f"Invalid YAML syntax",
                file_str,
                str(e)
            )
            return
        except Exception as e:
            self.report.add(
                IssueSeverity.ERROR,
                "agents",
                f"Failed to read file",
                file_str,
                str(e)
            )
            return
        
        # Check structure
        if not data:
            self.report.add(
                IssueSeverity.ERROR,
                "agents",
                f"Empty configuration file",
                file_str
            )
            return
        
        if "agent" not in data:
            self.report.add(
                IssueSeverity.ERROR,
                "agents",
                f"Missing 'agent' root key",
                file_str
            )
            return
        
        agent = data["agent"]
        
        # Check required fields
        required_fields = ["id", "name", "position_id"]
        for field in required_fields:
            if field not in agent or not agent[field]:
                self.report.add(
                    IssueSeverity.ERROR,
                    "agents",
                    f"Missing required field: {field}",
                    file_str
                )
        
        # Check id matches filename
        if "id" in agent:
            expected_filename = f"{agent['id']}.yaml"
            if file_path.name != expected_filename:
                self.report.add(
                    IssueSeverity.WARNING,
                    "agents",
                    f"ID '{agent['id']}' does not match filename",
                    file_str,
                    f"Expected: {expected_filename}, Got: {file_path.name}"
                )
        
        # Validate LLM config
        if "llm" in agent and agent["llm"]:
            llm = agent["llm"]
            if "model_name" not in llm or not llm["model_name"]:
                self.report.add(
                    IssueSeverity.WARNING,
                    "agents",
                    f"LLM config missing model_name",
                    file_str
                )
        
        # Validate Docker config
        if "docker" in agent and agent["docker"]:
            docker = agent["docker"]
            if "image" not in docker or not docker["image"]:
                self.report.add(
                    IssueSeverity.WARNING,
                    "agents",
                    f"Docker config missing image",
                    file_str
                )
        
        # Check skills is a list
        if "skills" in agent and not isinstance(agent["skills"], list):
            self.report.add(
                IssueSeverity.ERROR,
                "agents",
                f"Field 'skills' must be a list",
                file_str
            )
    
    def check_references(self) -> DiagnosticReport:
        """Check reference integrity between agents and positions.
        
        Returns:
            DiagnosticReport with reference issues
        """
        # Load all positions
        position_types: Set[str] = set()
        if self.positions_dir.exists():
            for yaml_file in self.positions_dir.glob("*.yaml"):
                try:
                    with open(yaml_file, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if data and "position" in data and "type" in data["position"]:
                        position_types.add(data["position"]["type"])
                except Exception:
                    pass
        
        # Load all agents and check references
        agent_position_refs: Dict[str, List[str]] = {}  # position_id -> list of agent files
        
        if self.agents_dir.exists():
            for yaml_file in self.agents_dir.glob("*.yaml"):
                try:
                    with open(yaml_file, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if data and "agent" in data:
                        agent = data["agent"]
                        if "position_id" in agent:
                            pos_id = agent["position_id"]
                            if pos_id not in agent_position_refs:
                                agent_position_refs[pos_id] = []
                            agent_position_refs[pos_id].append(str(yaml_file))
                        
                        # Check if position exists
                        if "position_id" in agent:
                            pos_id = agent["position_id"]
                            if pos_id not in position_types:
                                self.report.add(
                                    IssueSeverity.ERROR,
                                    "references",
                                    f"Agent references non-existent position: {pos_id}",
                                    str(yaml_file),
                                    f"Position '{pos_id}' not found in {self.positions_dir}"
                                )
                except Exception:
                    pass
        
        # Check for orphaned positions (no agents reference them)
        if position_types:
            for pos_type in position_types:
                if pos_type not in agent_position_refs:
                    self.report.add(
                        IssueSeverity.INFO,
                        "references",
                        f"Position has no agents: {pos_type}",
                        str(self.positions_dir / f"{pos_type}.yaml"),
                        "This position is not used by any agent"
                    )
        
        return self.report
    
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
                IssueSeverity.ERROR,
                category,
                f"Invalid Jinja2 template syntax",
                file_path,
                str(e)
            )
    
    def get_position_usage_stats(self) -> Dict[str, int]:
        """Get statistics on position usage by agents.
        
        Returns:
            Dictionary mapping position_id to count of agents
        """
        stats: Dict[str, int] = {}
        
        if not self.agents_dir.exists():
            return stats
        
        for yaml_file in self.agents_dir.glob("*.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and "agent" in data and "position_id" in data["agent"]:
                    pos_id = data["agent"]["position_id"]
                    stats[pos_id] = stats.get(pos_id, 0) + 1
            except Exception:
                pass
        
        return stats
