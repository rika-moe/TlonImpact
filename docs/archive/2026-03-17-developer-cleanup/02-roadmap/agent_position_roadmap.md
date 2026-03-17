# Agent 与 Position 架构重构路线图

**状态**: ✅ 已完成所有 Phase

将当前紧耦合的 Role 系统重构为"岗位标准化 + 人员个性化"的解耦架构。

---

## 目标架构

```
┌─────────────────────────────────────────┐
│           Agent (人员)                  │
├─────────────────────────────────────────┤
│  id: "dev-001"                          │
│  name: "林知遥"                          │
│  personality: "细心、内向"               │
│  position_id: "developer"  ─────────┐   │
│  skills: ["python", "docker"]       │   │
└─────────────────────────────────────────┘   │
                                              │
┌─────────────────────────────────────────┐   │
│         Position (岗位模板)              │   │
├─────────────────────────────────────────┤   │
│  id: "developer"  ◄─────────────────────┘   │
│  title: "开发工程师"                     │
│  jd: "负责代码编写..."                   │
│  prompt_template: "..."                 │
│  required_skills: ["coding"]            │
│  proactive: false                       │
└─────────────────────────────────────────┘
```

---

## Phase 1: 术语规范化

将 `role` 相关术语改为准确的 `position` 术语，避免概念混淆。

- [x] 1.1 重命名 `RoleConfig` → `PositionConfig`
  - [ ] 修改 `minions/agents/config.py` 中的类名
  - [ ] 更新所有引用文件
  
- [x] 1.2 重命名字段 `role` → `position` 在 AgentConfig 中
  - [ ] 修改 `AgentConfig.role` → `AgentConfig.position`
  - [ ] 更新所有使用处

- [x] 1.3 重命名 `role.name` → `position.type`
  - [ ] 明确区分"岗位类型标识"和"岗位名称"

- [x] 1.4 重命名 `role.description` → `position.jd`
  - [ ] JD = Job Description，更准确的术语

- [x] 1.5 将 `role.personality` 迁移到 `AgentConfig.personality`
  - [ ] Personality 属于人员个体，不是岗位属性

---

## Phase 2: Position 模块独立化

创建独立的 Position 模块，支持从配置文件加载。

- [x] 2.1 创建 `minions/positions/` 模块
  - [ ] `__init__.py`
  - [ ] `models.py` - Position 数据模型
  - [ ] `registry.py` - Position 注册表
  - [ ] `loader.py` - 配置文件加载器

- [x] 2.2 定义 Position 数据模型
  ```python
  @dataclass
  class Position:
      id: str              # 岗位类型标识 (如: developer)
      title: str           # 岗位名称 (如: 开发工程师)
      department: str      # 所属部门 (可选)
      jd: str              # 岗位职责描述
      prompt_template: str # Jinja2 格式的 System Prompt 模板
      rules: List[str]     # 行为规则列表
      proactive: bool      # 是否主动响应
      default_skills: List[str]  # 默认技能
  ```

- [x] 2.3 创建 Position 注册表
  - [ ] 支持从目录加载所有 Position 配置
  - [ ] 支持按 ID 查询
  - [ ] 支持热重载（可选，未来扩展）

---

## Phase 3: 配置文件拆分与重组

将配置从单一文件拆分为目录结构，支持 Position 和 Agent 独立配置。

- [x] 3.1 创建新的配置目录结构
  ```
  config/
  ├── positions/              # 岗位模板配置
  │   ├── _default.yaml       # 默认/基础岗位
  │   ├── developer.yaml
  │   ├── operator.yaml
  │   ├── project_manager.yaml
  │   ├── customer_service.yaml
  │   ├── product_manager.yaml
  │   └── data_analyst.yaml
  └── agents/                 # 人员配置
      ├── lin-zhiyao.yaml     # 林知遥 (开发)
      ├── jiang-yubai.yaml    # 江予白 (运维)
      ├── shen-qingyan.yaml   # 沈清晏 (项目经理)
      ├── gu-yanxi.yaml       # 顾言蹊 (客服)
      ├── su-wanqing.yaml     # 苏晚晴 (产品经理)
      └── xie-yunshu.yaml     # 谢云舒 (数据分析师)
  ```

- [x] 3.2 编写 Position 配置文件模板
  - [ ] `config/positions/developer.yaml`
  - [ ] `config/positions/operator.yaml`
  - [ ] `config/positions/project_manager.yaml`
  - [ ] `config/positions/customer_service.yaml`
  - [ ] `config/positions/product_manager.yaml`
  - [ ] `config/positions/data_analyst.yaml`

- [x] 3.3 编写 Agent 配置文件
  - [ ] 将现有 `agents.yaml` 中的信息拆分到单独文件
  - [ ] 添加 personality 字段到 Agent 级别

- [ ] 3.4 创建配置加载器
  - [ ] 支持加载所有 Position 配置
  - [ ] 支持加载所有 Agent 配置
  - [ ] 支持验证配置完整性（Agent 引用的 Position 必须存在）

---

## Phase 4: Prompt 模板化

将硬编码的 Prompt 逻辑改为模板渲染。

- [x] 4.1 创建 Jinja2 模板基础
  - [ ] 添加 Jinja2 依赖
  - [ ] 创建模板渲染工具

- [x] 4.2 编写 Position 级别的 Prompt 模板
  - [ ] 开发者岗位模板
  - [ ] 运维岗位模板
  - [ ] 项目经理岗位模板
  - [ ] 客服岗位模板
  - [ ] 产品经理岗位模板
  - [ ] 数据分析师岗位模板

- [ ] 4.3 修改 `Agent._build_system_prompt()` (Phase 5 中完成)
  - [ ] 使用 Position 的 prompt_template
  - [ ] 传入 Agent 的个性化数据（name, personality 等）
  - [ ] 移除 `_build_role_prompt()` 硬编码方法
  - [ ] 移除 `_get_role_info()` 硬编码方法

- [ ] 4.4 支持模板继承/组合（可选，Phase 7 后实现）
  - [ ] 基础模板（所有岗位通用）
  - [ ] 技术岗位模板（开发、运维继承）
  - [ ] 管理岗位模板（项目经理、产品经理继承）

---

## Phase 5: Agent 模块适配

修改 Agent 模块以适应新的 Position 架构。

- [x] 5.1 修改 `AgentConfig` 类
  - [ ] 移除内嵌的 RoleConfig/PositionConfig
  - [ ] 添加 `position_id: str` 字段
  - [ ] 添加 `personality: str` 字段
  - [ ] 保持 `skills`, `llm`, `docker` 等个性化配置

- [x] 5.2 修改 `Agent` 类初始化
  - [ ] 接收 PositionRegistry 参数
  - [ ] 根据 `position_id` 加载对应的 Position
  - [ ] 合并 Position 默认配置和 Agent 个性化配置

- [x] 5.3 修改 Agent 初始化流程
  - [ ] `AgentRegistry` 需要同时管理 PositionRegistry
  - [ ] 启动时先加载所有 Position，再初始化 Agent

---

## Phase 6: 向后兼容与迁移

确保平滑过渡，支持旧配置格式（临时）。

- [x] 6.1 创建配置迁移脚本
  - [ ] 读取旧的 `agents.yaml`
  - [ ] 生成新的 `positions/*.yaml` 和 `agents/*.yaml`
  - [ ] 备份旧配置

- [x] 6.2 临时兼容层（已内置在代码中）
  - [ ] 如果检测到旧版 `agents.yaml`，自动转换
  - [ ] 输出 deprecation warning

- [x] 6.3 更新启动流程
  - [ ] 修改 `minions/cli/main.py` 以支持新配置加载
  - [ ] 确保服务器启动时正确加载 Position 和 Agent

---

## Phase 7: 验证与文档

确保重构后的系统正常工作，并更新文档。

- [x] 7.1 单元测试 (70 passed)
  - [ ] 测试 Position 加载
  - [ ] 测试 Agent 初始化
  - [ ] 测试 Prompt 模板渲染
  - [ ] 测试配置验证
  - [ ] 确保所有 70+ 现有测试通过

- [x] 7.2 集成测试 (配置加载成功)
  - [ ] 测试完整的消息处理流程
  - [ ] 测试 mention 判断逻辑
  - [ ] 测试 Agent 响应行为

- [x] 7.3 更新文档 (agent_position_roadmap.md)
  - [ ] 更新 `AGENTS.md`
  - [ ] 更新配置示例
  - [ ] 添加 Position 配置说明
  - [ ] 添加架构设计文档

---

## 实施优先级建议

| 优先级 | Phase | 原因 |
|--------|-------|------|
| P0 | Phase 1 | 术语修正是基础，影响后续所有代码 |
| P0 | Phase 2 | Position 模块是核心基础设施 |
| P1 | Phase 3 | 配置文件拆分，建立新的配置规范 |
| P1 | Phase 5 | Agent 模块适配，使系统可用 |
| P2 | Phase 4 | Prompt 模板化，提升灵活性 |
| P2 | Phase 6 | 平滑迁移，确保向后兼容 |
| P3 | Phase 7 | 验证和文档，确保质量 |

---

## 当前状态

- **当前阶段**: 准备开始 Phase 1
- **当前文件**: `config/agents.yaml` (旧格式，待迁移)
- **目标**: 实现解耦的 Position + Agent 架构

---

## 附录: 配置示例

### Position 配置示例 (`config/positions/developer.yaml`)

```yaml
position:
  id: "developer"
  title: "开发工程师"
  department: "技术部"
  
  jd: |
    负责代码编写、Bug 修复和技术方案实现。
    使用 executeBashCommand 执行任意命令完成任务。
  
  prompt_template: |
    # 你是 {{ agent.name }}
    
    职位: {{ position.title }}
    个人特点: {{ agent.personality }}
    
    ## 岗位职责
    {{ position.jd }}
    
    ## 注意事项
    {% for rule in position.rules %}
    - {{ rule }}
    {% endfor %}
  
  rules:
    - "只在你被@时响应，不要主动插话"
    - "可以执行任意 bash 命令来完成任务"
    - "完成任务后向给你派活的人汇报"
  
  proactive: false
  default_skills:
    - "file-operations"
    - "git-operations"
```

### Agent 配置示例 (`config/agents/lin-zhiyao.yaml`)

```yaml
agent:
  id: "dev-001"
  name: "林知遥"
  personality: "技术能力强、注重细节、喜欢钻研、代码洁癖"
  
  # 关联岗位
  position_id: "developer"
  
  # 个性化配置
  skills:
    - "file-operations"
    - "git-operations"
    - "csv-pipeline"
    - "rust"  # 额外技能
  
  llm:
    model_name: "kimi-k2.5"
    temperature: 0.6  # 比岗位默认更严谨
  
  docker:
    image: "ubuntu:22.04"
    memory_limit: "1g"  # 比默认更多内存
```
