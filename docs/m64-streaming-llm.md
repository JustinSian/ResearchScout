# M64：/chat/stream 接入 LLM Agent（步骤级流式）

## 问题

`/chat/stream` 之前只走经典（无 LLM）路径，用 `narrate` 模板"模拟"流式；
真正烧钱的 LLM Agent 没有任何流式，前端干等 7–8 秒。

## 改动

1. `agent_llm.run/chat` 增加 `on_step` 回调：每完成一个工具调用就回调一次
   （tool / args / count / ms / error），默认 None，向后兼容；
2. `chat_stream` 的 `gen()`：LLM 启用时，在后台线程跑 `agent_llm.chat(on_step=…)`，
   主协程边等边把 step 转发成 SSE `event: step`；最终答案按 6 字/批逐字吐；
3. 前端 `v-chat` 增加 `step` 事件处理：把工具调用名映射成中文
   （检索专家/查画像/分析方向/查合作者/查论文/对比/查引用/记记忆），
   实时显示"→ 查画像（N 条）"，不再干等。

## SSE 事件（LLM 路径）

```
think   "正在分析你的问题，准备调用工具…"
step    {tool, args, count, ms, error}   ← 每步工具调用
token   逐字回答片段
citations / done
```

经典路径（未配 key 或 classic 模式）行为不变。

## 实测

```
curl /chat/stream?q=钟南山的研究方向
→ think  "正在分析…"
→ step   research_area（name=钟南山，count=1）
→ step   profile（name=钟南山，count=1）
→ token  "**结论：钟" …（逐字）
```

## 收益

- Agent 对话从"空白 7–8s 后一次性出答案"变成"立刻看到在查什么 + 边查边出"；
- 复用 `on_step` 回调，没有复制 Agent 主循环，避免两套逻辑漂移。
