# M18：Agent Harness 骨架（规划 + 虚拟文件系统 + 链路追踪）

## 为什么加这一层

之前的 Agent 是"**单轮规则规划器 + 5 个工具**"：一句问题调一个工具，答完就结束。
这解释不了下面这类问题——它们必须多步：

```
比较钟南山和李兰娟近五年的研究方向差异
谁更适合审这篇肺癌免疫治疗稿
这位专家 2010 年后产出为什么下降
```

M18 把"多步"显式建成状态机，并把大结果从上下文挪到文件系统。

## 三个组件

### 1. 任务规划器（确定性规则，不用 LLM）

意图可枚举，所以用规则而不是模型：每一步可单测、可解释、零延迟。

| 意图 | 触发 | 步骤数 |
|---|---|---|
| `compare_experts` | 有"对比/比较"且抽出 ≥2 个人名 | 7 |
| `reviewer_match` | 出现"审稿/评审" | 5 |
| `expert_dossier` | 只有一个人名 | 4 |
| `search` | 兜底 | 2 |

`compare_experts` 的 7 步：

```
解析专家 A → 解析专家 B → 载入档案 A → 载入档案 B
→ 近五年方向对比 → 证据校验 → 生成报告
```

### 2. 虚拟文件系统（VFS）

真实目录 `data/vfs/`，路径对外表现为 `/dossiers/{pid}/works.jsonl`、`/reports/{tid}.md`。

**目的不是"存文件"，是把大结果挪出上下文**：
钟南山 938 篇论文如果直接进 prompt 会爆；现在只把
"938 篇 → /dossiers/527/works.jsonl" 这句摘要留在上下文里，需要细节时按路径取。

读写都做了路径规范化与越界检查（`resolve()` 后必须在 VFS 根目录内）。

### 3. 链路追踪

```sql
tasks(tid, query, intent, plan_json, status, report_path, ...)
steps(tid, idx, name, tool, status, summary, artifact, ms, error)
spans(tid, sid, kind, name, started_at, ms, meta_json)
```

`GET /trace/{tid}` 返回每一步的工具、耗时、产物。实测一次任务：

```
resolve 24ms → resolve 25ms → dossier 33ms → dossier 41ms
→ contrast 66ms → verify 33ms → report 33ms
```

## 实际效果（真实输出）

```
Q: 比较钟南山和李兰娟近五年的研究方向差异

[1/7] 解析专家 A: 钟南山（pid=527）
[2/7] 解析专家 B: 李兰娟（pid=503）
[3/7] 载入档案 A: 938 篇 → /dossiers/527/works.jsonl
[4/7] 载入档案 B: 1174 篇 → /dossiers/503/works.jsonl
[5/7] 近五年方向对比: A [asthma 33, lung 27, pulmonary 25] / B [liver 65, microbiota 44, mice 31]
[6/7] 证据校验: 全部事实可回溯到 pid
[7/7] 生成报告: 742 字符 → /reports/2.md
```

报告节选：

| 指标 | 钟南山 | 李兰娟 |
|---|---|---|
| 全量论文 | 938 | 1,174 |
| h-index | 83 | 117 |
| 被引合计 | 68,984 | 101,527 |
| 年份跨度 | 1981–2026 | 1998–2026 |
| 近五年产出占比 | 21% | 22% |

**近五年高频词**：钟南山 `asthma / lung / pulmonary / copd / airway`；
李兰娟 `liver / microbiota / mice / injury / human`。
**早期高频词**：钟南山 `pulmonary / chronic / obstructive`；
李兰娟 `liver / hepatitis / failure / virus`。

这个差异是真的（呼吸 vs 肝病/肠道菌群），不是模板拼出来的。

## 接口

```
POST /task?q=...      执行多步任务，返回 plan/steps/report/artifacts
GET  /tasks           最近任务列表
GET  /task/{tid}      任务详情（含报告全文）
GET  /trace/{tid}     链路追踪（每步耗时）
GET  /vfs?path=...    读虚拟文件系统里的产物
```

## 两个诚实的说明

1. **"研究方向"用标题词频近似**：我们的 `dossier_work` 没存逐篇概念标签，
   所以用标题词频做替代。报告里明确写了"只用于描述性对比，不用于判定水平高低"。
   更好的做法是在建档案时保存 `concepts`，成本是每条记录多几十字节。
2. **规划器是规则，不是 LLM**：好处是可解释、可单测、零延迟；坏处是只能覆盖
   枚举过的意图。真正的开放问题（"帮我找个适合做院士提名的推荐人"）还答不了。

## 下一步（M19）

按规划补齐三个子 Agent：

- **researcher**：多轮检索 + 去重，产出候选证据集（写 VFS）
- **analyst**：读档案算指标（h-index、主题迁移、异常年份），不碰检索
- **verifier**：抽查每条结论能否回溯到 pid/wid，不能就驳回

再往后是中间件（审计/限流/成本）与 Skills（检索策略、审稿口径以 Markdown 加载）。
