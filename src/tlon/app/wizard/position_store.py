"""Position data storage operations.

Handles loading, saving, and managing Position configurations
from YAML files in config/positions/ directory.

This module is a thin wrapper around ConfigLoader/ConfigSaver
with additional support for default templates.
"""

from pathlib import Path

from tlon.base.config.loader import ConfigLoader
from tlon.base.config.models import PositionConfig
from tlon.base.config.saver import ConfigSaver
from tlon.base.config.validator import ConfigValidator
from tlon.base.logger import get_logger

logger = get_logger(__name__)


# Built-in default position templates (fallback when no YAML files exist)
DEFAULT_POSITIONS: dict[str, PositionConfig] = {
    "developer": PositionConfig(
        id="developer",
        type="developer",
        title="开发工程师",
        jd="负责代码编写、Bug 修复和技术方案实现",
        department="技术部",
        contact_when="有技术问题或Bug需要处理",
        rules=[
            "只在你被@时响应，不要主动插话",
            "完成任务后向给你派活的人汇报",
            "可以执行任意 bash 命令来完成任务",
        ],
        default_skills=[],
    ),
    "operator": PositionConfig(
        id="operator",
        type="operator",
        title="运维工程师",
        jd="负责系统部署、环境配置和故障处理",
        department="运维部",
        contact_when="有系统告警或部署需求",
        rules=[
            "收到告警时主动响应",
            "部署操作前必须确认环境",
            "记录所有故障处理过程",
        ],
        default_skills=[],
    ),
    "project_manager": PositionConfig(
        id="project_manager",
        type="project_manager",
        title="项目经理",
        jd="负责任务协调、进度把控和资源分配",
        department="项目管理部",
        contact_when="需要协调资源或把控进度",
        rules=[
            "主动协调团队成员",
            "定期跟进任务进度",
            "及时识别和上报风险",
        ],
        default_skills=[],
    ),
    "customer_service": PositionConfig(
        id="customer_service",
        type="customer_service",
        title="客服专员",
        jd="用户的第一接触点。友好、耐心地接待用户。对问题进行初步诊断和分类。将技术问题转派给开发或运维。将需求问题转派给产品经理。大问题协调项目经理介入。",
        department="客服部",
        contact_when="用户有任何问题咨询",
        rules=[
            "第一时间响应用户问题",
            "准确分类问题类型",
            "复杂问题及时转派给专业人员",
            "转派后不要持续跟进，相信同事会处理",
            "除非用户催促或被明确要求跟进",
        ],
        default_skills=[],
    ),
    "product_manager": PositionConfig(
        id="product_manager",
        type="product_manager",
        title="产品经理",
        jd="负责需求分析、功能规划和方案评审",
        department="产品部",
        contact_when="有需求分析或功能规划需要",
        rules=[
            "深入理解用户需求",
            "与开发团队密切沟通",
            "确保产品方案可落地",
        ],
        default_skills=[],
    ),
    "data_analyst": PositionConfig(
        id="data_analyst",
        type="data_analyst",
        title="数据分析师",
        jd="负责数据处理、可视化和统计分析",
        department="数据部",
        contact_when="有数据处理或分析需求",
        rules=[
            "确保数据准确性",
            "提供清晰的数据解读",
            "保护敏感数据安全",
        ],
        default_skills=[],
    ),
}


class PositionStore:
    """Store for managing Position configurations.

    A thin wrapper around ConfigLoader/ConfigSaver that provides
    CRUD operations with default template support.

    Attributes:
        positions_dir: Directory containing position YAML files
        _loader: ConfigLoader instance for loading
        _saver: ConfigSaver instance for saving
    """

    def __init__(self, positions_dir: str = "config/positions"):
        """Initialize the position store.

        Args:
            positions_dir: Directory containing position YAML files
        """
        self.positions_dir = Path(positions_dir)
        self._loader = ConfigLoader(positions_dir=positions_dir)
        self._saver = ConfigSaver(positions_dir=positions_dir)
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Ensure the positions directory exists."""
        self.positions_dir.mkdir(parents=True, exist_ok=True)

    def load_all_positions(self, force_reload: bool = False) -> dict[str, PositionConfig]:
        """Load all positions from YAML files.

        Falls back to default templates if no YAML files exist.

        Args:
            force_reload: Force reload from disk even if cached

        Returns:
            Dictionary mapping position id to PositionConfig object
        """
        positions = self._loader.load_all_positions(force_reload=force_reload)

        # If no positions found, use defaults
        if not positions:
            logger.info("No position YAML files found, using default templates")
            positions = DEFAULT_POSITIONS.copy()

        return positions

    def get_position(self, position_id: str) -> PositionConfig | None:
        """Get a position by id.

        Args:
            position_id: Position identifier

        Returns:
            PositionConfig object or None if not found
        """
        # Try loading from file first
        position = self._loader.load_position(position_id)
        if position:
            return position

        # Fall back to default templates
        return DEFAULT_POSITIONS.get(position_id)

    def save_position(self, position: PositionConfig) -> tuple[bool, str]:
        """Save a position to YAML file.

        Uses ConfigSaver for the actual save operation.

        Args:
            position: PositionConfig to save

        Returns:
            Tuple of (success, message)
        """
        return self._saver.save_position(position)

    def delete_position(self, position_type: str, agent_store=None) -> tuple[bool, str]:
        """Delete a position.

        Args:
            position_type: Position type to delete
            agent_store: Optional AgentStore to check references

        Returns:
            Tuple of (success, message)
        """
        # Check if position exists
        position = self.get_position(position_type)
        if not position:
            return False, f"Position '{position_type}' not found"

        # Check if any agents reference this position
        if agent_store:
            count = self.get_position_usage_count(position_type, agent_store)
            if count > 0:
                return False, f"Cannot delete: {count} agent(s) are using this position"

        # Cannot delete default positions (they don't have files)
        if (
            position_type in DEFAULT_POSITIONS
            and not (self.positions_dir / f"{position_type}.yaml").exists()
        ):
            return False, f"Cannot delete built-in position '{position_type}'"

        return self._saver.delete_position(position_type)

    def get_position_usage_count(self, position_type: str, agent_store) -> int:
        """Get number of agents using this position.

        Args:
            position_type: Position type to check
            agent_store: AgentStore instance

        Returns:
            Number of agents referencing this position
        """
        agents = agent_store.load_all_agents()
        count = sum(1 for agent in agents.values() if agent.position_id == position_type)
        return count

    def position_exists(self, position_type: str) -> bool:
        """Check if a position exists.

        Args:
            position_type: Position type to check

        Returns:
            True if position exists (in files or defaults)
        """
        return self.get_position(position_type) is not None

    def get_default_positions(self) -> dict[str, PositionConfig]:
        """Get built-in default position templates.

        Returns:
            Dictionary of default position templates
        """
        return DEFAULT_POSITIONS.copy()

    def create_from_template(
        self, template_key: str, customizations: dict | None = None
    ) -> PositionConfig | None:
        """Create a position from a template.

        Args:
            template_key: Key of the template to use
            customizations: Optional dict of fields to override

        Returns:
            New PositionConfig object or None if template not found
        """
        template = DEFAULT_POSITIONS.get(template_key)
        if not template:
            return None

        # Create a copy with customizations
        data = template.to_dict()
        if customizations:
            data.update(customizations)

        return PositionConfig.from_dict(data)

    def validate_position_data(self, data: dict) -> tuple[bool, list[str]]:
        """Validate position data.

        Args:
            data: Position data dictionary

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        return ConfigValidator.validate_position_data(data)


__all__ = ["PositionStore", "DEFAULT_POSITIONS"]
