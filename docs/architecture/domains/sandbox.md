# Sandbox 层

> 状态: ✅ 已验证
> 最后验证: 2026-08-13

---

## 职责

Agent 命令执行环境：每 agent 一个 Docker 容器，提供命令执行与文件 I/O，
容器懒启动、跨 session 持久。Agent 自身不接触宿主机文件系统。

---

## 模块结构

```
sandbox/
└── docker_runtime.py    # DockerRuntime（全部实现）+ ExecutionResult dataclass
```

仅两个文件（含空 `__init__.py`），无子包。

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `DockerRuntime` | 每 agent 一个实例：容器生命周期 + execute + 文件 I/O | `sandbox/docker_runtime.py:55` |
| `ExecutionResult` | 执行结果（exit_code/stdout/stderr） | `sandbox/docker_runtime.py:46` |
| `DockerRuntimeConfig` | 容器配置（pydantic），来自 `AgentConfig.docker` | `base/config.py:110-117` |

---

## 每 agent 一个实例 + 懒启动

- 构造点：`AgentRegistry._build_cognitive`（`agents/agent_registry.py:570-575`）
  ——构造失败只记 `docker_unavailable` 警告、runtime 置 None（工具报
  "Docker runtime not initialized" 而非启动失败）；**只构造不启动容器**。
  同一实例共享注入 shell + filesystem 工具。
- 懒启动：容器独立、带全网络访问（network_mode 默认 host）、跨 session 持久；
  `execute` / `read_file` / `write_file` / `file_exists` 在 container 为空时先
  `ensure_running()`（:201-202, :337, :369, :409）。纯对话唤醒零容器成本。
- `ensure_running()`（:122）幂等：已存在则复用、停了则 start（`asyncio.sleep(1)`
  等就绪）、NotFound 则 `_create_container`。

## execute 双层超时（:188）

docker SDK 阻塞调用走 `asyncio.to_thread`；双层超时——容器内 `timeout` 命令
真杀进程（超时线程无法被取消），外层 `asyncio.wait_for(timeout+5)` 兜底；
exit 124 归一为超时错误；`demux=True` 分离 stdout/stderr。

## 文件 I/O（容器内无需任何软件）

- `read_file`（:322）：容器内 `cat`（symlink 自动跟随），按 stderr 判别
  FileNotFoundError / RuntimeError；
- `write_file`（:352）：docker SDK `put_archive` + 内存 tar，自动 `mkdir -p`——
  容器内无需 python3/base64 等任何软件；
- `file_exists`（:400）：`test -e ... && echo EXISTS`。

另有 `stop()`（:260）/ `remove(force)`（:269）/ `is_running`（:283）/
`get_logs`（:297）。

## macOS socket 探测（`_create_client`，:86-120）

按序：先试 `docker.from_env()`（吃 DOCKER_HOST，Colima/Docker Desktop 都走这里）
→ 再按序探测 socket 路径 `~/.colima/default/docker.sock`、
`~/.docker/run/docker.sock`、`/var/run/docker.sock`、`~/.colima/docker.sock`
（:102-107）→ 兜底再 `from_env()` 让它抛原始错。

## 容器命名与配置

- 容器名约定：`tlon-agent-{agent_id}`（:82）。**没有 label 约定**
  （`containers.run` 未传 labels）。
- 容器形态：镜像本地没有就 pull；`containers.run(command="sleep infinity",
  detach, tty, stdin_open)`；卷映射 = 宿主机
  `paths.agent_workspace_dir(agent_id)`（`~/.tlon/{id}/workspace`，
  base/paths.py:119）→ 容器 `config.working_dir`；mem_limit + cpu_period/quota
  限额；镜像原样使用，运行时不安装任何软件。
- 配置来源：`DockerRuntimeConfig`（`base/config.py:110-117`）——
  `image="ubuntu:22.04"`、`working_dir="/workspace"`、`memory_limit="512m"`、
  `cpu_limit=1.0`、`network_mode="host"`；来自 `AgentConfig.docker`。

## 已知债

`stop` / `remove` 全仓**零调用方**（grep `docker_runtime.(stop|remove)` 无生产
代码命中）——agent 停机/回收时容器不被停止或删除，容器泄漏清理见 issue 。

## 依赖边界

import-linter 契约 "sandbox 不依赖 agents/organization/memory/app/tools/prototypes"
（pyproject.toml:193-204）——sandbox 只依赖 `tlon.base`。

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [agents.md](./agents.md) | 装配点与容器懒启动的调用侧（`_build_cognitive` / `_start_agent`） | L2 |

---

## 验证清单

- [x] 懒启动路径（ensure_running 幂等）与代码一致
- [x] execute 双层超时与 exit 124 归一与代码一致
- [x] read/write/file_exists 实现方式与代码一致
- [x] macOS socket 探测顺序与 `_create_client` 一致
- [x] 容器命名约定（无 label）与 `DockerRuntimeConfig` 默认值与代码一致
