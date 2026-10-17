# ADR 0022：社会政策语境——policy.tlon 与 pull 型求值点

> 阅读定位：本文保留当时的选择与理由；当前设计见 [society-policy](../design/society-policy.md)。当前适用范围：社会政策语境与 pull 型求值点。完整关系见 [决策索引](README.md)。

状态：已接受（2026-09-28 拍板）

## 背景

Bridge API（ADR 0020）落地了四个事件触发型求值语境；ADR 0021 赋予 tlosure 完整函数能力。此后 runtime 中仍硬编码的政策公式——fitness 的 tilt/资不抵债（`src/tlon/agents/fitness.py`）与 trust 见习判定（`src/tlon/agents/trust.py`）——具备迁移条件：它们是纯函数，输入社会事实、输出数值/布尔判定，但改政策等于改代码发版。

## 决策

1. **新增第五个求值语境「社会政策语境」**，第一种 pull 型语境：宿主在固定求值点（spawn 前、staging 提案前、assembly 前）主动调用政策 hook，而非事件触发。
2. **政策即数据**：society 级 EDN 文件 `~/.tlon/policy.tlon`，`:policy` 为共享 env 中顺序求值的 form 序列（复用 ADR 0021 的 defn 穿线，不新增加求值机制）；hook 契约是保留函数名（`tilt` / `solvent?` / `full-member?`），逐 hook 缺失回落内嵌默认。
3. **数据物化注入，不设回调查询端口**：社会数据收集是异步 IO，宿主在求值前把原始事实（血缘 token 合计、成员背书计数、spawned_by）物化为 EDN 数据注入；政策函数只读数据。机制/政策切分点：物化原始事实是机制，从事实到判定是政策。
4. **内嵌默认政策**：`DEFAULT_POLICY_EDN` 精确复刻现行公式（ADAM_SPEC_EDN 同款模式），policy.tlon 缺失时行为逐字节不变。
5. **fail-open**：任何求值异常回落安全默认（tilt 1.0 / solvent true / full trust true），政策簿记永不阻塞 spawn 或 assembly；provider 接口签名不变，调用方零改动。
6. **人类专写**：policy.tlon 位于 ~/.tlon 根，不进任何成员 allowed_paths，不走 staging/原型通道——改政策是治理行为，只能人类执行。

## 后果

- fitness.py / trust.py 的公式函数删除，模块随迁移收缩；社会演化政策的调整不再需要发版（ADR 0014 的运维面补完）。
- 政策语境注册表是成员注册表的纯子集（无动作词、无 host 回调）；求均值所需 `count` 补入 tlosure 通用词汇（对齐 SCI core）。
- hook 名集合只增；词汇演进走 ADR 0020 的 deprecation 生命周期。
- 被明确排除：cron 条件化（需独立 tick 语境）、act 拦截（reject-with）、retirement 政策化——各自留待单独决策。
