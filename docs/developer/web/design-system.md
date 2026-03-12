# Tlon WebUI 设计系统

> 统一的视觉语言与设计规范

---

## 1. 设计原则

### 1.1 核心理念

- **清晰**：信息层次分明，用户一眼找到重点
- **一致**：相同元素保持统一的表现形式
- **高效**：减少认知负担，快速完成任务
- **美观**：现代化设计，专业但不冰冷

### 1.2 设计哲学

```
博尔赫斯美学 × 工程精确性
诗意隐喻 + 功能清晰
```

---

## 2. Layout System（布局系统）

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│  Header (64px)                                              │
├──────────┬──────────────────────────────────────────────────┤
│          │                                                  │
│ Sidebar  │              Main Content                        │
│ (80px)   │              (自适应)                            │
│          │                                                  │
│          │                                                  │
└──────────┴──────────────────────────────────────────────────┘
```

### 2.2 间距系统（8px Grid）

| Token | Value | Usage |
|-------|-------|-------|
| `space-1` | 4px  | 图标内边距、紧凑间距 |
| `space-2` | 8px  | 组件内部间距 |
| `space-3` | 12px | 小卡片内边距 |
| `space-4` | 16px | 标准内边距 |
| `space-6` | 24px | 区块间距 |
| `space-8` | 32px | 大区块间距 |
| `space-12` | 48px | Section 间距 |

```css
/* 使用示例 */
.card { @apply p-4; }        /* 16px 内边距 */
.section { @apply py-12; }   /* 48px 垂直间距 */
.stack > * + * { @apply mt-4; }  /* 16px 堆叠间距 */
```

### 2.3 响应式断点

| Breakpoint | Width | Target |
|------------|-------|--------|
| `xs` | 480px | 小屏手机 |
| `sm` | 640px | 手机横屏 |
| `md` | 768px | 平板 |
| `lg` | 1024px | 小桌面 |
| `xl` | 1280px | 桌面 |
| `2xl` | 1536px | 大桌面 |

```tsx
// 使用示例
<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
```

### 2.4 容器系统

```tsx
// 页面容器
<div className="page-container">
  {/* 最大宽度 1280px，自动居中，响应式内边距 */}
</div>

// 等效于
<div className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
```

### 2.5 网格系统

```tsx
// 卡片网格（自适应）
<div className="card-grid">
  {/* 1列 → 2列 → 3列 */}
</div>

// 自定义网格
<div className="grid grid-cols-12 gap-4">
  <div className="col-span-12 lg:col-span-8">主内容</div>
  <div className="col-span-12 lg:col-span-4">侧边栏</div>
</div>
```

### 2.6 Z-Index 层级

| Layer | Z-Index | Usage |
|-------|---------|-------|
| Base | 0-10 | 普通内容 |
| Sticky | 20 | 粘性头部 |
| Dropdown | 30 | 下拉菜单 |
| Modal | 100 | 模态框 |
| Popover | 200 | 浮层 |
| Tooltip | 300 | 提示 |
| Toast | 400 | 通知 |

---

## 3. Typography System（排版系统）

### 3.1 字体栈

```css
/* 主字体 */
font-family: 'Inter', 'Noto Sans SC', system-ui, -apple-system, sans-serif;

/* 等宽字体（代码、数据） */
font-family: 'JetBrains Mono', 'Fira Code', SF Mono, Monaco, monospace;
```

### 3.2 字体层级

| Style | Size | Line Height | Weight | Usage |
|-------|------|-------------|--------|-------|
| Display | 36px | 40px | 700 | 页面大标题 |
| Display SM | 30px | 36px | 700 | 区块标题 |
| Title | 24px | 32px | 600 | 卡片标题 |
| Title SM | 20px | 28px | 600 | 小标题 |
| Subtitle | 18px | 28px | 500 | 副标题 |
| Body | 16px | 24px | 400 | 正文 |
| Body SM | 14px | 20px | 400 | 辅助文字 |
| Caption | 12px | 16px | 400 | 说明文字 |
| Meta | 12px | 16px | 500 | 标签、徽章 |

```tsx
// 使用预定义类
<h1 className="text-display">大标题</h1>
<h2 className="text-title">卡片标题</h2>
<p className="text-body">正文内容</p>
<span className="text-caption">说明文字</span>
```

### 3.3 中文排版优化

```css
/* 自动应用于 :lang(zh) */
line-height: 1.75;        /* 更大行高 */
text-align: justify;      /* 两端对齐 */
word-wrap: break-word;    /* 自动换行 */
```

### 3.4 响应式排版

```tsx
// 标题随屏幕缩小
<h1 className="text-responsive-4xl">
  {/* 手机: 24px → 平板: 30px → 桌面: 36px */}
</h1>

// 手动控制
<h2 className="text-xl sm:text-2xl md:text-3xl">
```

---

## 4. Color System（色彩系统）

### 4.1 主题色

```
Primary:    #6366f1 (Indigo-500)  - 品牌主色
Secondary:   (Slate-700)  - 次要色
Accent:     #10b981 (Emerald-500) - 强调色
```

### 4.2 语义色

| Color | Dark | Light | Usage |
|-------|------|-------|-------|
| Success | #10b981 |  | 成功状态 |
| Warning | #f59e0b | #d97706 | 警告状态 |
| Error | #ef4444 | #dc2626 | 错误状态 |
| Info | #3b82f6 | #2563eb | 信息提示 |

### 4.3 背景层级（Dark Mode）

| Layer | Color | Usage |
|-------|-------|-------|
| base-100 | #0f172a | 最底层背景 |
| base-200 | #1e293b | 卡片背景 |
| base-300 |  |  elevated 背景 |

---

## 5. Component Patterns（组件模式）

### 5.1 卡片模式

```tsx
// 基础卡片
<div className="card bg-base-200 border border-base-300 shadow-sm">
  <div className="card-body p-5">
    {/* 内容 */}
  </div>
</div>

// 可交互卡片
<div className="card bg-base-200 border border-base-300 
              hover:shadow-xl hover:border-primary/30 
              transition-all duration-300">
```

### 5.2 按钮层级

```tsx
// 主要操作
<button className="btn btn-primary">主要按钮</button>

// 次要操作
<button className="btn btn-secondary">次要按钮</button>

// 幽灵按钮
<button className="btn btn-ghost">幽灵按钮</button>

// 危险操作
<button className="btn btn-error">删除</button>
```

### 5.3 表单模式

```tsx
// 表单控件
<div className="form-control">
  <label className="label">
    <span className="label-text">标签</span>
  </label>
  <input className="input input-bordered" />
  <label className="label">
    <span className="label-text-alt">辅助文字</span>
  </label>
</div>
```

---

## 6. Animation System（动画系统）

### 6.1 时长规范

| Duration | Usage |
|----------|-------|
| 75ms | 微小反馈（按钮点击）|
| 150ms | 快速过渡（颜色变化）|
| 200ms | 标准过渡（hover）|
| 300ms | 复杂过渡（展开/收起）|
| 500ms | 页面切换 |

### 6.2 缓动函数

```css
/* 标准 */
transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);

/* 进入 */
transition-timing-function: cubic-bezier(0, 0, 0.2, 1);

/* 离开 */
transition-timing-function: cubic-bezier(0.4, 0, 1, 1);

/* 弹性 */
transition-timing-function: cubic-bezier(0.68, -0.55, 0.265, 1.55);
```

### 6.3 动画类

```tsx
// 淡入
<div className="animate-fade-in">

// 从右侧滑入
<div className="animate-slide-in-right">

// 柔和脉冲
<div className="animate-pulse-soft">
```

---

## 7. Iconography（图标系统）

### 7.1 图标尺寸

| Size | Value | Usage |
|------|-------|-------|
| XS | 14px | 内嵌图标 |
| SM | 16px | 按钮内图标 |
| MD | 20px | 标准图标 |
| LG | 24px | 大图标 |
| XL | 32px | 展示图标 |

### 7.2 图标库

```tsx
import { IconName } from 'lucide-react'

// 使用示例
<Home className="w-5 h-5" />
```

---

## 8. Accessibility（无障碍）

### 8.1 色彩对比度

- 正文文字：对比度 ≥ 4.5:1
- 大文字：对比度 ≥ 3:1
- 交互元素：对比度 ≥ 3:1

### 8.2 焦点样式

```css
*:focus-visible {
  outline: 2px solid hsl(var(--p));
  outline-offset: 2px;
}
```

### 8.3 减少动画

```css
@media (prefers-reduced-motion: reduce) {
  /* 禁用动画 */
}
```

---

## 9. 使用示例

### 9.1 完整页面布局

```tsx
export default function ExamplePage() {
  return (
    <div className="h-full flex">
      {/* Sidebar */}
      <aside className="w-20 bg-base-200 border-r border-base-300">
        {/* 导航内容 */}
      </aside>
      
      {/* Main */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="h-16 bg-base-200/80 backdrop-blur border-b border-base-300">
          <div className="flex items-center justify-between px-6 h-full">
            <h1 className="text-title">页面标题</h1>
          </div>
        </header>
        
        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          <div className="max-w-6xl mx-auto">
            <div className="card-grid">
              {/* 卡片内容 */}
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
```

### 9.2 组件示例

```tsx
// Agent 卡片
function AgentCard({ agent }: { agent: Agent }) {
  return (
    <div className="card bg-base-200 border border-base-300 
                    hover:shadow-xl hover:border-primary/30 
                    transition-all duration-300">
      <div className="card-body p-5">
        <div className="flex items-center gap-3 mb-4">
          <div className="avatar">
            <div className="w-12 h-12 rounded-xl bg-primary/10 
                          flex items-center justify-center text-2xl">
              🤖
            </div>
          </div>
          <div>
            <h3 className="font-bold text-lg">{agent.name}</h3>
            <span className="badge badge-sm badge-success">运行中</span>
          </div>
        </div>
        
        <p className="text-body-sm text-base-content/60 mb-4">
          {agent.description}
        </p>
        
        <div className="flex gap-2">
          <button className="btn btn-sm btn-primary flex-1">
            唤醒
          </button>
          <button className="btn btn-sm btn-ghost">
            设置
          </button>
        </div>
      </div>
    </div>
  )
}
```

---

## 10. 设计 Token 速查

### Tailwind 类速查

```
Layout:
  p-4, px-6, py-4, m-4, mx-auto, gap-4
  flex, grid, block, inline, hidden
  w-full, h-full, min-h-screen, max-w-7xl

Typography:
  text-sm, text-base, text-lg, text-xl, text-2xl
  font-normal, font-medium, font-semibold, font-bold
  leading-tight, leading-snug, leading-relaxed
  tracking-tight, tracking-wide

Colors:
  bg-base-100, bg-base-200, bg-primary, bg-success
  text-base-content, text-primary, text-error
  border-base-300, border-primary

Effects:
  shadow-sm, shadow, shadow-lg, shadow-xl
  rounded-lg, rounded-xl, rounded-full
  transition-all, duration-200, ease-out
```

---

*最后更新: 2026-03-11*
