> ⚠️ **注意**: Skill 系统当前实现已废弃，计划重新设计。本文档仅供参考。

# Agent Skills 开发指南

本指南介绍如何为 Tlon 系统开发符合 [Agent Skills](https://github.com/agentskills/agentskills) 标准的技能。

## 目录

1. [技能结构](#技能结构)
2. [SKILL.md 格式](#skillmd-格式)
3. [脚本开发](#脚本开发)
4. [验证与测试](#验证与测试)
5. [最佳实践](#最佳实践)

---

## 技能结构

每个技能是一个独立的目录，遵循以下结构：

```
skills/
└── my-skill/              # 技能目录名（kebab-case）
    ├── SKILL.md           # 技能定义文件（必需）
    ├── scripts/           # 可执行脚本（可选）
    │   ├── script1.sh
    │   └── script2.py
    ├── references/        # 参考文档（可选）
    │   └── api-reference.md
    └── assets/            # 资源文件（可选）
        └── template.json
```

### 目录说明

| 路径 | 必需 | 说明 |
|------|------|------|
| `SKILL.md` | ✅ | 技能定义文件，包含元数据和使用说明 |
| `scripts/` | ❌ | 可执行脚本目录 |
| `references/` | ❌ | 参考文档目录 |
| `assets/` | ❌ | 资源文件目录（图片、模板等）|

---

## SKILL.md 格式

SKILL.md 采用 YAML Frontmatter + Markdown 格式。

### 基本结构

```markdown
---
name: my-skill
description: 简短描述这个技能的功能（20-300字符）
metadata:
  author: your-name
  version: "1.0.0"
---

# My Skill

详细描述这个技能的用途和使用场景。

## When to Use

- 何时使用此技能
- 使用场景示例

## Prerequisites

- 需要的依赖项
- 环境要求

## Available Scripts

### script1.sh

**功能**: 描述这个脚本的作用

**用法**:
```bash
bash /skills/my-skill/scripts/script1.sh [options] <argument>
```

**参数**:
- `argument`: 参数说明
- `--option`: 选项说明

**示例**:
```bash
bash /skills/my-skill/scripts/script1.sh --verbose data.txt
```

## Error Handling

描述脚本的错误输出格式和退出码。
```

### Frontmatter 字段

#### 必需字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 技能名称（kebab-case，如 `file-operations`）|
| `description` | string | 简短描述（20-300字符）|

#### 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `metadata` | object | 扩展元数据 |
| `license` | string | 许可证信息 |
| `disable-model-invocation` | boolean | 是否禁用模型调用 |

### 名称规范

- 使用 **kebab-case**（小写字母+连字符）
- 简洁、有意义
- 与目录名保持一致

**示例**:
- ✅ `file-operations`
- ✅ `git-operations`
- ✅ `csv-pipeline`
- ❌ `FileOperations` (不是 kebab-case)
- ❌ `file_operations` (使用下划线)

---

## 脚本开发

### 脚本路径

在 Docker 容器中，skills 目录挂载于 `/skills`，脚本路径格式：

```bash
# Bash 脚本
bash /skills/{skill-name}/scripts/{script-name}.sh [args...]

# Python 脚本
python3 /skills/{skill-name}/scripts/{script-name}.py [args...]

# PEP 723 脚本（带内联依赖）
uv run /skills/{skill-name}/scripts/{script-name}.py
```

### 输出格式

脚本输出应遵循统一格式，便于 Agent 解析：

```bash
#!/bin/bash

# JSON 输出格式
{
  "success": true/false,
  "data": { ... },      # 成功时的数据
  "error": "..."        # 失败时的错误信息
}
```

### 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 一般错误 |
| 2 | 文件/路径不存在 |
| 3 | 权限拒绝 |

### Python 脚本示例

```python
#!/usr/bin/env python3
"""示例技能脚本。"""

import argparse
import json
import sys


def do_something(arg1: str, arg2: int) -> dict:
    """执行操作并返回结果。"""
    try:
        # ... 业务逻辑 ...
        return {
            "success": True,
            "data": {
                "result": "success",
                "value": arg2 * 2
            }
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "code": 1
        }


def main():
    parser = argparse.ArgumentParser(description="示例脚本")
    parser.add_argument("input", help="输入参数")
    parser.add_argument("--count", type=int, default=1, help="计数")
    
    args = parser.parse_args()
    
    result = do_something(args.input, args.count)
    
    # 输出 JSON
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # 根据结果设置退出码
    sys.exit(0 if result["success"] else result.get("code", 1))


if __name__ == "__main__":
    main()
```

### Bash 脚本示例

```bash
#!/bin/bash
#
# 示例技能脚本
#

set -e

# 输出 JSON 的辅助函数
output_json() {
    local success="$1"
    local data="$2"
    local error="$3"
    
    if [ "$success" = "true" ]; then
        echo "{\"success\": true, \"data\": $data}"
    else
        echo "{\"success\": false, \"error\": \"$error\", \"code\": 1}"
    fi
}

# 检查参数
if [ $# -lt 1 ]; then
    output_json false "" "Usage: $0 <input>"
    exit 1
fi

INPUT="$1"

# 执行业务逻辑
if [ -f "$INPUT" ]; then
    output_json true "{\"file\": \"$INPUT\", \"exists\": true}"
else
    output_json false "" "File not found: $INPUT"
    exit 2
fi
```

### PEP 723 脚本（推荐）

Python 脚本可以使用 [PEP 723](https://peps.python.org/pep-0723/) 内联依赖声明：

```python
#!/usr/bin/env python3
# /// script
# dependencies = [
#   "requests>=2.25.0",
#   "beautifulsoup4",
# ]
# ///

import requests
from bs4 import BeautifulSoup

# 脚本会自动安装依赖
response = requests.get("https://example.com")
print(response.status_code)
```

执行方式：
```bash
uv run /skills/my-skill/scripts/web-scraper.py
```

---

## 验证与测试

### 使用验证工具

```bash
# 验证所有技能
uv run python -m tlon.skills.validator

# 验证指定目录
uv run python -m tlon.skills.validator ./skills

# 验证单个技能
uv run python -m tlon.skills.validator ./skills/my-skill
```

### 手动测试脚本

```bash
# 直接运行脚本测试
cd /path/to/tlon
python3 skills/my-skill/scripts/my-script.py --help

# 在 Docker 容器中测试
docker exec tlon-agent-dev-001 bash /skills/my-skill/scripts/my-script.sh
```

---

## 最佳实践

### 1. 单一职责

每个技能应专注于一个特定领域：

- ✅ `file-operations`: 文件操作
- ✅ `git-operations`: Git 操作
- ❌ `utils`: 过于宽泛

### 2. 清晰的文档

- 在 SKILL.md 中详细说明使用场景
- 提供完整的脚本使用示例
- 说明错误处理和退出码

### 3. 安全的脚本

- 验证所有输入参数
- 处理边界情况（空值、特殊字符）
- 避免执行用户提供的未过滤输入

### 4. 用户友好的错误

```python
# 好的错误信息
return {
    "success": False,
    "error": "File not found: /workspace/config.yaml. Use --create-dirs to create parent directories.",
    "code": 2
}

# 差的错误信息
return {
    "success": False,
    "error": "Error",
    "code": 1
}
```

### 5. 避免交互式提示

脚本不应等待用户输入：

```python
# ❌ 错误 - 会挂起
user_input = input("Enter value: ")

# ✅ 正确 - 使用命令行参数
parser.add_argument("--value", required=True)
```

### 6. 幂等性

脚本应该是幂等的（多次运行结果一致）：

```bash
# 好的做法 - 检查存在性
if [ ! -d "$DIR" ]; then
    mkdir -p "$DIR"
fi

# 或者使用 -p 自动处理
mkdir -p "$DIR"
```

---

## 示例技能

参考项目中已有的技能：

| 技能 | 说明 |
|------|------|
| `file-operations` | 文件读写、目录列表、文件删除 |
| `git-operations` | Git 克隆、提交、推送、分支操作 |
| `csv-pipeline` | CSV 数据处理和分析 |
| `plan-i` | 规划文档创建 |
| `frontend-design` | 前端设计指南 |

---

## 更多信息

- [Agent Skills 标准](https://github.com/agentskills/agentskills)
- [SKILL.md 规范](../../../skills/doc/specification.mdx)
- [脚本使用指南](../../../skills/doc/using-scripts.mdx)
