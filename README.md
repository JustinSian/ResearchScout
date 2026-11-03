<div align="center">

# ResearchScout

**会记住你的科研 Agent —— 长期记忆 · 多轮对话 · 受控自我改进**

用自然语言找专家、查论文、追问合作关系；它会记住你的研究方向，并用自己的评测集持续改进自己。

[![CI](https://github.com/JustinSian/ResearchScout/actions/workflows/ci.yml/badge.svg)](https://github.com/JustinSian/ResearchScout/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-236%20passed-brightgreen.svg)](#测试与评测)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[快速开始](#快速开始) · [核心能力](#核心能力) · [架构](#架构) · [评测](#测试与评测) · [API](#api)

</div>

---

## 产品是什么

科研人员找一个领域的专家，通常要跨三个工具：学术搜索引擎、机构主页、合作关系网。而且每次都要重新描述一遍"我是做什么的、我要什么条件"。

**ResearchScout 把这件事收进一个 Agent**：

- 你用一句话描述需求（"找做帕金森早期诊断、近三年活跃的专家"），它自己决定查什么、查几次；
- 它会记住你是做什么的（跨会话），下次只说"推荐几个"就够了；
- 它的每一条结论都能回溯到库里的专家/论文，查不到时明确说不知道；
- 它用一套评测集衡量自己，并在受控范围内改进自己的检索策略。

底库是 **47,569 位学者 / 11,104 篇论文 / 30,101 条库内引用边 / 101,414 条合作边**，国家覆盖 97%（带来源标记）。

## 核心能力

| 能力 | 实现 | 用户看到什么 |
|---|---|---|
| **LLM Agent 循环** | 模型自主选择 8 个工具（找专家/画像/方向/合作者/论文/对比/引用/记忆），最多 3 轮 4 次调用，含预算与强制收尾 | "它自己查了 3 次，然后给出 5 位专家和依据" |
| **共享经验记忆** | 成功链路写入 `search_episode`（问题/工具序列/命中专家），同类问题把其他用户的路线注入 prompt | "别人查过的同类问题，它直接知道该查什么、找谁" |
| **长期记忆** | 三层结构（语义/情景/程序），事实带 `valid_from/valid_to`，转向后旧值失效但保留历史 | "我转做帕金森了" → 下次推荐自动切换，并能回答"我以前做什么" |
| **短期记忆** | 会话滚动摘要（超过 10 轮压缩）+ 最近 8 轮原文注入 | 第 10 轮之后它仍记得"你要的是医院的、被引高的" |
| **多轮对话** | 指代解析（"那这些人里谁被引最高"）+ 上一轮结果集继承 | 不用重复条件，追问能接上 |
| **检索与图分析** | FTS5 + 向量 + RRF 混合检索；引用链、PageRank、Louvain 聚类、割点/边介数桥接 | 专家、论文、合作网络、研究流派 |
| **库自动扩充** | 无相关专家 → OpenAlex 主题检索 → 标题验证（≥2 篇）→ 入库 + 补 FTS → 自动重试 | "这次没有 → 已为你扩充该领域专家 → 重试后给出结果" |
| **受控 RSI** | 白名单策略变异 → dev 评估 → held-out 闸门 → 晋级/回滚，全程预算与审计 | 检索策略变好，但可以被"拒绝"和"回滚" |
| **评测数据集** | Agent 14 条（多轮/记忆/拒答）+ 记忆 50 条（五能力）+ RSI dev/heldout/perturbed | 每个数字都能用仓库内命令复现 |

## 技术栈

| 层 | 选型 | 为什么 |
|---|---|---|
| API | **FastAPI** + uvicorn | 58 个端点、SSE 流式、写接口 `X-API-Key` |
| Agent | **DeepSeek V4.1 Flash**（function calling）+ 自研循环 | 工具调用、强制收尾、成本/步数预算 |
| 短期记忆 | SQLite `session_turn` / `session_summary` | 滚动窗口 + LLM 摘要，控制 token |
| 长期记忆 | SQLite `mem_fact`（时间区间）/ `mem_event` / `mem_policy` | 可回答"以前/现在"，可回滚策略 |
| 检索 | SQLite **FTS5** + 本地向量 + **RRF** | 单机可跑，无需外部向量库 |
| 重排 | ONNX bge-reranker + 交叉编码器拒答 | 判定"库里到底有没有" |
| 图分析 | 纯 Python PageRank / Louvain / Tarjan / Brandes | 无重型依赖，可单测 |
| 自我改进 | 自研 RSI（变异 + 双闸门 + 快照） | 可控、可审计、可回滚 |
| 前端 | 单文件 `web/index.html`（9 视图，零构建） | clone 即跑 |

## 架构

```
                        ┌─────────────────────────────────────────────┐
用户 ── query, uid, sid ▶│ Agent（LLM 在环）                            │
                        │  system = 人设 + 长期记忆 + 会话摘要 + 策略     │
                        └───────────────┬─────────────────────────────┘
                                        │  function calling（≤3 轮 / ≤4 次）
              ┌─────────────────────────┼──────────────────────────┐
              ▼                         ▼                          ▼
      知识库工具                  remember 工具              强制收尾（预算/轮数）
  find_expert / profile /        写入长期记忆               不带工具再问一次
  research_area / collaborators       │
  / works / compare / citations       │
              │                       │
              ▼                       ▼
   证据（pid/wid + 计数）       mem_fact（时间区间）
              │                       │
              ▼                       ▼
        答案 + 引用 + trace      情景记忆 mem_event
              │
              ▼
   会话记忆：追加本轮 → 超 10 轮压缩摘要

离线两条回路：
  评测  eval/agent_cases · eval/memory.jsonl · eval/rsi_dev+heldout+perturbed
  RSI   变异（synonyms / plan_prompt）→ dev → held-out 闸门 → 晋级 / 回滚
```

## 快速开始

```bash
git clone https://github.com/JustinSian/ResearchScout.git
cd ResearchScout

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 必需：Agent 走 LLM（DeepSeek）
echo "DEEPSEEK_API_KEY=sk-你的key" >> .env

# 用演示快照直接起（6.6 MB，无需采集）
ST_DB=data/snapshot/demo.db uvicorn app.api:app --host 0.0.0.0 --port 8020
```

打开 <http://127.0.0.1:8020>：专家墙 / 检索 / 画像 / 合作图谱 / **Agent 问答** / 尽调 / 复核 / 记忆。

### 一次带记忆的多轮对话

```bash
# 第一轮：告诉它你是谁
curl "http://127.0.0.1:8020/chat?q=我研究帕金森&uid=demo&sid=s1"

# 第二轮：不用再说方向
curl "http://127.0.0.1:8020/chat?q=推荐几个专家&uid=demo&sid=s1"

# 第三轮：指代追问
curl "http://127.0.0.1:8020/chat?q=那这些人里谁被引最高&uid=demo&sid=s1"
```

返回里除了 `answer` / `citations`，还有 `engine: "llm"`、`steps`（每次工具调用与耗时）、`cost_usd`、`memory`（命中的长期记忆）。

## 测试与评测

```bash
python -m pytest tests -q          # 236 项测试
python -m app.eval_agent           # Agent 级：多轮 + 记忆 + 拒答（14 条）
python -m app.eval_memory          # 记忆五能力（50 条）
python -m app.rsi --generations 3 --dry-run   # 自我改进（不落库）
```

当前实测（DeepSeek V4.1 Flash）：

| 评测 | 结果 |
|---|---|
| **Agent 级（14 条）** | 通过率 **100%**（14/14）· 工具命中 100% · 记忆使用 21% · 平均 **1.14 轮** · 单次 **$0.0020** · p50 **8.8 s** |
| 记忆五能力（50 条） | 通过率 **100%** · 过期事实误用 **0%** · 记忆进检索 76% |
| 受控 RSI（检索词） | dev 8% → **25%**，held-out 12% → **38%**；**1 代晋级 / 4 代被拒**（附拒绝原因） |
| 共享经验 + 自动扩充 | 经验跨用户复用生效；Gaucher 扩充 4 位可检索；误标审计降级 6 人 |
| **盲审** | 41 条（金标 20 + 扩充 21），随机 ID 防泄漏，浏览器本地标注；精确率 + Wilson CI + kappa |
| **扩充隔离（影子索引）** | 自动扩充作者独立索引 + RRF 合并；主检索 R@10 **80.9% → 83.9%**（配对 Δ+3.0pt，CI [0.3, 6.3]）；缺口回答率 67%→89%；净收益待 40 题验证 |

| **受控 RSI（Agent 策略）** | 通过率保持 **100%**，平均工具调用 **4.12 → 2.75（−33%）**；先拒后晋，held-out 不退步 |
| 检索 / 路由 | nDCG 基线、路由留出集（脚本见 `app/eval_*.py`） |
| **专家发现（60 条概念）** | recall@5 **52.9%** · recall@10 **88.7%** · MRR **0.919** · 覆盖 **100%** · p50 **3 ms**（标签路径） |

> 专家发现评测用**库内 4.1 万人的 OpenAlex 概念标签**自建金标（60 个概念，取该标签下被引最高的人），
> 评测走产品实际路径。基线 recall@10 仅 12.7%，定位到两个问题——概念查询在论文标题里查不到、
> 概念查询被论文结果挤掉一半名额——修复后 **recall@10 88.7%、recall@5 52.9%**，
> 且概念路径延迟从 1.45 s 降到 **3 ms**（详见 [docs/m55](docs/m55-expert-finding-eval.md)）。

> 说明：这 14 条里有一条"库外主题该不该拒答"的边界 case，历史 3 次运行中有 1 次模型用常识作答被判失败（其余为 13/14）。上面是最近一次成绩，未为了凑 100% 改判定。

## 自我改进（RSI）

它改进的是**策略**，不是代码：

```
提议变异（模型/规则，白名单：synonyms / plan_prompt）
   → dev 集评估（不退化）
   → held-out 集闸门（提升 > 1pt）
   → 预算/代数上限
   → 晋级（写快照）或拒绝（记录原因）
可随时 rollback(gen)
```

`plan_prompt` 会直接进入 Agent 的 system prompt；`synonyms` 会进入检索词扩展。

Agent 级进化需要 `python -m app.rsi_agent --generations 1 --budget-usd 0.6`：
从失败/高开销轨迹里生成策略补丁，dev 通过率提升或工具调用下降 ≥20% 才晋级，held-out 不退步才采纳。
5 代实验里 4 代被拒，原因写进了 `rsi_log`——**能拒绝自己，才算受控**。

## API

| 端点 | 说明 |
|---|---|
| `GET /chat?q=&uid=&sid=` | 一次 Agent 会话（LLM + 长短记忆） |
| `GET /chat/stream` | SSE：叙述 → 逐字 → 引用 |
| `GET /search` | facet 检索（国家/机构/被引/排序/主题分层） |
| `GET /people/{pid}` · `/work/{wid}` | 专家 / 论文详情（引用、社区、桥接） |
| `GET /citations/top?sort=count\|pagerank\|pagerank_recent` | 引用榜三口径 |
| `POST /memory/fact` · `GET /memory/timeline` | 写 / 读长期记忆（写需 `X-API-Key`） |
| `POST /memory/feedback` | 采纳/否决反馈 |
| `GET /rsi/status` · `POST /rsi/run` · `POST /rsi/rollback` | 自我改进状态/运行/回滚 |
| `GET /health` | 六项健康检查 |

## 目录结构

```
app/
  agent_llm.py      ★ Agent 主循环（LLM 工具调用 + 预算 + 强制收尾）
  session_memory.py ★ 短期记忆（滚动摘要 + 最近轮次）
  user_memory.py    ★ 长期记忆（时间有效区间）
  memory_extract.py   记忆抽取（规则优先，模型可调用 remember）
  memory_recall.py    记忆召回与注入
  rsi.py            ★ 受控自我改进（变异 → 双闸门 → 晋级/回滚）
  eval_agent.py     ★ Agent 级评测（多轮/记忆/拒答/成本）
  eval_memory.py      记忆五能力评测
  supervisor.py       路由与仲裁（classic 回退路径）
  agent.py            知识库工具集
  facets.py / search.py / citations.py / cluster.py
  api.py            FastAPI（58 个端点）
web/index.html      单文件前端（9 视图）
eval/               agent_cases · memory · rsi_dev/heldout/perturbed · answers
docs/               里程碑设计文档
tests/              236 项测试
```

## 设计取舍与局限

- **LLM 是默认路径，但不是唯一路径**：`ST_CHAT_ENGINE=classic` 可回退到确定性链路（演示/离线场景不依赖 key）。
- **工具调用有硬预算**（3 轮 / 4 次 / $0.03）：宁可强制收尾，也不让一次会话失控。
- **拒答优先于流畅**：所有工具都没返回条目时，即使模型写出了像答案的段落，也会被改成"没有找到"。
- **长期记忆只补空位**：用户这句话里显式给的条件永远优先于记忆。
- **RSI 的动作空间很小**：目前只接通 `synonyms` 与 `plan_prompt`，避免"自我改进"变成不可审计的漂移。
- **自动扩充默认关闭**：能力缺口回答率 +22pt，但会拉低原有概念查询 R@10（诊断见 m58，排除扩充作者+池=100 可恢复 3.1pt，剩余 4.7pt 为 FTS 统计漂移）；需要时用 `POST /library/expand` 显式触发；
- 已知局限：库内引用链回率 5.3%；评测集偏小（Agent 14 / 记忆 50）；单次会话 p50 约 8.8s，成本约 $0.002；单机部署、无鉴权体系（仅写接口有 key）。

## Roadmap

- [ ] 并发工具调用 + 结果去重，把 p50 从 8.8s 压到 5s 内
- [ ] 记忆冲突检测（同时有效的两个方向应告警）
- [ ] 情景记忆参与个性化排序（现在只记录）
- [ ] 评测集扩到 150 条并分层统计
- [ ] `plan_prompt` 加入 RSI 的正式评测集
- [ ] 多用户隔离与配额

## License

[MIT](LICENSE) · 数据来自 [OpenAlex](https://openalex.org/)（CC0），LLM 使用 [DeepSeek API](https://platform.deepseek.com/)。
