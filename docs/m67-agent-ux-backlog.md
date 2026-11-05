# M67：Agent 体验改进清单（Backlog）

> 基于当前代码（HEAD 8fffa09）逐条核实后的改进项，按「影响 × 工作量」排序。
> 已核实的现状写在前，避免空谈。

## 现状（实测/读码确认）

| 事实 | 证据 |
|---|---|
| 工具**串行**执行 | `app/agent_llm.py:246 for call in tool_calls`（一轮最多 4 个工具 = 最多 4 次 LLM 调用） |
| 最终答案**非真实流式** | `app/llm.py` 只有 `chat_text`；前端 `app/api.py:1012` 是 6 字/15ms **模拟**逐字 |
| 延迟 p50 7–8s，补库最坏 150s | 实测 |
| 成本算出来但**不展示** | `app/agent_llm.py` 有 `cost_usd`，前端没渲染 |
| 无取消/超时 | 前端无中断按钮，后端无取消线程 |
| 无查询缓存 | `grep cache` 仅命中 HTTP 头 |
| 无反馈闭环 | RSI 有评测闸门，但前端没有 👍/👎 入口 |

---

## P0 — 高影响、低中工作量（✅ 1、2 已完成，见文末实测）

### 1. 真实 LLM 流式输出
- **现状**：答案整段算完后再 6 字/15ms 模拟吐字，首 token 前用户干等 7–8s。
- **改法**：`llm.chat_text` 加 `stream=True`，SSE 逐 token；工具步骤仍用 `on_step` 事件。
- **收益**：首 token ~1s，感知延迟大幅下降；不改 Agent 逻辑。

### 2. 并行工具调用
- **现状**：`profile` + `research_area` + `works` 串行 = 3 次 LLM 往返。
- **改法**：OpenAI 兼容 `parallel_tool_calls=true`（DeepSeek 支持），一轮发多个工具，结果合并后再进下一轮。
- **收益**：典型查询 7–8s → 4–5s；`MAX_CALLS` 语义需改成"按工具数计"。

### 3. 取消 + 超时
- **现状**：补库最坏 150s，用户只能等；无超时。
- **改法**：前端 `AbortController` 断开 EventSource；后端 `run()` 加 `deadline` 检查 + 可中断标志（`threading.Event`）。
- **收益**：用户能随时停，不再"卡死感"。

### 4. 成本/耗时/引擎透出
- **现状**：`cost_usd`、`elapsed_ms`、`engine` 都在返回里，前端不显示。
- **改法**：每条回答底部一行小字：`$0.003 · 8.2s · LLM Agent · 用了记忆/补库`。
- **收益**：信任感 + 成本可感。

---

## P1 — 高影响、中工作量

### 5. 查询缓存
- 精确匹配 + 语义近似（向量），TTL 24h；命中直接返回，跳过 LLM。
- 收益：重复问题省钱省时；对演示站尤其重要。

### 6. 反馈闭环驱动 RSI
- 前端加 👍/👎（+ 原因），写回 `search_episode` / 新 `feedback` 表；RSI 用真实用户反馈做 gate。
- 现状 RSI 只有离线评测闸门，缺"用户反馈→自进化"的最后一公里。

### 7. 引用锚定到句子
- 现在只有"引用 3 条"，点不到具体句。改成答案里 `[1][2]` 上标，悬停显示来源。
- 收益：可信度、可核查性（学术产品的核心体验）。

---

## P2 — 中等影响

### 8. 不确定性分级
- 让模型输出 `confidence`（确定/推断/不确定），前端用颜色/标签区分。
- 避免"看着很确定其实没查到"的误导。

### 9. 澄清对话增强
- `_clarify` 现在只处理"推荐几个专家"这类空问；扩展到"给出子方向选择按钮"（复用现有 refinement 机制）。

### 10. 长期记忆可视化
- 回答里标注"这次用到了你之前说的：帕金森"，让记忆**可见**、可纠正。

### 11. 引擎降级明示
- LLM 失败退回 classic 时，前端提示"模型调用失败，已退回规则链路"，而不是静默切换。

---

## P3 — 打磨
- 会话历史管理（清空/导出）；
- 移动端适配；
- 语音输入（可选）。

---

## 已知正确性问题（非纯 UX，建议一并排期）
1. **主题改写回退**（m66 已记录）：模型把主题改写成长句导致影子索引不命中 → 下一问才答出。需"改写后回退原词"或主题向量化。
2. **profile 同名异人**：已加精确匹配，但模型仍可能传 `name` 而非 `pid`；理想是 `find_expert` 结果里带 `pid`，模型直接引用。


---

## 完成记录：P0-1 真实流式 + P0-2 多工具同轮

### 实现

| 改动 | 文件 |
|---|---|
| `llm.chat_stream()`：SSE 逐 token，失败自动退回 `chat_text` | `app/llm.py` |
| `run/chat` 新增 `on_delta` / `on_reset`；强制收尾走流式 | `app/agent_llm.py` |
| 一轮多个 tool_calls 全部执行、顺序对齐；`parallel_tool_calls=true`（400 自动回退） | `app/llm.py` + `app/agent_llm.py` |
| SSE 转发真实 token；补库/补人重试时发 `reset` 清屏 | `app/api.py` |
| 前端处理 `reset` 事件 | `web/index.html` |
| 系统提示加"profile/research_area/works 同轮并行调用" | `app/agent_llm.py` |

### 实测（服务器，同一查询「介绍一下钟南山」）

| 指标 | 改造前 | 改造后 |
|---|---|---|
| 首个可见答案文字 | ~7.8s（整段算完才开始"打字"） | **4.6s**（真实 token 边生成边到） |
| 工具调用轮次 | 2 个工具可能分 2 轮 | 一轮返回 2 个工具（间隔 0.0s） |
| `chat_stream` 单独耗时 | — | 1.0–1.4s，20 个 delta |

### 一个反直觉的发现

**本地工具并发执行反而更慢**：并发时三个工具实测 ~670ms/个（SQLite 抢锁），
串行只要 ~23ms。所以默认串行执行，并发放在 `ST_PARALLEL_EXEC=1` 后面。
真正的收益来自**减少 LLM 往返轮次**，不是并发跑工具。


---

## 完成记录：P0-3 取消/超时 + P0-4 成本透出

### 实现

| 改动 | 文件 |
|---|---|
| `run/chat` 增加 `deadline_s` 与 `on_should_stop`；每轮开始和收尾前检查 | `app/agent_llm.py` |
| 中止时返回 `finish_reason=cancelled/timeout`，并**跳过补库/补人/公示** | 同上 |
| SSE 客户端断开 → `stop.set()` → worker 在下一个检查点收手 | `app/api.py` |
| `done` 事件带 `cost_usd / ms / tools / memory / finish_reason` | 同上 |
| 前端「停止」按钮 + 每条回答底部 `$0.0017 · 5.0s · LLM Agent · 3 次工具` | `web/index.html` |

### 顺带修掉一个真 bug

`_match_topic` 的"主题一致性"判断会**无条件覆盖**前面设的 `_need_expand=False`，
导致人名（`Édouard Bard`）和介绍类问题仍被当成主题去补库。
现在用独立的 `_skip_expand` 标志，人名/关系/介绍类问题**任何情况下都不补库**。

### 实测（真实 LLM）

```
timeout：deadline_s=0.8 → 4.0s 返回 finish=timeout「查询超过 1 秒已中止」
cancel：0.8s 后置位 → 3.6s 返回 finish=cancelled「已停止本次查询。」
done 事件：{"cost_usd":0.0017,"ms":5025.6,"tools":3,"memory":false,"streamed":false}
```

中止的实际粒度是"当前这一轮 LLM 请求"——已发出的 HTTP 请求不会被打断，
所以 0.8 秒的 deadline 实际 3.6–4.0 秒返回（等当前轮结束）。这是诚实的上限。
