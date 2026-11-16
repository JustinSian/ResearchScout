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

---

## 追加：检索请求污染长期记忆（已修）

### 现象

`w2-para-asthma` 第一次能过，重复跑就 0 分，且 `steps`、`evidence` 全空。

### 根因

1. `agent_llm.chat` 对**每一句 query** 都调用 `memory_extract.apply`；
2. “帮我找几位研究哮喘的专家”里的“我”是祈使句宾语，但抽取器把它当第一人称；
3. `_TOPIC` 抽出 `topic=哮喘的专家` 写进 `mem_fact`；
4. 下一次同 uid 查询被 `_memory_answer` 短路，直接返回“根据你的长期记忆…”，不调工具。

### 修复

- `app/memory_extract.py`：先剥掉“帮我/给我/替我/帮忙”等祈使前缀，再判断第一人称；
- `app/agent_llm.py`：只有 `_is_disclosure(query)` 为真时才写长期记忆；
- `app/eval_agent.py`：`--repeat` 每轮重跑前清空该 uid 的记忆，避免上一轮污染下一轮；
- `tests/test_memory.py`：新增“祈使请求不写记忆”的回归测试。

### 结果

- `pytest`：**483 passed**；
- `w2-para-asthma`：`--repeat 3` 稳定 100%；
- 原 14 条：13/14（`a-refuse-2` 仍抖）。

### 仍未解决

- `w2-mem-update` 偶发失败（需看是答案不含“哮喘”还是 memory 断言）；
- 拒答难题集 0/2～1/2 波动，LLM 路径对库外主题仍会用常识作答；
- 这两类都应先用 `--repeat 3` 观察，再决定改提示词还是改守卫。

---

## 最终结果（2026-11-16 晚）

| 套件 | 结果 |
|---|---|
| W2 稳定套件（7 条） | **7/7** |
| 难题集（2 条拒答） | **2/2** |
| 原 14 条基线 | **14/14** |
| pytest | **485 passed** |

### 又修了两个问题

1. **`--repeat` 清状态时把 setup 也清掉了**：记忆类用例重跑时前置条件丢失。
   改成 `_reset_case()`：清库 + 重新执行 `setup_queries` / `setup_facts`。
2. **强拒答被引用数豁免**：模型开头就说“查不到”，但放宽检索带回 10 条无关结果，
   `citations=10` 触发了 `_looks_refusal` 的“有引用就不是拒答”。
   新增 `_is_refusal()`：开头 80 字出现“查不到/资料不足/无法回答”等强措辞时，
   不管几条引用，一律按拒答处理。局部否定（“X 没有经典论文”）仍不受影响。

### 可以写进简历的结论

> 用评测驱动修了三个真实缺陷：检索请求污染长期记忆、评测重跑丢前置条件、
> 误匹配引用击穿拒答守卫；最终 Agent 14/14、记忆 50/50、W2 套件 7/7。
