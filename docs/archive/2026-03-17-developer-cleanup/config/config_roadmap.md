# Tlon 配置系统实施路线图

> 版本: 1.0  
> 日期: 2026-03-08  
> 状态: 计划中

---

## 1. 目标

将 Agent Prompt 的硬编码内容迁移到配置文件中，实现：
- **可配置**: 无需修改代码即可调整 Prompt 内容
- **可维护**: 配置集中管理，易于理解和修改
- **灵活**: 不同 Position 可以有不同的 Prompt 策略

---

## 2. 实施阶段

### Phase 1: 准备工作（已完成 ✅）

**任务清单**:
- [x] 识别代码中的硬编码 Prompt 内容
- [x] 设计三层配置架构（全局/Position/Agent）
- [x] 确定覆盖策略（完全替换 vs 追加）
- [x] 编写设计文档

**相关文件**:
- `minions/agents/agent.py` 中的 `_build_system_prompt()`, `_build_role_prompt()`, `_build_discipline_rules()`
- `config/positions/*.yaml` 中的 `prompt_template` 字段（将被删除）

**完成标志**:
- [x] `docs/developer/config/config_design.md` 完成
- [x] 本文档 `config_roadmap.md` 完成

---

### Phase 2: 核心模型修改

**任务清单**:
- [ ] Position 模型添加 `prompt_overrides` 字段
- [ ] 创建 `PromptTemplateRegistry` 类
- [ ] 添加 `contact_when` 字段到 Position 模型（已完成 ✅）

**涉及文件**:
```
minions/positions/models.py          # 添加 prompt_overrides 字段
minions/prompt_templates.py          # 新建，模板加载器
```

**Schema 变更**:

```python
# tlon/positions/models.py

@dataclass
class Position:
    # ... 现有字段 ...
    
    # 新增字段
    contact_when: str = ""  # 已完成 ✅
    
    prompt_overrides: Dict[str, Any] = field(default_factory=dict)
    # 结构:
    # {
    #   "workflow": "...",
    #   "important_notes": ["...", "..."],
    #   "discipline_rules": ["...", "..."]
    # }
```

**完成标志**:
- [ ] Position 模型可以正确加载 `prompt_overrides`
- [ ] 单元测试通过

---

### Phase 3: 创建全局模板配置

**任务清单**:
- [ ] 创建 `config/prompt_templates.yaml` 文件
- [ ] 从代码中提取默认内容
- [ ] 实现 `PromptTemplateRegistry` 加载器

**涉及文件**:
```
config/prompt_templates.yaml         # 新建
minions/prompt_templates.py          # 新建
```

**内容来源**:

从 `minions/agents/agent.py` 的 `_build_system_prompt()` 提取：

```yaml
# config/prompt_templates.yaml
templates:
  workflow: |
    1. **思考阶段**: 分析任务，决定需要执行哪些操作
    2. **执行阶段**: 使用工具（bash、记忆操作等）收集信息或执行操作
    3. **完成阶段**: 使用 completeDialog 工具发送最终回复并结束处理
  
  important_notes:
    - "⚠️ **必须使用 completeDialog 结束**: 直接输出文本不会发送消息，必须调用 completeDialog 工具"
    - "⚠️ **@mention 机制**: 只有通过工具的 `mentions` 参数指定的人才会收到通知"
    - "使用表格中'名字'列的完整姓名作为 mentions 参数（如 ['顾言蹊', '江予白']）"
  
  discipline_rules:
    - "1. **收到任务时**: 确认收到即可，不要重复 @发送者"
    - '   - ✅ 正确: "收到，我来处理"'
    - '   - ❌ 错误: "@顾言蹊 收到，我来处理"（不要回@）'
    - ""
    - "2. **任务完成时**: 向任务分派者汇报"
    - "   - 如果 @沈清晏 派的活 → 完成后 @沈清晏"
    - "   - 如果 @人类 直接派的活 → 完成后 @人类"
    - ""
    - '3. **避免无意义回应**: 不要对 "收到"、"好的"、"谢谢"进行回复'
    - ""
    - "4. **循环检测**: 如果你和另一个 Agent 来回@超过2轮，停止回复，让人类介入"
    - ""
    - "5. **@mention 规则**: 使用 completeDialog 的 mentions 参数指定负责人"
    - "   - 只有被 mentions 的人才会收到通知并响应"
    - "   - 分派任务时必须通过 mentions 明确指定负责人"
```

**完成标志**:
- [ ] 文件创建成功
- [ ] `PromptTemplateRegistry` 可以正确加载
- [ ] 单元测试通过

---

### Phase 4: 重构 Agent Prompt 构建

**任务清单**:
- [ ] 重构 `_build_system_prompt()` 使用模板配置
- [ ] 删除 `_build_role_prompt()` 方法
- [ ] 重构 `_build_discipline_rules()` 使用模板配置

**涉及文件**:
```
minions/agents/agent.py
```

**代码变更**:

```python
# tlon/agents/agent.py

# 删除方法
- def _build_role_prompt(self) -> str:
-     """已删除，功能被 position.jd 替代"""

# 修改方法签名
- def _build_discipline_rules(self) -> str:
+ def _build_discipline_rules(self, templates: PromptTemplateRegistry) -> str:

# 修改 _build_system_prompt
  async def _build_system_prompt(self) -> str:
      # 加载模板
+     from ..prompt_templates import PromptTemplateRegistry
+     templates = PromptTemplateRegistry()
      
      # 获取配置（优先使用 Position 覆盖）
+     workflow = templates.get_workflow(self.position)
+     important_notes = templates.get_important_notes(self.position)
+     discipline_rules = templates.get_discipline_rules(self.position)
      
      # ... 构建其余部分 ...
```

**完成标志**:
- [ ] `_build_system_prompt()` 正常工作
- [ ] 所有测试通过
- [ ] 手动验证 Prompt 输出正确

---

### Phase 5: 更新 Position 配置文件

**任务清单**:
- [ ] 删除所有 Position 配置中的 `prompt_template` 字段
- [ ] 为需要特殊 Prompt 的 Position 添加 `prompt_overrides`
- [ ] 添加 `contact_when` 字段（已完成 ✅）

**涉及文件**:
```
config/positions/customer_service.yaml
config/positions/developer.yaml
config/positions/project_manager.yaml
config/positions/operator.yaml
config/positions/product_manager.yaml
config/positions/data_analyst.yaml
```

**变更示例**:

```yaml
# config/positions/customer_service.yaml

position:
  id: "customer_service"
  type: "customer_service"
  title: "客服专员"
  
  # ... 现有字段 ...
  
  # 删除以下字段（不再使用）
  # prompt_template: | ...
  
  # 添加 contact_when（已完成 ✅）
  contact_when: "需要首响应或问题分类"
  
  # 添加 prompt_overrides（可选）
  prompt_overrides:
    workflow: |
      1. **首响应**: 快速响应用户，进行初步诊断和安抚
      2. **分类**: 判断问题类型（技术问题/需求问题/大问题）
      3. **转派**: 使用 completeDialog 的 mentions 参数指定负责人
      4. **跟进**: 只在用户催促或被明确要求时跟进
```

**完成标志**:
- [ ] 所有 Position 配置文件已更新
- [ ] 无 `prompt_template` 残留
- [ ] 应用启动正常

---

### Phase 6: 测试与验证

**任务清单**:
- [ ] 单元测试
- [ ] 集成测试
- [ ] 手动验证

**测试用例**:

1. **模板加载测试**
   - 全局模板正确加载
   - Position 覆盖正确生效
   - 默认值正确回退

2. **Prompt 构建测试**
   - 无覆盖时使用全局模板
   - 有覆盖时使用 Position 配置
   - 多行字符串正确处理

3. **集成测试**
   - Agent 启动正常
   - Prompt 内容正确
   - 对话行为正常

**完成标志**:
- [ ] 所有测试通过
- [ ] 手动验证 Prompt 输出符合预期

---

### Phase 7: 文档更新

**任务清单**:
- [ ] 更新 README.md（如果需要）
- [ ] 更新 AGENTS.md（如果需要）
- [ ] 添加配置示例

**完成标志**:
- [ ] 用户可以通过文档理解如何配置

---

## 3. 时间线

| 阶段 | 预计时间 | 依赖 |
|------|----------|------|
| Phase 1: 准备工作 | ✅ 已完成 | - |
| Phase 2: 模型修改 | 1-2 小时 | Phase 1 |
| Phase 3: 全局模板 | 2-3 小时 | Phase 2 |
| Phase 4: 重构代码 | 3-4 小时 | Phase 3 |
| Phase 5: 更新配置 | 1-2 小时 | Phase 4 |
| Phase 6: 测试验证 | 2-3 小时 | Phase 5 |
| Phase 7: 文档更新 | 1 小时 | Phase 6 |
| **总计** | **10-15 小时** | - |

---

## 4. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Prompt 内容错误导致 Agent 行为异常 | 高 | 1. 保留原始硬编码作为 fallback<br>2. 充分的测试验证<br>3. 渐进式 rollout |
| 配置文件格式错误 | 中 | 1. 严格的 Schema 验证<br>2. 清晰的错误提示<br>3. 单元测试覆盖 |
| Position 覆盖配置遗漏 | 低 | 1. 默认使用全局模板<br>2. 文档说明覆盖机制 |

---

## 5. 回滚计划

如果出现问题，可以：

1. **快速回滚**: 将 `prompt_overrides` 清空，使用全局默认
2. **代码回滚**: 恢复 `_build_system_prompt()` 的硬编码版本
3. **配置恢复**: 从 git 历史恢复旧的 Position 配置文件

---

## 6. 后续优化（可选）

### 6.1 热重载支持
- 监听配置文件变化
- 无需重启即可更新 Prompt

### 6.2 配置验证工具
- 命令行工具验证配置格式
- 预检查配置文件错误

### 6.3 配置编辑器
- Web UI 编辑配置
- 实时预览 Prompt 效果

### 6.4 A/B 测试支持
- 同一 Position 多版本配置
- 按比例分配流量

---

## 7. 参考

- [config_design.md](./config_design.md) - 详细设计文档
- `docs/developer/02-roadmap/agent_position_roadmap.md` - 原有路线图
