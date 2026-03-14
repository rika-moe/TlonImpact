"""Position data storage operations.

Handles loading, saving, and managing Position configurations
from YAML files in config/positions/ directory.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from tlon.base.logger import get_logger
from tlon.organization.positions.models import Position

logger = get_logger(__name__)


# Built-in default templates (fallback when no YAML files exist)
# Uses standardized field format with 'id' as primary key
DEFAULT_POSITION_TEMPLATES: Dict[str, Dict] = {
    "developer": {
        "id": "developer",
        "title": "开发工程师",
        "jd": "负责代码编写、Bug 修复和技术方案实现",
        "department": "技术部",
        "contact_when": "有技术问题或Bug需要处理",
        "rules": [
            "只在你被@时响应，不要主动插话",
            "完成任务后向给你派活的人汇报",
            "可以执行任意 bash 命令来完成任务",
        ],
        "default_skills": ["file-operations", "git-operations", "bash-execution"],
        "prompt_template": """你是 {{ agent.name }}，一名 {{ position.title }}。

{{ position.jd }}。

你性格特点: {{ agent.personality }}

工作原则:
{% for rule in position.rules %}
- {{ rule }}
{% endfor %}

你可以使用各种工具来完成任务。当你完成任务时，必须使用 completeDialog 工具来结束对话。""",
    },
    "operator": {
        "id": "operator",
        "title": "运维工程师",
        "jd": "负责系统部署、环境配置和故障处理",
        "department": "运维部",
        "contact_when": "有系统告警或部署需求",
        "rules": [
            "收到告警时主动响应",
            "部署操作前必须确认环境",
            "记录所有故障处理过程",
        ],
        "default_skills": ["docker-operations", "bash-execution", "monitoring"],
        "prompt_template": """你是 {{ agent.name }}，一名 {{ position.title }}。

{{ position.jd }}。

你性格特点: {{ agent.personality }}

工作原则:
{% for rule in position.rules %}
- {{ rule }}
{% endfor %}

你可以使用各种工具来完成任务。当你完成任务时，必须使用 completeDialog 工具来结束对话。""",
    },
    "project_manager": {
        "id": "project_manager",
        "title": "项目经理",
        "jd": "负责任务协调、进度把控和资源分配",
        "department": "项目管理部",
        "contact_when": "需要协调资源或把控进度",
        "rules": [
            "主动协调团队成员",
            "定期跟进任务进度",
            "及时识别和上报风险",
        ],
        "default_skills": ["task-management", "planning", "communication"],
        "prompt_template": """你是 {{ agent.name }}，一名 {{ position.title }}。

{{ position.jd }}。

你性格特点: {{ agent.personality }}

工作原则:
{% for rule in position.rules %}
- {{ rule }}
{% endfor %}

作为项目经理，你需要主动协调团队，确保项目按时交付。当你完成任务时，必须使用 completeDialog 工具来结束对话。""",
    },
    "customer_service": {
        "id": "customer_service",
        "title": "客服专员",
        "jd": "用户的第一接触点。友好、耐心地接待用户。对问题进行初步诊断和分类。将技术问题转派给开发或运维。将需求问题转派给产品经理。大问题协调项目经理介入。",
        "department": "客服部",
        "contact_when": "用户有任何问题咨询",
        "rules": [
            "第一时间响应用户问题",
            "准确分类问题类型",
            "复杂问题及时转派给专业人员",
            "转派后不要持续跟进，相信同事会处理",
            "除非用户催促或被明确要求跟进",
        ],
        "default_skills": ["communication", "problem-triage", "escalation"],
        "prompt_template": """你是 {{ agent.name }}，一名 {{ position.title }}。

{{ position.jd }}。

你性格特点: {{ agent.personality }}

工作原则:
{% for rule in position.rules %}
- {{ rule }}
{% endfor %}

你是用户的第一接触点，需要友善耐心地帮助用户。当你完成任务时，必须使用 completeDialog 工具来结束对话。""",
    },
    "product_manager": {
        "id": "product_manager",
        "title": "产品经理",
        "jd": "负责需求分析、功能规划和方案评审",
        "department": "产品部",
        "contact_when": "有需求分析或功能规划需要",
        "rules": [
            "深入理解用户需求",
            "与开发团队密切沟通",
            "确保产品方案可落地",
        ],
        "default_skills": ["requirements-analysis", "documentation", "planning"],
        "prompt_template": """你是 {{ agent.name }}，一名 {{ position.title }}。

{{ position.jd }}。

你性格特点: {{ agent.personality }}

工作原则:
{% for rule in position.rules %}
- {{ rule }}
{% endfor %}

你是连接用户和技术的桥梁。当你完成任务时，必须使用 completeDialog 工具来结束对话。""",
    },
    "data_analyst": {
        "id": "data_analyst",
        "title": "数据分析师",
        "jd": "负责数据处理、可视化和统计分析",
        "department": "数据部",
        "contact_when": "有数据处理或分析需求",
        "rules": [
            "确保数据准确性",
            "提供清晰的数据解读",
            "保护敏感数据安全",
        ],
        "default_skills": ["data-processing", "visualization", "csv-pipeline"],
        "prompt_template": """你是 {{ agent.name }}，一名 {{ position.title }}。

{{ position.jd }}。

你性格特点: {{ agent.personality }}

工作原则:
{% for rule in position.rules %}
- {{ rule }}
{% endfor %}

你擅长从数据中发现洞察。当你完成任务时，必须使用 completeDialog 工具来结束对话。""",
    },
}


class PositionStore:
    """Store for managing Position configurations.
    
    Handles loading positions from YAML files and provides
    CRUD operations with reference tracking.
    """
    
    def __init__(self, positions_dir: str = "config/positions"):
        """Initialize the position store.
        
        Args:
            positions_dir: Directory containing position YAML files
        """
        self.positions_dir = Path(positions_dir)
        self._positions: Dict[str, Position] = {}
        self._ensure_directory()
    
    def _ensure_directory(self) -> None:
        """Ensure the positions directory exists."""
        self.positions_dir.mkdir(parents=True, exist_ok=True)
    
    def load_all_positions(self, force_reload: bool = False) -> Dict[str, Position]:
        """Load all positions from YAML files.
        
        Args:
            force_reload: Force reload from disk even if cached
            
        Returns:
            Dictionary mapping position id to Position object
        """
        if self._positions and not force_reload:
            return self._positions.copy()
        
        self._positions = {}
        
        # Check if directory has any YAML files
        yaml_files = list(self.positions_dir.glob("*.yaml"))
        
        if not yaml_files:
            # No YAML files, use default templates
            logger.info("No position YAML files found, using default templates")
            for pos_type, template in DEFAULT_POSITION_TEMPLATES.items():
                position = self._dict_to_position(template)
                self._positions[position.id] = position  # Use id as key
        else:
            # Load from YAML files
            for yaml_file in yaml_files:
                try:
                    with open(yaml_file, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    
                    if data and "position" in data:
                        position = self._dict_to_position(data["position"])
                        self._positions[position.id] = position  # Use id as key
                        logger.debug(f"Loaded position: {position.id}")
                except Exception as e:
                    logger.error(f"Failed to load position from {yaml_file}: {e}")
        
        return self._positions.copy()
    
    def get_position(self, position_id: str) -> Optional[Position]:
        """Get a position by id.
        
        Args:
            position_id: Position identifier
            
        Returns:
            Position object or None if not found
        """
        if not self._positions:
            self.load_all_positions()
        return self._positions.get(position_id)
    
    def save_position(self, position: Position) -> Tuple[bool, str]:
        """Save a position to YAML file.
        
        Uses standardized field format compatible with Server config loader.
        
        Args:
            position: Position to save
            
        Returns:
            Tuple of (success, message)
        """
        try:
            self._ensure_directory()
            
            # Build position dict with standardized fields
            # id is primary key, type is optional (defaults to id)
            position_dict = {
                "id": position.id,
                "type": position.type or position.id,
                "title": position.title,
                "jd": position.jd,
            }
            
            # Add optional fields only if they have values
            if position.department:
                position_dict["department"] = position.department
            
            if position.contact_when:
                position_dict["contact_when"] = position.contact_when
            
            if position.rules:
                position_dict["rules"] = position.rules
            
            if position.default_skills:
                position_dict["default_skills"] = position.default_skills
            
            if position.prompt_template:
                position_dict["prompt_template"] = position.prompt_template
            
            data = {"position": position_dict}
            
            # Write to file (use id as filename)
            filename = f"{position.id}.yaml"
            filepath = self.positions_dir / filename
            
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)
            
            # Update cache (use id as key)
            self._positions[position.id] = position
            
            logger.info(f"Saved position: {position.id} to {filepath}")
            return True, f"Position saved to {filepath}"
            
        except Exception as e:
            logger.error(f"Failed to save position {position.id}: {e}")
            return False, f"Error: {e}"
    
    def delete_position(self, position_type: str, agent_store=None) -> Tuple[bool, str]:
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
        
        try:
            # Delete file
            filename = f"{position_type}.yaml"
            filepath = self.positions_dir / filename
            
            if filepath.exists():
                filepath.unlink()
            
            # Remove from cache
            self._positions.pop(position_type, None)
            
            logger.info(f"Deleted position: {position_type}")
            return True, f"Position '{position_type}' deleted"
            
        except Exception as e:
            logger.error(f"Failed to delete position {position_type}: {e}")
            return False, f"Error: {e}"
    
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
            True if position exists
        """
        return self.get_position(position_type) is not None
    
    def get_default_templates(self) -> Dict[str, Dict]:
        """Get built-in default position templates.
        
        Returns:
            Dictionary of default position templates
        """
        return DEFAULT_POSITION_TEMPLATES.copy()
    
    def create_from_template(self, template_key: str, 
                            customizations: Optional[Dict] = None) -> Optional[Position]:
        """Create a position from a template.
        
        Args:
            template_key: Key of the template to use
            customizations: Optional dict of fields to override
            
        Returns:
            New Position object or None if template not found
        """
        template = DEFAULT_POSITION_TEMPLATES.get(template_key)
        if not template:
            return None
        
        data = template.copy()
        if customizations:
            data.update(customizations)
        
        return self._dict_to_position(data)
    
    def validate_position_data(self, data: Dict) -> Tuple[bool, List[str]]:
        """Validate position data.
        
        Args:
            data: Position data dictionary
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        # Check required fields
        required_fields = ["id", "title", "jd"]
        for field in required_fields:
            if field not in data or not data[field]:
                errors.append(f"Missing required field: {field}")
        
        # Validate id format (alphanumeric and underscore)
        if "id" in data:
            id_val = data["id"]
            if not id_val.replace("_", "").replace("-", "").isalnum():
                errors.append("Position id must be alphanumeric with underscores/hyphens only")
        
        # Validate Jinja2 template syntax
        if "prompt_template" in data and data["prompt_template"]:
            try:
                from jinja2 import Environment
                env = Environment()
                env.parse(data["prompt_template"])
            except Exception as e:
                errors.append(f"Invalid Jinja2 template: {e}")
        
        return len(errors) == 0, errors
    
    def _dict_to_position(self, data: Dict) -> Position:
        """Convert dictionary to Position object.
        
        Args:
            data: Position data dictionary
            
        Returns:
            Position object
        """
        return Position(
            id=data["id"],
            type=data.get("type", data["id"]),
            title=data.get("title", ""),
            jd=data.get("jd", ""),
            department=data.get("department", ""),
            rules=data.get("rules", []),
            default_skills=data.get("default_skills", []),
            prompt_template=data.get("prompt_template"),
            contact_when=data.get("contact_when", ""),
        )
