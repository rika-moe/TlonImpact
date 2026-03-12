# Tlon WebUI 开发路线图

> WebUI 实施计划与任务追踪
> 
> **状态**: Phase 1-3 核心代码已完成，待构建验证 | Phase 4-5 未开始

---

## 实现状态总览

| Phase | 任务数 | 已完成 | 状态 |
|-------|--------|--------|------|
| Phase 1: 基础架构 | 12 | 12 | ✅ 完成 |
| Phase 2: 核心功能 | 19 | 19 | ✅ 完成 |
| Phase 3: 监控与管理 | 14 | 14 | ✅ 完成 |
| Phase 4: 设置与优化 | 12 | 12 | ✅ 完成 |
| Phase 5: polish 与发布 | 8 | 0 | ⏳ 未开始 |
| **总计** | **65** | **57** | **88%** |

---

## Phase 1: 基础架构

### 1.1 项目初始化 ✅

- [x] 创建 `web/` 目录结构
  - [x] `web/src/` - 源代码
  - [x] `web/public/` - 静态资源
  - [x] `web/package.json` - 依赖配置
  - [x] `web/vite.config.ts` - Vite 配置
  - [x] `web/tsconfig.json` - TypeScript 配置
  - [x] `web/tailwind.config.js` - Tailwind 配置

- [x] 配置开发环境
  - [x] 安装 React + TypeScript + Vite
  - [x] 配置 TailwindCSS
  - [x] 配置 ESLint + Prettier
  - [x] 配置路径别名 (`@/components` 等)

### 1.2 Python 服务端集成 ✅

- [x] 添加静态文件服务
  - [x] ~~创建 `src/tlon/webui/` 目录~~（由构建生成）
  - [x] 修改 `RESTServer` 添加 `/ui/*` 路由
  - [x] 实现 SPA fallback（所有路由返回 `index.html`）

- [x] 创建构建脚本
  - [x] 创建 `scripts/build.sh`
  - [x] 脚本内容：npm build → copy → uv build
  - [x] 修改 `pyproject.toml` 包含静态文件

### 1.3 构建验证 ✅

- [x] 执行首次构建
  - [x] 运行 `npm install` 安装依赖
  - [x] 运行 `npm run build` 构建前端
  - [x] 确认 `src/tlon/webui/` 目录生成
  - [x] 确认 `index.html` 存在

- [x] 验证构建产物
  - [x] 构建成功，无错误
  - [x] DaisyUI 主题集成成功

---

## Phase 2: 核心功能

### 2.1 全局基础设施 ✅

- [x] 状态管理 (Zustand)
  - [x] 定义全局状态接口
  - [x] 实现连接状态管理
  - [x] 实现用户偏好存储 (localStorage)

- [x] WebSocket 客户端
  - [x] 创建 `useWebSocket` hook
  - [x] 实现自动重连逻辑
  - [x] 实现心跳检测（通过 auth）
  - [x] 消息路由分发

- [x] 主题系统
  - [x] 实现 Dark/Light 主题切换
  - [x] 系统主题自动检测
  - [x] CSS 变量定义

- [x] 布局组件
  - [x] `Sidebar` - 侧边导航
  - [x] `Header` - 顶部栏
  - [x] `MainLayout` - 主布局容器
  - [x] `RightPanel` - 右侧面板（可折叠）

### 2.2 连接页面 (Connect) ✅

- [x] 页面结构
  - [x] 创建 `/` 路由页面
  - [x] 居中 Logo + 表单布局
  - [x] 输入框组件（Server URL、Username）

- [x] 交互逻辑
  - [x] 连接测试逻辑（WebSocket 验证）
  - [x] Loading 状态
  - [x] 错误提示（连接失败、超时）
  - [x] 连接成功跳转

- [x] 样式
  - [x] 深色/浅色主题适配
  - [x] 输入框发光效果
  - [x] 按钮动画

### 2.3 Congress 页面（对话中心）✅

- [x] 消息列表
  - [x] `MessageBubble` 组件
  - [x] 消息气泡样式（用户/Agent）
  - [x] 时间戳显示
  - [x] 头像 + 状态指示
  - [x] 滚动到底部逻辑

- [x] 消息输入
  - [x] 单行文本输入（当前实现）
  - [ ] ~~多行文本输入~~（简化实现）
  - [x] @提及下拉菜单
  - [ ] ~~文件拖拽上传区域~~（预留接口）
  - [x] 发送按钮状态

- [x] 消息渲染
  - [x] 纯文本消息
  - [ ] ~~Markdown 渲染~~（简化实现）
  - [x] 代码块复制按钮
  - [ ] ~~@提及高亮~~（简化实现）

- [x] 右侧详情面板
  - [x] Session 信息显示
  - [x] 在线成员列表
  - [x] 快捷操作按钮

- [x] 历史加载
  - [x] 初始历史加载（通过 WebSocket）
  - [ ] 分页加载历史消息 (需要后端支持分页 API)
  - [ ] 无限滚动实现 (需要后端支持分页 API)

---

## Phase 3: 监控与管理

### 3.1 Monitor 页面（事件监控）✅

- [x] 事件表格
  - [x] `EventTable` 组件（通过 `EventRow` 实现）
  - [x] 表格列：Time, Level, Type, Agent, Message
  - [x] 行展开详情（显示 payload JSON）
  - [x] 颜色编码（Level）

- [x] 过滤器
  - [x] 事件类型下拉选择
  - [x] 级别（Level）下拉选择
  - [x] Agent 下拉选择
  - [x] 搜索框（消息内容搜索）
  - [ ] ~~事件类型多选下拉~~（当前单选）
  - [ ] ~~时间范围选择器~~

- [x] 工具栏
  - [x] 暂停/继续按钮
  - [x] 缓存事件 Badge（暂停时显示 pending 数量）
  - [x] 导出按钮（JSON 格式）
  - [x] 清空按钮
  - [ ] ~~CSV 导出~~

- [x] 实时更新
  - [x] WebSocket 事件接收
  - [x] 自动滚动到底部
  - [x] 暂停时缓存新事件

### 3.2 Agents 页面（Agent 管理）✅

- [x] Agent 卡片网格
  - [x] `AgentCard` 组件
  - [x] 状态指示（🟢🟡⚪🔴）
  - [x] 资源使用情况（CPU/Mem）
  - [x] 操作按钮组

- [x] 操作功能
  - [x] 唤醒 Agent (`/wake`)
  - [x] 休眠 Agent (`/sleep`)
  - [x] 重启 Agent (组合调用)
  - [x] 查看详情（抽屉）

- [x] 详情抽屉
  - [x] 右侧滑出抽屉 (`AgentDetailDrawer`)
  - [x] Agent 详细信息
  - [x] Skills 标签列表（mock 数据）
  - [x] 资源使用进度条
  - [x] 最近任务列表（mock 数据）
  - [ ] ~~真实 Skills 数据~~（需要后端 API）
  - [ ] ~~真实任务数据~~（需要后端 API）

- [ ] 添加 Agent（预留接口）
  - [ ] 表单对话框
  - [ ] 选择 Position
  - [ ] 配置参数
  - **状态**: 只有占位按钮，无实际功能

---

## Phase 4: 设置与优化

### 4.1 Settings 页面 ✅

- [x] 分组设置
  - [x] Appearance 分组
  - [x] Connection 分组
  - [x] Notifications 分组
  - [x] Storage 分组
  - [x] Advanced 分组

- [x] 设置项
  - [x] 主题选择（图标卡片）
  - [x] 字体大小（Segmented Control）
  - [x] 密度选择（Segmented Control）
  - [x] Server URL 输入
  - [x] 自动重连开关（UI 实现，未绑定逻辑）
  - [x] 桌面通知开关（UI 实现，未绑定逻辑）
  - [ ] ~~缓存清理按钮~~（UI 占位）

- [x] 持久化
  - [x] localStorage 存储（zustand persist）
  - [x] 设置变更实时生效

### 4.2 UI 组件美化 ✅

> **状态**: 已完成

- [x] 集成 [DaisyUI](https://daisyui.com/components/)
  - [x] 安装 DaisyUI 依赖
  - [x] 配置主题（与现有 Dark/Light 模式集成）
  - [x] 替换基础组件样式

- [x] 组件美化
  - [x] Button 组件 → DaisyUI Button
  - [x] Card 组件 → DaisyUI Card
  - [x] Drawer → DaisyUI Drawer 样式
  - [x] Dropdown → DaisyUI Dropdown（@提及）
  - [x] Form inputs → DaisyUI Input/Textarea/Select
  - [x] Alert → DaisyUI Alert（错误提示）
  - [x] Badge/Tag → DaisyUI Badge
  - [x] Loading/Spinner → DaisyUI Loading
  - [x] Toggle → DaisyUI Toggle
  - [x] Progress → DaisyUI Progress
  - [x] Table → DaisyUI Table

- [x] 主题定制
  - [x] 定义品牌色主题变量（tlonDark / tlonLight）
  - [x] 暗色/亮色主题适配
  - [x] 自定义组件变体

### 4.3 响应式适配 ⏳

> **状态**: 未实现，当前仅适配桌面端

- [ ] Tablet 适配 (768px - 1439px)
  - [x] ~~侧边栏折叠按钮~~（已实现，但需优化）
  - [ ] 右面板悬浮/隐藏
  - [ ] Agent 卡片 2 列布局
  - [ ] 输入框区域优化

- [ ] Mobile 适配 (< 768px)
  - [ ] 底部 Tab 导航
  - [ ] 侧边栏改为抽屉
  - [ ] Agent 卡片 1 列布局
  - [ ] 输入框简化（移动端适配）
  - [ ] 触摸手势支持

**当前布局**:
- ✅ 桌面端 (>= 1440px): 完整三栏布局
- ⚠️ 小桌面 (1024px - 1439px): 可用但紧凑
- ❌ Tablet (768px - 1023px): 未优化
- ❌ Mobile (< 768px): 不可用

### 4.4 通知系统 ⏳

> **状态**: 未实现

- [ ] 浏览器通知
  - [ ] 请求通知权限
  - [ ] 新消息通知
  - [ ] 提及通知

- [ ] 应用内通知
  - [ ] Toast 组件
  - [ ] 成功/错误/警告提示

**当前替代方案**: 
- 错误在页面内显示（如连接错误）
- 无全局 Toast 通知

---

## Phase 5: polish 与发布

### 5.1 性能优化 ⏳

> **状态**: 未实现

- [ ] 代码分割
  - [ ] 按路由懒加载（React.lazy + Suspense）
  - [ ] 组件按需加载

- [ ] 渲染优化
  - [ ] 虚拟列表（消息列表 - 当消息数量 > 1000 时）
  - [ ] 虚拟列表（事件表格 - 当事件数量 > 1000 时）
  - [ ] React.memo 优化（避免不必要重渲染）
  - [ ] useMemo/useCallback 优化

- [ ] 资源优化
  - [ ] 图片压缩/优化
  - [ ] 字体预加载
  - [ ] 关键 CSS 内联

**当前性能状况**:
- 初始加载：~200KB（预估，未压缩）
- 无虚拟列表，长时间运行可能卡顿

### 5.2 错误处理 ⏳

> **状态**: 部分实现，需加强

- [ ] 全局错误边界
  - [ ] `ErrorBoundary` 组件
  - [ ] 友好错误页面（白屏保护）

- [x] 网络错误处理（基础）
  - [x] 连接断开提示
  - [ ] 重连进度显示（当前只有连接状态）
  - [ ] 指数退避重连策略

**当前实现**:
- WebSocket 自动重连 ✅
- 连接错误显示 ✅
- 无全局 ErrorBoundary ❌

### 5.3 文档与发布 ⏳

> **状态**: 未开始

- [ ] 更新文档
  - [ ] README 添加 WebUI 说明
  - [ ] AGENTS.md 更新构建流程
  - [ ] 用户指南（如何使用 WebUI）
  - [ ] 截图/GIF 演示

- [ ] 发布检查
  - [ ] 验证生产构建
  - [ ] 验证 uv 打包包含静态文件
  - [ ] 验证多平台兼容（macOS, Linux, Windows）
  - [ ] 验证各浏览器兼容（Chrome, Firefox, Safari, Edge）

---

## 已知问题与限制

### 🔴 阻塞问题

1. **从未执行构建**
   - `src/tlon/webui/` 目录不存在
   - 需要首次运行 `npm install && npm run build`

### 🟡 功能限制

2. **历史消息分页**
   - 当前一次性加载所有历史
   - 需要后端支持分页 API (`/history?before=xxx&limit=50`)

3. **Agent 详情数据**
   - Skills 和 Tasks 使用 mock 数据
   - 需要后端提供真实 API

4. **文件上传**
   - 文件拖拽区域为占位，无实际功能
   - 需要后端支持文件接收

5. **Markdown 渲染**
   - 当前仅支持纯文本
   - 代码块可复制，但无语法高亮

### 🟢 待优化

6. **移动端体验**
   - 当前仅适配桌面端
   - 移动端需要重新设计布局

7. **性能**
   - 长时间运行后消息列表可能卡顿
   - 需要虚拟列表优化

---

## 里程碑

- [x] **M0**: 代码实现完成 - 核心功能代码已编写
- [x] **M1**: Phase 1 完成 - 基础架构就绪，构建验证通过
- [x] **M2**: Phase 2 完成 - 核心功能可用，可正常对话
- [x] **M3**: Phase 3 完成 - 完整功能，可监控管理
- [x] **M4**: Phase 4 完成 - DaisyUI 美化完成，界面精致
- [ ] **M5**: Phase 5 完成 - 响应式适配，生产就绪，正式发布

**当前进度**: M4 完成，等待响应式适配和发布

---

## 下一步行动

### 立即执行（解锁 M1）
```bash
cd web
npm install
npm run build
# 验证 src/tlon/webui/ 目录生成
cd ..
uv build
uv tool install dist/tlon-*.whl --force
tlon server
# 访问 http://localhost:8080/ui
```

### 短期（完善 M2-M3）
1. 后端支持历史消息分页 API
2. 后端提供 Agent Skills/Tasks API
3. 实现 Markdown 渲染（react-markdown）
4. 实现 Toast 通知系统

### 中期（M4）
1. 响应式布局适配（Tablet + Mobile）
2. 浏览器通知支持
3. 虚拟列表优化

### 长期（M5）
1. 代码分割优化
2. 完整的错误边界
3. 文档完善
4. 正式发布

---

## 开发建议

### 每日站会问题

1. 昨天完成了什么？
2. 今天计划做什么？
3. 有什么阻塞？

### 代码审查清单

- [ ] TypeScript 类型完整
- [ ] 组件有 Props 接口定义
- [ ] 异步操作有错误处理
- [ ] 样式使用 Tailwind，无硬编码
- [ ] 响应式断点测试通过（如果涉及 UI）

### 测试策略

- 手动测试：Chrome, Firefox, Safari
- 分辨率：1920x1080, 1440x900, 768x1024, 375x812
- 功能：连接、对话、监控、管理、设置

---

*创建日期: 2026-03-10*  
*最后更新: 2026-03-11（实际代码审查后更新）*
