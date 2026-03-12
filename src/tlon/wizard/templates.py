"""Wizard templates for roles and skills.

Provides predefined role templates and skill library for Agent configuration.
"""

from typing import Dict, List

# Role templates with default specialties
ROLE_TEMPLATES: Dict[str, Dict] = {
    "developer": {
        "name": "开发工程师",
        "description": "负责代码编写、Bug 修复和代码审查",
        "personality": "技术能力强、注重细节、喜欢钻研",
        "proactive": False,
        "default_specialties": ["文件操作", "Git操作", "代码审查"],
        "skill_categories": ["programming", "frameworks", "databases", "tools"],
    },
    "operator": {
        "name": "运维工程师",
        "description": "负责系统部署、环境配置和故障处理",
        "personality": "沉稳冷静、善于排查问题、注重稳定性",
        "proactive": False,
        "default_specialties": ["Linux系统", "Docker", "Nginx", "监控告警"],
        "skill_categories": ["os", "containers", "networking", "monitoring"],
    },
    "project_manager": {
        "name": "项目经理",
        "description": "负责任务协调、进度把控和资源分配",
        "personality": "善于沟通、有条理、结果导向",
        "proactive": True,
        "default_specialties": ["任务分解", "进度把控", "团队协调", "风险管理"],
        "skill_categories": ["management", "planning", "communication"],
    },
    "frontend": {
        "name": "前端工程师",
        "description": "负责用户界面实现、交互优化和样式调整",
        "personality": "注重细节、追求用户体验、善于沟通",
        "proactive": False,
        "default_specialties": ["HTML/CSS", "JavaScript", "React/Vue", "UI设计"],
        "skill_categories": ["frontend", "ui", "frameworks"],
    },
    "data_analyst": {
        "name": "数据分析师",
        "description": "负责数据处理、可视化和统计分析",
        "personality": "严谨细致、善于发现规律、数据敏感",
        "proactive": False,
        "default_specialties": ["数据处理", "Pandas", "可视化", "统计分析"],
        "skill_categories": ["data", "statistics", "visualization"],
    },
    "security": {
        "name": "安全工程师",
        "description": "负责安全审计、漏洞修复和安全策略",
        "personality": "谨慎细致、攻防思维、持续学习",
        "proactive": False,
        "default_specialties": ["安全审计", "漏洞扫描", "渗透测试", "安全加固"],
        "skill_categories": ["security", "audit", "compliance"],
    },
    "customer_service": {
        "name": "客服",
        "description": "负责首响应、问题分类和初步诊断",
        "personality": "友好耐心、善于沟通、专业高效",
        "proactive": True,
        "default_specialties": ["首响应", "问题分类", "用户安抚", "初步诊断"],
        "skill_categories": ["communication", "problem_solving"],
    },
    "product_manager": {
        "name": "产品经理",
        "description": "负责需求分析、功能规划和方案评审",
        "personality": "用户导向、逻辑思维、善于协调",
        "proactive": False,
        "default_specialties": ["需求分析", "产品设计", "竞品分析", "用户研究"],
        "skill_categories": ["product", "analysis", "planning"],
    },
    "qa": {
        "name": "测试工程师",
        "description": "负责测试用例设计、Bug 跟踪和质量保证",
        "personality": "细心严谨、善于发现问题、追求质量",
        "proactive": False,
        "default_specialties": ["功能测试", "自动化测试", "Bug跟踪", "测试用例"],
        "skill_categories": ["testing", "automation", "quality"],
    },
}

# Skill library organized by category
SKILL_LIBRARY: Dict[str, List[Dict]] = {
    "基础技能": [
        {"id": "file-ops", "name": "文件操作", "desc": "读写文件、目录管理"},
        {"id": "git", "name": "Git操作", "desc": "版本控制、分支管理、代码提交"},
        {"id": "shell", "name": "Shell脚本", "desc": "Bash脚本编写和执行"},
    ],
    "编程语言": [
        {"id": "python", "name": "Python", "desc": "Python开发"},
        {"id": "javascript", "name": "JavaScript", "desc": "前端和后端JS开发"},
        {"id": "typescript", "name": "TypeScript", "desc": "带类型的JavaScript"},
        {"id": "go", "name": "Go", "desc": "Go语言开发"},
        {"id": "rust", "name": "Rust", "desc": "Rust系统编程"},
        {"id": "java", "name": "Java", "desc": "Java企业级开发"},
        {"id": "sql", "name": "SQL", "desc": "数据库查询和操作"},
    ],
    "框架/库": [
        {"id": "fastapi", "name": "FastAPI", "desc": "Python高性能Web框架"},
        {"id": "django", "name": "Django", "desc": "Python全栈Web框架"},
        {"id": "flask", "name": "Flask", "desc": "轻量级Python Web框架"},
        {"id": "react", "name": "React", "desc": "前端UI框架"},
        {"id": "vue", "name": "Vue", "desc": "渐进式前端框架"},
        {"id": "pandas", "name": "Pandas", "desc": "数据分析和处理"},
        {"id": "numpy", "name": "NumPy", "desc": "科学计算"},
    ],
    "运维/工具": [
        {"id": "docker", "name": "Docker", "desc": "容器化部署"},
        {"id": "k8s", "name": "Kubernetes", "desc": "容器编排"},
        {"id": "cicd", "name": "CI/CD", "desc": "持续集成/部署"},
        {"id": "aws", "name": "AWS", "desc": "亚马逊云服务"},
        {"id": "nginx", "name": "Nginx", "desc": "Web服务器和反向代理"},
        {"id": "linux", "name": "Linux系统", "desc": "Linux系统管理"},
    ],
    "数据库": [
        {"id": "mysql", "name": "MySQL", "desc": "关系型数据库"},
        {"id": "postgresql", "name": "PostgreSQL", "desc": "高级关系型数据库"},
        {"id": "redis", "name": "Redis", "desc": "内存数据库和缓存"},
        {"id": "mongodb", "name": "MongoDB", "desc": "文档型数据库"},
        {"id": "elasticsearch", "name": "Elasticsearch", "desc": "搜索引擎"},
    ],
    "AI/ML": [
        {"id": "ml", "name": "机器学习", "desc": "机器学习基础"},
        {"id": "pytorch", "name": "PyTorch", "desc": "深度学习框架"},
        {"id": "tensorflow", "name": "TensorFlow", "desc": "机器学习平台"},
        {"id": "nlp", "name": "自然语言处理", "desc": "NLP技术"},
        {"id": "openai", "name": "OpenAI API", "desc": "大语言模型API调用"},
    ],
}

# Available LLM models
AVAILABLE_MODELS = [
    "kimi-k2.5",
    "kimi-k2",
    "gpt-4o",
    "gpt-4",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
    "claude-3-opus",
    "claude-3-sonnet",
    "deepseek-chat",
    "qwen-max",
    "glm-4",
]

# Preset skills for the skills field in agents.yaml
PRESET_SKILLS = [
    "file-operations",
    "git-operations",
    "csv-pipeline",
    "plan-i",
    "frontend-design",
]


def get_role_template(role_id: str) -> Dict:
    """Get a role template by ID.
    
    Args:
        role_id: Role identifier
        
    Returns:
        Role template dict or empty dict if not found
    """
    return ROLE_TEMPLATES.get(role_id, {})


def get_all_roles() -> Dict[str, Dict]:
    """Get all role templates.
    
    Returns:
        Dictionary of all role templates
    """
    return ROLE_TEMPLATES.copy()


def get_skills_by_category(category: str) -> List[Dict]:
    """Get skills for a specific category.
    
    Args:
        category: Skill category name
        
    Returns:
        List of skill dictionaries
    """
    return SKILL_LIBRARY.get(category, [])


def get_all_skills() -> List[str]:
    """Get all skill names flattened.
    
    Returns:
        List of all skill names
    """
    skills = []
    for category_skills in SKILL_LIBRARY.values():
        for skill in category_skills:
            skills.append(skill["name"])
    return skills


def suggest_skills_for_role(role_id: str) -> List[str]:
    """Suggest skills based on role.
    
    Args:
        role_id: Role identifier
        
    Returns:
        List of suggested skill names
    """
    template = ROLE_TEMPLATES.get(role_id, {})
    return template.get("default_specialties", [])
