# W2 评测用例设计笔记（2026-11-16）

## 做了什么

在原有 14 条 Agent 评测之外，新增两套用例，单独跑、不污染基线：

| 文件 | 用途 | 条数 |
|---|---|---|
| `eval/agent_cases_w2.jsonl` | 稳定套件：同义改写、工具选择、多轮指代、记忆更新、结构化条件、合作者 | 7 |
| `eval/agent_cases_hard.jsonl` | 已知难题：库外主题拒答（中/英） | 2 |

运行方式：

```bash
python -m app.eval_agent --file eval/agent_cases_w2.jsonl --repeat 2
python -m app.eval_agent --file eval/agent_cases_hard.jsonl --repeat 3
```

`--repeat` 是多数投票，用来对冲模型抖动。

## 首轮结果

| 套件 | 结果 | 说明 |
|---|---|---|
| 稳定套件 | 5/7（86%） | `w2-para-asthma` 抖动：单跑能过，重复跑有一次失败 |
| 难题集 | 1/2（50%） | 中文拒答过了，英文 `medieval icelandic poetry` 没拒答 |

## 三类典型失败（这是本课重点）

### 1. 断言太窄（case 写错）

`w2-para-asthma` 原来要求答案出现“哮喘/Asthma”，但 Agent 的列表型答案只列人名和单位，不复述主题。

**修法**：断言具体实体（钟南山 / Nanshan Zhong）+ `tools_any`，不要要求复述问题。

### 2. 断言了产品没有的能力（case 写错）

`w2-collab-sort` 要求“合作者里谁被引最高”，但 `collaborators` 工具按**共同论文数**排序，根本不按被引。

**修法**：改成“钟南山的合作者有哪些”，断言“合作” + `tools_any: collaborators`。**case 要贴着产品契约写，不能按想象写。**

### 3. 两条路径行为不一致（产品问题）

`w2-refuse-zh` 在 `supervisor`（规则路径）稳定拒答，但评测走的是 `agent_llm.chat`（LLM 路径），模型用常识答了。

**这不是 case 错，是产品缺口**：LLM 路径的拒答比规则路径弱。英文版 `medieval icelandic poetry` 同样失败，和原有 `a-refuse-2` 是同一类问题。

## 抖动怎么处理

- 单条 case 跑 3 次，多数通过才算通过（`--repeat 3`）；
- 仍然抖的，单独放 `hard` 或 quarantine，**不要混进稳定套件**；
- 每条 case 记录“为什么可能失败”，否则下次看到红叉不知道是回归还是噪声。

## 下一步

1. `w2-para-asthma` 用 `--repeat 3` 再跑，确认是抖动还是真回归；
2. 修 LLM 路径的拒答：库外主题应该先由 `has_items` 守卫兜住，查清为什么这次没兜住；
3. 稳定套件扩到 12–15 条，再并入主评测。
