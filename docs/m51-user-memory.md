# M51：用户长期记忆（三层 + 时间有效区间）

## 做了什么

在 ScholarTrust 上新增**用户**记忆层。注意它和已有的 `app/memory.py` 不是一回事：

| | `app/memory.py`（已有） | `app/user_memory.py`（新增） |
|---|---|---|
| 内容 | 任务缓存 + 档案 diff | 用户的语义/情景记忆 |
| 作用域 | 系统内部 | 跨会话、按 `uid` |
| 时间 | 无 | `valid_from` / `valid_to` |

三层：

| 层 | 表 | 存什么 |
|---|---|---|
| 语义 | `mem_fact` | 方向、领域、限制、偏好、单位（带时间区间） |
| 情景 | `mem_event` | 每次问答的路由、结果、反馈 |
| 程序 | `mem_policy` | RSI 产出的策略（M53） |

## 为什么另开一个库

`data/user_mem.db`（WAL + `busy_timeout=8000`），和主库、`chat_ctx.db` 分开。
主库有 FTS/向量的大写入，用户记忆是高频小写入，混在一起会互相等锁——`chat_ctx` 已经因此迁出去过一次。

## 时间语义

- 一律存 **UTC ISO8601**；
- 写入同一 `(uid,key)` 时，旧值 `valid_to=now` 而不是删除；
- `facts(uid, as_of=...)` 支持"我 2024 年在做什么"；
- 边界：`valid_to == as_of` 视为**已过期**（开区间），有单测覆盖。

## 抽取（默认规则，零成本）

`app/memory_extract.py` 只认**第一人称自述**（没有"我/I/my"就不抽，避免把"找研究 dementia 的专家"抽成用户偏好）。

| 句式 | 产出 |
|---|---|
| 我研究/做/专注 X | `set topic=X` |
| 我的研究方向是 X | `set field=X` |
| 我现在只做 X | `set topic=X` |
| 我不做 X 了，转做 Y | `expire topic=X` + `set topic=Y` |
| 我不要 X / 我更喜欢 X | `set constraint` / `set preference` |
| 我在 X 工作 | `set org=X` |
| I work on X | `set field=X` |

`ST_MEM_LLM=1` 时才走模型抽取（JSON 白名单校验），失败退回规则——抽取永远不阻塞对话。

## 召回与注入

`app/memory_recall.py`：

- 打分 = 词重叠 ×2 + key 权重 + 时间衰减（半衰期 180 天）；
- `apply_to_parsed()` **只填空，不覆盖**：用户这句话里显式给的条件优先，覆盖情况记 `explicit_keeps`；
- 空 `uid` / 空记忆 → 不查库、不改行为（零回归）；
- 答案前加一行「（已按你的长期记忆：方向=帕金森，2026-11-02 更新）」。

## 踩过的四个坑（都在评测里暴露）

1. **表没建但异常被吞**：`apply()` 为了"记忆故障不影响主流程"捕获异常，
   结果表不存在时静默丢记忆，评测表现为"全都没记住"。改为 `connect()` 首次自动建表。
2. **LLM 分支提前 return**：`plan_node` 的 LLM 分支绕过了规则分支里的 hint 注入。
3. **LLM 计划缓存没带 hint**：`decide()` 先规划一次（无 hint），`answer()` 再规划时命中缓存，
   记忆永远进不了检索。把 hint 计入缓存键。
4. **LangGraph 状态白名单**：`AgentState` 是 `TypedDict`，没声明的 `hint` 键被丢掉。
   加一行声明才通。

（另外两个产品级问题：`parse()` 把"推荐几个"当成主题，加"短且无英文映射 = 噪声"判断；
`facets.topic_en` 对帕金森/阿尔茨海默返回空，加小词表兜底。）

## 实测（50 条，五能力各 10 条）

```
通过率 100%（50/50）
过期事实误用 0%
记忆进检索 76%（= 40 条有记忆用例中的 95%；10 条 abstain 本就不该有记忆）
p95 4073ms（含 web 兜底与两次规划）
```

| 能力 | 通过 |
|---|---|
| extraction | 100% |
| multi_session | 100% |
| temporal | 100% |
| update | 100% |
| abstain | 100% |

## 测试

新增 36 项：`test_user_memory.py`（11）、`test_memory.py`（18）、`test_eval_memory.py`（7）。
全量 **199 项**通过。

## 遗留

1. 抽取只覆盖中文/英文少数句式，否定句（"我不喜欢综述"）会抽成正向偏好；
2. `topic_en` 词表覆盖有限，靠兜底表补，长期应扩到别名表；
3. 记忆没有冲突检测（"研究 A" 与 "研究 B" 可以同时有效，除非用转向句式）；
4. 情景记忆还没被用于个性化排序（只记录，未消费）；
5. 前端记忆视图、反馈按钮尚未实现（W3）。
