# 记忆回归集（ADR 0017 阶段 5）

记忆系统的手动回归门禁：**每次动记忆系统（`src/tlon/memory/`、蒸馏/检索/注入相关调用方）必跑**。
跑的是真实蒸馏 + 真实向量检索的端到端链路，不是 mock 测试。

## 为什么不进 CI

- 需要真实 LLM 与 embedding 调用——花钱、要 API key；
- LLM 蒸馏与冲突判定有固有波动，不适合做硬门禁（脚本允许单场景重试一次再判 FAIL）；
- 定位是「人看着跑」的回归工具：逐题 PASS/FAIL、失败时打印实际检索内容，供人判断是回归还是波动。

## 前置条件

1. 蒸馏/判定模型的 API key：按 `{PROVIDER}_API_KEY` 约定放 `~/.tlon/.env`（真实环境变量优先），如 `DEEPSEEK_API_KEY`；
2. **embedding 模型必须显式指定**（JIT 检索无从降级评测，缺失直接报错退出）：`--embedding-model provider/model`，对应 provider 的 key 同样在 `~/.tlon/.env`。

## 运行

```bash
# 全量
uv run python scripts/eval_memory.py \
    --model deepseek/deepseek-chat \
    --embedding-model openai/text-embedding-3-small

# 只看场景清单（不触网）
uv run python scripts/eval_memory.py --list

# 只跑一个维度 / 单场景 / 失败不重试
uv run python scripts/eval_memory.py --embedding-model ... --dimension knowledge-update
uv run python scripts/eval_memory.py --embedding-model ... --scenario update-cloud-migration
uv run python scripts/eval_memory.py --embedding-model ... --no-retry
```

`--model` / `--embedding-model` 也可用环境变量 `TLON_EVAL_MODEL` / `TLON_EVAL_EMBEDDING_MODEL`；
私有端点用 `--api-base`（同时作用于 chat 与 embedding）。
退出码：全过 0，有挂 1，用法/配置错误 2——可脚本化。

## 四个维度（对齐 LongMemEval）

| 维度 | 含义 | 断言方式 |
| --- | --- | --- |
| `fact-recall` | run 里埋一个具体事实，相关 query 应检索命中 | `must_contain` 命中 |
| `knowledge-update` | 新事实取代矛盾旧事实（冲突判定 supersede） | 出新 `must_contain`，旧 `must_not_contain`（失效不删除，但不得进注入/检索结果） |
| `temporal` | 多时间点状态变化，问「当前/最新」应得最新值 | 最新值 `must_contain`，历史值 `must_not_contain`（valid_from/valid_to 端到端） |
| `refusal` | 问记忆里不存在的话题 | 不编造（`must_not_contain` 问话题词）、空记忆报空（`expect_empty`）、top-k 选择性（无关 claim 不进注入面） |

## 场景格式（scenarios.json）

```json
{
  "name": "update-cloud-migration",
  "dimension": "knowledge-update",
  "runs": [
    { "ts": "2026-07-01T10:00:00", "trigger": "用户来信原文", "responses": ["agent 回复"] }
  ],
  "query": "评测时的检索 query",
  "expect": {
    "surface": "both",
    "must_contain": ["腾讯", ["周三", "星期三"]],
    "must_not_contain": ["阿里"],
    "expect_empty": false
  }
}
```

- `runs[].ts`：run 的 `run/start` 时间戳。**蒸馏的 pending 判定靠它与水位线比对**——时序/更新类场景靠递增 ts 控制蒸馏顺序；每个 run 完成即蒸馏（评测 window=1）。
- `expect.surface`：断言面——`injection`（`build_context_blocks`，JIT 注入面）、`search`（`search_memories`，检索面）、`both`（默认）。
- `must_contain`：字符串（必须出现）或字符串列表（任一出现即可——容忍 LLM 措辞差异，如 `["3 楼", "三楼"]`）。
- `must_not_contain`：任何一面出现即 FAIL。检索面的 query 回显行（`搜索结果 for '...'`）在断言前会被剥离，query 本身的措辞不会误判。
- `expect_empty`：检索面必须为空或「未找到相关记忆」。

## 加新场景

往 `scenarios.json` 加一条，保持 name 唯一、dimension 取四个之一即可。写断言时注意：

- 新 run 的 trigger 里**不要提及要被取代的旧值**（否则蒸馏出的新 claim 自带旧值，`must_not_contain` 必然挂）；
- `must_contain` 用蒸馏大概率会保留的具体 token（数字、专名、工具名），措辞有歧义的用「任一出现」列表；
- 断言针对的是检索面文本（claim 行），不是 LLM 回答——评测不验证「agent 答得对不对」，只验证「该出现的记忆出现了、不该出现的没出现」。

## 已知局限

- **评测压缩在真实时间的一刻**：所有 claim 的落盘时间都是运行时，recency 衰减无法区分场景内的新旧——时序维度实际验证的是冲突链 supersede 与 query 语义，不是时间衰减。
- 拒答的「选择性」场景依赖 embedding 排序质量，天然比其它维度更容易波动；挂了先看失败输出里是哪个无关 claim 进了 top-k，再判断是回归还是边界波动。
