# M53：受控自我改进（RSI）

## 它是什么（先说清边界）

**不是**"AI 改自己的代码"，而是：

> 在一个**白名单策略空间**里做搜索，用**外部评测**当闸门，改进才被采纳。

| 可改（白名单） | 是否接通检索 |
|---|---|
| `synonyms`：中文主题 → 英文检索词 | ✅ 唯一接通的 |
| `rerank_weights` / `plan_prompt` / `tool_order` | ⬜ 预留（校验通过但不生效） |

只接通一个是有意的：**先把"变异→评测→晋级"的闭环跑通**，再扩动作空间。

## 闸门（四条，全部可单测）

```
1. dev 不退化：        dev_pass >= parent_dev
2. held-out 有提升：   heldout_pass > parent_held + 0.01
3. 成本不失控：        cost_per_case <= parent_cost * 1.2
4. 预算与代数：        spent <= budget_usd，gen <= 10
```

另加两条工程规则：

- **每代只晋级一个**变异（避免同代多次改动叠加，无法归因）；
- **任何一条不过就记录原因**（`rsi_log.reason`），不静默丢弃。

## 防作弊

| 手段 | 做法 |
|---|---|
| 变异提议看不到 held-out | `propose()` 只接收 dev 用例 |
| held-out 只用于闸门 | `gate()` 内部读取，调用方拿不到分数 |
| 评测集被改就拒跑 | 预留 `file_sha()`；目前由 git 管理，后续接 sha 校验 |
| 可回滚 | 每次晋级写 `mem_policy` 快照，`rollback(gen)` 一条命令 |
| 可审计 | `rsi_log` 记录每代变异、结果、原因、dev/held、成本 |

## 实测（5 代，DeepSeek 提议 + 规则兜底）

```
基线（gen=0）：dev 8% / held-out 12%
gen 1  ✅ 晋级   dev 25% / held 38%      （+17pt / +26pt）
gen 2  ❌ 拒绝   held-out 没有提升 38%→38%
gen 3  ❌ 拒绝   同上
gen 4  ❌ 拒绝   同上
gen 5  ❌ 拒绝   同上
rsi_log 5 条（1 promoted / 4 rejected）
```

晋级的那一代给 12 个中文主题补上了英文扩展词（帕金森→parkinson、单细胞测序→single-cell sequencing…）。

后四代被拒的原因很直接：**剩下的失败用例不是"缺同义词"能修的**（语料里确实没有对应专家），
继续加词只会得到一份**等价策略**——闸门正确地拒绝了它。

## 两个 bug（都是评测跑出来的）

1. **`run(log=print)` 的参数把模块级 `log()` 遮蔽了**：日志打印出来了，但 `rsi_log` 一条没写。
   改名 `record()`。
2. **同义词没生效**：纯中文主题走的是 CE 路径，根本没到 `facets.search`。
   在 `tool_find_expert` 里加"策略词存在 → 直接走 facet"才接通。

## 复现

```bash
python -m app.rsi --generations 3 --budget-usd 2      # 真实晋级
python -m app.rsi --generations 3 --dry-run           # 只评估不落库
```

API：`GET /rsi/status`、`POST /rsi/run`、`POST /rsi/rollback`（写接口需 `X-API-Key`）。

## 局限（重要）

1. **held-out 是"问法留出"，不是"主题留出"**：同义词只能迁移到已见过的主题；
   真正没见过的主题，这套机制无解（要用别的手段，比如 query 改写模型）；
2. 只接通了 `synonyms` 一种动作，`plan_prompt` / `rerank_weights` 尚未生效；
3. 没有做"多代连续改进"的预算曲线（受限于 dev 只有 12 条）；
4. LLM 提议偶尔给出等价改写（`single-cell sequencing` vs `scRNA-seq`），
   闸门会拒，但会浪费一轮 eval；
5. 扰动用例（`eval/rsi_perturbed.jsonl`）已生成，尚未接入最终验收报表。
