# M49：可选的 LLM 规划路径（把最大的弱点变成对照实验）

## 背景

m45 的 A/B 证明 supervisor 比单 agent 好，但有个更尖锐的问题一直没解决：

> **这条链路里没有 LLM。** `plan_node` 是正则匹配，联网兜底调的是搜索 API。
> 所以"多智能体"严格说是"确定性工具编排"。

这一轮加一条**可选**的 LLM 规划路径，目的不是"换成 LLM"，而是**把两者的取舍测出来**。

## 设计（三条硬约束）

1. **失败必须可退回**：没 key、超时、JSON 坏、工具名不在白名单 → 一律返回 `None`，
   规则规划器接管。LLM 是增强，不是单点故障。
2. **输出必须可校验**：模型只能输出 `{"tool": ..., "args": {...}}`；
   工具名与参数名都在白名单里，多余字段丢弃，类型不合法丢弃（`cites_min`/`limit`/`hops`）。
3. **成本必须可计量**：每次调用返回 token 数与美元成本，供 A/B 汇总。

## 实现

| 文件 | 作用 |
|---|---|
| `app/llm.py` | 极简 DeepSeek 客户端（httpx，不引 SDK）；`.env` 加载；token/成本/耗时 |
| `app/plan_llm.py` | 提示词、输出校验、`to_call()`（映射到真实工具签名） |
| `app/ab_planner.py` | 规则 vs LLM 的 A/B |
| `.env.example` | 配置模板；`.env` 已在 `.gitignore`（并放行 `.env.example`） |

### 一个容易踩的坑：工具签名

`_run_tool` 是 `**args` 直接展开调用的，而 `tool_find_expert(query, limit, sid)`
**没有** `country`/`topic` 形参——LLM 多给一个键就 `TypeError`。

所以 `to_call()` 不直接透传，而是把结构化条件**拼回一句人话**：

```
{"country":"CN","inst_kind":"hospital","topic":"respiratory","sort":"cites"}
        ↓
"中国 医院 被引最多 respiratory 专家"
        ↓
tool_find_expert(query=...)   # 复用已有的 facet 解析器
```

这样既不改进工具签名，也不丢条件。

### 缓存

`plan_node` 在一次请求里会被调用两次（supervisor 决策 + agent 规划），
所以按 `(query, sid)` 做进程内缓存，避免同一句话付两次 token。

## 怎么跑

```bash
# .env 里填 DEEPSEEK_API_KEY 后
ST_PLANNER=llm python -m app.ab_planner

# 或者只比规则（默认）
python -m app.ab_planner --modes rules
```

输出：

```
  rules  通过 100% · 工具匹配 100% · p50 183.5ms · tokens 0+0
  llm    通过  ?%  · 工具匹配  ?% · p50   ?ms  · tokens ?+?
```

## 当前状态（未配置 key 时）

- `configured()` 为 False → LLM 组自动跳过，A/B 打印"跳过"而不是报错；
- 164 项测试通过（新增 11 项：.env 解析、输出校验、`to_call` 映射、成本公式、开关逻辑）。

## 预期与风险（先写下来，跑完再对照）

| 猜测 | 依据 |
|---|---|
| LLM 的 `tool_match` 可能**低于**规则 | 规则是围绕这套数据硬编码的，LLM 没有这些先验 |
| LLM 在"同义问法"上更好 | 规则靠关键词表，覆盖不到的说法会漏 |
| LLM 延迟高一个量级 | 单次 1–3s vs 规则 <10ms |
| LLM 有真实成本 | 20 条约几十美分以下，但每次请求都在付 |

**如果 LLM 没赢，这个结果本身就是价值**：说明"确定性编排 + 规则"在这个闭域问题上更划算，
而这正是这个项目一直主张的——**能算 ≠ 该用，能上模型 ≠ 该上模型**。

## 遗留

1. 规则组没有 token 成本，A/B 的 `cost_usd` 对规则组恒为 0，比较时要注意口径；
2. 上下文提示只传了 `str(ctx)[:300]`，多轮指代交给 LLM 的效果未验证；
3. 没有做 LLM 的重试降级策略（当前失败一次就退回规则）；
4. 20 条 gold set 对规划器评测偏少，扩到 60 条后再下结论更稳。
