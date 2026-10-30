# M20–M22：中间件 / Skills / 尽调前端

## M20 中间件：限流 + 脱敏 + 审计 + 成本

### 为什么要

Agent 自己会调工具之后，必须能回答四个问题：

1. 谁在什么时候调了什么？ → **审计**
2. 会不会被一次请求打爆？ → **限流**
3. 日志里会不会写进敏感信息？ → **脱敏**
4. 一次任务花了多少？ → **成本核算**

### 实现（不依赖 Redis / Kafka）

| Hook | 时机 | 作用 |
|---|---|---|
| `before_tool` | 每次工具调用前 | 令牌桶限流（容量 60，每分钟补 60；高成本工具权重 2） |
| `after_tool` | 调用结束后 | 写审计行：脱敏参数、状态、耗时、产物、成本单位 |
| `on_error` | 异常时 | 记录原因，不吞异常 |

审计表 `audit(aid, ts, tid, sid, tool, args_hash, args_redacted, status, ms, artifact, cost_units, error)`。

### 实测

```
任务 #19：6 步，审计 6 条，成本 10.3 单位，失败 0
  extract_topic done  25ms  {"query": "谁更适合审一篇关于肺癌免疫治疗的稿件"}
  researcher    done 144ms  {}
  analyst       done 208ms  {"top": 5}
```

限流实测：连续调用 `researcher`（权重 2）第 31 次被拦截，
提示"令牌不足，每分钟 60 次"——符合容量 60 / 权重 2 的预期。

### 一个坑

脱敏顺序写错：`A5101475024` 先被手机号正则匹配成 `A[phone]`。
必须先匹配长 ID（`A\d{9,}`）再匹配手机号。

## M21 Skills：口径写进 Markdown

### 渐进式披露

| 层 | 内容 | 何时加载 |
|---|---|---|
| 1 | front-matter：name / version / when / 参数键 | 列表页、路由判断 |
| 2 | 正文 SOP | 命中后才读 |

`GET /skills` 只返回第一层：

```json
{"name":"reviewer-matching","version":2,"keys":["weights","require_domain_hits"]}
```

### 热改口径的实测

`skills/reviewer-matching.md` 里：

```yaml
weights: {"topic": 0.5, "h": 0.3, "recent": 0.2}
require_domain_hits: 2
```

把权重改成 `{"topic": 0.7, "h": 0.1, "recent": 0.2}`，**不改一行代码**，再跑同一问题：

| 口径 | 第 1 名 | 第 2 名 | 第 3 名 |
|---|---|---|---|
| 主题 0.5 / h 0.3 | Ying Cheng **0.656** | Hanzhang Chen 0.445 | Guilin Peng 0.439 |
| 主题 0.7 / h 0.1 | Ying Cheng **0.563** | Guilin Peng 0.375 | Xin Xu 0.372 |

排序变了——说明口径确实来自文件，不是硬编码。

## M22 尽调前端

新增导航页「尽调 Agent」：

- 输入一句自然语言 → 点运行 → 显示**步骤流**（每步名称、摘要、耗时、产物路径）
- 报告用极简 Markdown 渲染（标题/粗体/表格/代码），表格是真 HTML 表格
- 点步骤上的产物徽章 → 直接读 `/vfs` 展示原文（候选人/指标/校验 JSON 都能看）
- 顶部显示任务号、意图、总耗时、**审计成本**

## 端到端现状

```
POST /task?q=...    规划 6 步 → researcher/analyst/verifier → 报告
GET  /tasks         任务列表
GET  /task/{tid}    步骤 + 报告全文
GET  /trace/{tid}   每步耗时
GET  /audit?tid=    审计明细 + 成本汇总
GET  /skills        Skill 索引（第一层）
GET  /skills/{name} Skill 全文（第二层）
GET  /vfs?path=     读任意产物
```

六个步骤的 time：25 / 144 / 208 / 46 / 50 / 33 ms（合计约 0.5 秒）。

## 仍然缺的三块（对照简历）

| 简历有 | 我们 | 说明 |
|---|---|---|
| 沙箱执行代码 | 无 | 没有 docker，只能用 subprocess+白名单，风险高，暂不做 |
| Redis/Milvus 记忆 | SQLite 表 | 跨会话记忆还没接入 Agent |
| Kafka 链路追踪 | SQLite spans | 单机场景够用，多实例才需要消息队列 |
