<div align="center">

# ResearchScout

**学术专家检索 · 长期记忆 · 分层评测 · 受控自我改进**

从 OpenAlex 到 4.7 万学者知识库：混合检索、引用图分析、跨会话记忆，以及一套"能证明自己有效"的评测。

[![CI](https://github.com/JustinSian/ResearchScout/actions/workflows/ci.yml/badge.svg)](https://github.com/JustinSian/ResearchScout/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-211%20passed-brightgreen.svg)](#测试)
[![Docs](https://img.shields.io/badge/docs-53%20milestones-informational.svg)](docs/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[快速开始](#快速开始) · [实测结果](#实测结果) · [架构](#架构) · [设计取舍](#设计取舍) · [文档索引](#文档索引)

</div>

---

## 这是什么

一个学术专家知识库 + 检索 Agent。它不追求"功能多"，而是追求**每个能力都能回答"你怎么知道它有效"**：

- **数据可信**：4.7 万学者不是"拉到就入库"，而是四组加权证据 + 复核队列，国家字段带来源标记；
- **检索可证**：BM25 + 向量 + RRF 融合，但**融合没赢过单路就不启用**（有对照实验）；
- **答不上来会说不知道**：交叉编码器判拒答；库外问题才转网络，且明确标注"未经本库核验"；
- **会记住你**：三层长期记忆（语义/情景/程序），事实带 `valid_from/valid_to`，转向后旧值失效但保留历史；
- **会改进自己，但受控**：白名单策略空间 + dev/held-out 双闸门 + 预算上限 + 一键回滚。

## 为什么不是"又一个 RAG"

| 常见做法 | 本项目的做法 | 证据 |
|---|---|---|
| 拉到人就入库 | 四组加权证据打分 + 复核队列，门槛可审计 | [m1](docs/m1-report.md) · [m5](docs/m5-report.md) |
| 检索只做 BM25 | BM25 + 向量 + RRF，**但融合赢不过单路就回退** | `app/eval_*.py` |
| 上多智能体 | **先做对照实验**再决定要不要 | [m45](docs/m45-ab-single-vs-multi.md) |
| 上 LLM 做规划 | 规则与 LLM 各跑一遍，按数据选 | [m49](docs/m49-llm-planner.md) · [m50](docs/m50-react-loop.md) |
| 记忆=向量库 | 三层记忆 + 时间有效区间，可回答"我以前做什么" | [m51](docs/m51-user-memory.md) |
| 自我改进=让模型改自己 | 白名单变异 + 双闸门 + 预算 + 回滚 | [m53](docs/m53-rsi.md) |

## 实测结果

不报"跑通了"，只报对照数字（全部可用仓库内命令复现）。

| 实验 | 结果 |
|---|---|
| 单 agent vs supervisor 编排 | 通过率 85% → **100%**；p50 447 → **123 ms**（[m45](docs/m45-ab-single-vs-multi.md)） |
| 规则规划 vs LLM 规划 | 通过率 100% vs 100%；p50 **135.7 ms vs 1422.9 ms**；成本 **$0 vs $0.0039**（[m49](docs/m49-llm-planner.md)） |
| 规则路径 vs ReAct 工具循环 | 100% vs 80%；工具调用 **0.95 vs 5.9**；p50 1.6 s vs 8.5 s（[m50](docs/m50-react-loop.md)） |
| verifier 故障注入 | 误报 **0%**；无意义注入检出 **100%**；跨人注入 **100%**（[m48](docs/m48-injection-bug-correction.md)） |
| 长期记忆（50 条五能力） | 通过率 **100%**；过期事实误用 **0%**；记忆进检索 **76%**（[m51](docs/m51-user-memory.md)） |
| 受控自我改进（5 代） | dev 8% → **25%**，held-out 12% → **38%**；**1 代晋级 / 4 代被拒**（[m53](docs/m53-rsi.md)） |

数据规模：**47,569** 位学者（发布集 8,490）· **11,104** 篇论文 · **30,101** 条库内引用边 · 国家覆盖 **97%**（作者 39,062 / 机构 7,425 / 未知 1,082）· **101,414** 条合作边 · 50 个 API 端点 · **211** 项测试 · 53 篇里程碑文档。

## 架构

```
                        ┌──────────────────────────────┐
用户问题 + uid ────────▶ │ 长期记忆召回（事实/时间衰减） │
                        └──────────────┬───────────────┘
                                       ▼
                        ┌──────────────────────────────┐
                        │ Supervisor（路由 + 仲裁合并） │
                        └───┬──────────┬───────────┬───┘
                            ▼          ▼           ▼
                      kb_agent    task_agent   web_agent
                    （工具集）   （多步尽调）  （AnySearch）
                            │          │           │
                    ┌───────┴────┐ ┌───┴─────┐     │
                    ▼            ▼ ▼         ▼     ▼
              facets 检索    引用图      三角色   网络结果
            (FTS+向量+RRF) (PageRank/   researcher  (标注未核验)
                            聚类/桥接)  analyst
                                        verifier
                            │
                            ▼
                    答案 + 引用 + 记忆说明

侧边两套"证据机器"：
  eval/     检索 nDCG · 路由 · 答案级 20 条 · 记忆 50 条 · 注入实验
  RSI 回路   变异 → dev 评估 → held-out 闸门 → 晋级/回滚
```

## 快速开始

```bash
git clone https://github.com/JustinSian/ResearchScout.git
cd ResearchScout

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 用 6.6 MB 的演示快照直接起（无需采集、无需模型）
ST_DB=data/snapshot/demo.db uvicorn app.api:app --host 0.0.0.0 --port 8020
```

打开 <http://127.0.0.1:8020> 即可：专家墙 / 检索 / 画像 / 合作图谱 / Agent 问答 / 尽调 / 复核 / 记忆。

```bash
# 直接问
curl "http://127.0.0.1:8020/chat?q=中国的呼吸科专家"
curl "http://127.0.0.1:8020/chat?q=找研究帕金森的专家&uid=demo"   # 带长期记忆
```

## 它能做什么

| 输入 | 输出 |
|---|---|
| 中国的呼吸科专家 | 结构化 facet 检索（国家/机构/被引/排序），回显识别到的条件 |
| 谁和钟南山合作过 | 合作网络遍历（检索做不到的关系问题） |
| 这篇论文引用了谁 / 两跳 | 库内引用链（双向，含 PageRank 与聚类） |
| 那这些人里谁被引最高 | 多轮指代：在上一轮结果集内重排 |
| 我研究帕金森 | 写入长期记忆；之后的"推荐几个专家"自动按你的方向检索 |
| 找研究 quantum gravity 的专家 | 明确拒答（而不是编造），并可降级到公开网络并标注 |

## 常用命令

```bash
python -m pytest tests -q                 # 211 项测试
python -m app.eval_answers                # 答案级评测（20 条 gold set）
python -m app.eval_memory                 # 记忆五能力评测（50 条）
python -m app.ab_planner                  # 规则 vs LLM 规划
python -m app.ab_loop                     # 规则 vs ReAct 循环
python -m app.ab_verifier                 # verifier 故障注入
python -m app.rsi --generations 3 --dry-run   # 受控自我改进（不落库）
```

> 完整库（`data/scholar.db`，387 MB）不随仓库分发；演示快照够跑 UI 与大部分接口，评测类命令需要完整库。

## 主要 API

| 端点 | 说明 |
|---|---|
| `GET /chat?q=&sid=&uid=` | 一次问答（路由/记忆/引用） |
| `GET /chat/stream` | SSE 流式：思考叙述 → 逐字 → 引用 |
| `GET /search` | facet 检索（country / inst_kind / cites_min / sort / topic_high） |
| `GET /people/{pid}` · `/work/{wid}` | 学者 / 论文详情（含引用、社区、桥接） |
| `GET /citations/top?sort=count\|pagerank\|pagerank_recent` | 引用榜（三种口径） |
| `POST /memory/fact` · `GET /memory/timeline` | 写入 / 查看长期记忆（写接口需 `X-API-Key`） |
| `GET /rsi/status` · `POST /rsi/run` | 自我改进状态 / 跑一代 |
| `GET /health` | 六项健康检查 |

## 目录结构

```
app/
  api.py            FastAPI 入口（50 个端点）
  supervisor.py     路由与仲裁（kb / task / web / hybrid）
  agent.py          工具集（find_expert / profile / collaborators / works / citations …）
  facets.py         自然语言 → 结构化条件
  search.py         FTS5 + 向量 + RRF 混合检索
  citations.py      引用链 / 被引 / PageRank 口径
  cluster.py        Louvain 聚类 + 割点/边介数桥接
  user_memory.py    三层长期记忆（时间有效区间）
  memory_extract.py 记忆抽取（规则优先，LLM 可选）
  memory_recall.py  记忆召回与注入（只填空不覆盖）
  rsi.py            受控自我改进（变异 → 双闸门 → 晋级/回滚）
  llm.py            DeepSeek 客户端（可选路径，带成本记账）
  ab_*.py           五组对照实验
eval/               评测集（answers / memory / rsi_dev / rsi_heldout / rsi_perturbed）
docs/               53 篇里程碑文档（m1–m53）
web/index.html      单文件前端（8 个视图，零构建）
tests/              211 项测试
```

## 设计取舍

这一节比功能列表重要——每个选择都有代价。

1. **默认链路不含 LLM**。规则路由 + 模板作答，换来零 token 成本、可复现、p50 ~120 ms。
   LLM 只作为可选路径（`ST_PLANNER=auto`），并且有对照数据证明它在这个闭域问题上不划算。
2. **不做"自由多智能体"**。supervisor 是确定性编排；子 agent 之间用结构化 dict 通信，没有消息队列。
   ——这个规模（单机、秒级任务）上 MQ 只会增加故障面。
3. **只统计库内引用**。`被引 1807` 是库内口径，和 Google Scholar 不可比；这是刻意的，否则无法自证。
4. **PageRank 不并入专家排名**。稀疏图（库内链回率 5.3%）会让 6 次引用的论文排第一，只作可选口径。
5. **RSI 只改白名单里的同义词**。其余三类动作（提示词/权重/工具顺序）只做校验不生效——先跑通闭环，再扩动作空间。

## 已知局限

- 语料是 OpenAlex 子集，库内引用链回率 **5.3%**，跨库引用不可见；
- 评测集偏小（答案 20 条 / 记忆 50 条 / RSI dev 12 条），结论的置信区间较宽；
- 记忆抽取以规则为主，否定句（"我不喜欢综述"）会抽成正向偏好；
- RSI 的 held-out 是**问法留出**，不能迁移到没见过的主题；
- 单机单进程部署，无鉴权体系（只有写接口的 `X-API-Key`）、无限流。

## Roadmap

- [ ] 把 `eval/rsi_perturbed.jsonl` 接入最终验收（现在只生成未使用）
- [ ] 记忆冲突检测（同时有效的两个方向应告警）
- [ ] 情景记忆参与个性化排序（现在只记录）
- [ ] 评测集扩到 150 条并分层统计
- [ ] 前端把"记忆时间线"做成演示脚本（说完"转帕金森"即时看到变化）

## 文档索引

53 篇里程碑文档按主题分组（全量见 [docs/](docs/)）：

| 主题 | 文档 |
|---|---|
| 采集与可信准入 | m1–m12、m26–m33 |
| 检索与融合 | m6、m16、m20–m25、m34–m38 |
| 引用与图分析 | m39–m43 |
| Agent 与调度 | m14、m44–m50 |
| 长期记忆与 RSI | m51–m53 |
| 工程与运维 | m17–m19、m28、m31 |

关键几篇：[m33 国家字段的静默错误](docs/m33-country.md) · [m39 PageRank 的口径](docs/m39-pagerank.md) · [m45 多智能体对照](docs/m45-ab-single-vs-multi.md) · [m48 更正自己的实验 bug](docs/m48-injection-bug-correction.md) · [m51 长期记忆](docs/m51-user-memory.md) · [m53 受控 RSI](docs/m53-rsi.md)

## 贡献

```bash
pip install -r requirements.txt
python -m pytest tests -q          # 提交前必须全绿
python -m compileall -q app        # CI 还会做语法检查
```

- 新增能力请附一份 `docs/mNN-*.md`：做了什么、数字多少、**遗留什么**；
- 改评测逻辑必须同时更新对应对照实验，不接受"只改阈值不提数据"；
- 发现旧结论有误，**不要改历史文档**，新增一篇更正并互相引用（见 m46 → m48）。

## 致谢与许可

- 数据来自 [OpenAlex](https://openalex.org/)（CC0）；
- 可选 LLM 路径使用 [DeepSeek API](https://platform.deepseek.com/)；
- 依赖 FastAPI / LangGraph / transformers / onnxruntime。

[MIT](LICENSE) © 2026 · 本项目从 [ScholarTrust](https://github.com/JustinSian/ScholarTrust) 演进而来。
