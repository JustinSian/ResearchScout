# ScholarTrust · 学术专家知识库与可信尽调 Agent

> 在一台**没有 docker、没有 sudo、没有 GPU** 的机器上，用 FastAPI + SQLite(FTS5) + 两个
> ONNX 模型，从 0 搭出「学术数据采集 → 可信准入 → 混合检索 → 多 Agent 尽调」的完整闭环。
> 47,569 位学者 / 11,104 篇论文 / 101,414 条合作边 /
> 102 份全量成果档案（23,242 篇，人均 228 篇），公网可访问。

---

## 一、出发点：为什么再做一次学术知识库

对标项目（某学术图谱学术图谱）能查到人和论文，但有三件事没说清：

1. **数据可不可信**——从 OpenAlex 拉来的人，凭什么算"专家"？
2. **检索好不好用**——BM25 排出来的第一页，真的是最相关的吗？
3. **答不上来时怎么办**——是编一个像样的答案，还是明确说"不知道"？

所以这个项目的主线不是"功能多"，而是**每加一个能力，都必须能回答"你怎么知道它有效"**。
29 份里程碑报告里，有 5 次是**推翻自己刚做的东西**（见第四节与第五节）。

## 二、它不是另一个检索框

| 常见做法 | 本项目的做法 |
|---|---|
| 拉到人就入库 | 四组加权证据打分 + 复核队列，门槛可审计 |
| 只做 BM25 | BM25 + 向量 + 融合，但**融合没赢过单路就不启用** |
| 大模型直接答 | 规则规划 + 事实拼装，**每条结论可回溯到 pid/wid** |
| 答不上来就胡编 | 交叉编码器判拒答；库外问题才转网络，且标注"未核验" |
| 演示跑通就算完 | 六项健康检查 + 定时守护 + 回归基线 |

## 三、架构

```
                        ┌──────────────────────────────────────────┐
   用户问题 ──────────▶ │ Supervisor（能力探测 → 路由 → 仲裁合并）  │
                        └───────┬──────────────┬──────────────┬────┘
                                ▼              ▼              ▼
                        kb_agent(工具)   task_agent(多步)   web_agent(AnySearch)
                                │              │              │
                    ┌───────────┴──────┐   ┌───┴────────┐     │
                    ▼                  ▼   ▼            ▼     ▼
              混合检索(BM25+向量)  深档案  researcher   analyst  verifier
                    │                  │        └──────┬───────┘
                    ▼                  ▼               ▼
              SQLite/FTS5          dossier 表      虚拟文件系统 + 审计 + 追踪
```

数据层：SQLite（WAL）+ FTS5（porter unicode61）+ 本地 bge-small 向量 + bge-reranker(ONNX)。
没有任何外部服务依赖。

## 四、六层能力与量化结果

### 1. 采集：把请求预算算清楚

OpenAlex 无 key 时**按请求计费**（1,000 次/天），逐人采集必然打光预算。
改成 **works-first**：一页 200 篇论文带出数百位作者，8,000 篇只花 40 次请求。
`cursor` 翻页在无 key 时稳定 401（踩了两次）→ 全部改 `page` 翻页。
申请到免费 API key 后额度 10,000 次/天，用「403 自动降级」保证 key 失效时流水线不挂。

### 2. 可信准入：门槛必须可审计

`app/judge.py` 用四组加权证据（身份/产出/影响/归属），输出 score + verdict + reasons。
`accept ≥ 0.80`、`review ≥ 0.50`，拒绝进复核队列（带留痕）。

- 人工抽检 60 条后发现：三档**全是真人**（采集走 authorship，源头就是人），
  门槛的真实作用不是"判是不是人"，而是"筛产出"；
- 扩张时的「网络背书」（与已验证专家共同论文 ≥2 篇且本人作品 ≥10 篇）
  抽样 40 条，精度 **77.5%，95% CI [62.5%, 87.7%]**，占比 2.8%（上限 5%）；
- 曾把"用过网络背书就打标"做成语义错误（3,542 人全标网络背书）→ 改成
  "**去掉该证据后是否仍达标**"，最终独立核验 8,252 / 网络背书 238。

### 3. 检索：融合是要被证明的

BM25 + 向量 + RRF/DBSF 全参数可切，两套 gold set（按被引 / 随机抽样）。

**四次推翻自己**：
1. 权威性先验看着 +74%，换随机真值后变差 → **标签泄漏**，弃用；
2. 摘要级 chunk 融合在按被引真值上 +0.043，随机真值 −0.056 → 弃用；
3. 全文 chunk 加入后同样只在"按被引真值"上赢 → 只用于证据，不用于排序；
4. 自创的"共享作者集合"质量指标大量误报 → 改成逐篇对照上游。

结论：**融合没有赢过更强的单路，就不启用融合**。

### 4. 拒答：只有交叉编码器分得开

稠密相似度和 BM25 分都分不开"有答案/无答案"；cross-encoder 可以：

```
有答案 ≥ 0.858    无答案 ≤ 0.425    阈值 0.60
```

### 5. 多 Agent：调度层，不是自主性

- Supervisor 分派到 kb / task / web 三个 agent，仲裁时**分节标注**，绝不混证据；
- 路由准确率：调参集 19/19（过拟合，不作数）、**留出集 9/10 = 90%**（旧版 40%）；
- task agent 内部是 researcher / analyst / verifier 三角色协作，
  上下文压缩 **29.2×**（主上下文 254B vs 落盘 7,423B）；
- verifier 抓到过真实错误：把"晶体结构"方向的同名者从审稿人推荐里剔除；
- 记忆层：同问题二次尽调 **45× 加速**（2,246ms → 50ms），带语料版本校验防旧结论。

'### 6. 实体层：期刊与机构

对标 AMiner 的 `venue/*` 与 `organization/na` 补齐实体层：

| 实体 | 规模 | 要点 |
|---|---|---|
| 期刊 | **2,314 个**（2,308 期刊 + 6 仓储），10,599/11,104 篇挂载 | `PubMed` 这类索引库单列，不再冒充期刊 |
| 机构 | **4,319 个**，38,663 人挂载 | 20 条缩写展开 + 去连接词；**只在归一化完全一致时自动合并** |

近似重复机构按 `parent_child / similar / same_tokens_confirm` 三类报告，
**不自动合并**——"University of York" 与 "York University" 是英加两所学校，
机器分不出来，交给人。

### 7. 可观测与运维'

- 中间件：令牌桶限流 + 参数脱敏（邮箱/手机/长 ID）+ 审计表 + 成本核算；
- 链路追踪：`tasks / steps / spans`，`/trace/{tid}` 看每步耗时；
- 健康检查六项：网络背书占比、无作者论文、重复 openalex_id、悬空边、
  **FTS 行数一致性**、无主题占比；守护进程每 6 小时落盘 + 告警文件；
- 上线当天就抓到 **6,567 人没进 FTS 索引**（搜索里不存在）。

## 五、最值钱的部分：三次真实事故

| 事故 | 怎么发现 | 怎么修 | 结果 |
|---|---|---|---|
| 作者挂错论文 | 用户让我"自己看看图谱"，我抽查合作者名单 | 根因：SQLite UPSERT 走更新分支时 `lastrowid` 不刷新 | 全量重建 48,935 条作者关联 / 88,611 条边，抽样 12/12 与 OpenAlex 一致 |
| 网络背书语义写错 | 我自己算了一下比例（42% 明显异常） | 改成"去掉证据后是否仍达标" | 从 3,542 降到 238（2.8%） |
| 6,567 人搜不到 | 健康检查上线第一天 | 增量补 FTS + 建档时同步写入 | 索引 47,569 = 人物 47,569 |

## 六、快速开始

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 填 ST_API_KEY / ST_OPENALEX_KEY / ANYSEARCH_API_KEY
python -m app.init_db
python -m uvicorn app.api:app --host 0.0.0.0 --port 8020
```

### 没有 API key / 不想抓数据？用仓库内的演示快照

```bash
ST_DB=data/snapshot/demo.db python -m uvicorn app.api:app --port 8020
```

快照 **6.3 MB**：60 位专家（全量档案）、1,200 篇论文、2,827 条作者关联、
419 个期刊、31 个机构，引用完整（无孤儿行）。由 `python -m app.snapshot` 从主库裁剪生成。

常用命令：

```bash
python -m app.regression                    # 24 题 Agent 回归（对比基线）
python -m app.eval_routing                  # 路由评测（--file 指定留出集）
python -m app.build_dossiers --network 527,503,498 --limit 100   # 建全量档案
python -m app.health_watch --once           # 数据健康检查
python -m app.repair_fts                    # 补 FTS 索引
python -m app.snapshot                      # 生成演示快照（60 人 / 1200 篇，约 6 MB）

# 质量门禁
python -m pytest tests -q                   # 42 项单测（离线，不依赖模型与网络）
python -m coverage run --source=app -m pytest tests && python -m coverage report
```

| 质量项 | 现状 |
|---|---|
| 单测 | **42 项**，全离线 |
| 核心模块覆盖率 | supervisor 83% / judge 89% / onboard 93% / websearch 88% / hooks 71% |
| 整体覆盖率 | 14%（大量一次性脚本与数据流水线不在测试范围，如实标注） |
| CI | GitHub Actions：pytest + `compileall` |
| 提交历史 | 有（含"归档 228 个历史脚本"这类可追溯的整理记录） |

## 六点五、MCP：把能力接进 Cursor / Claude

除了 HTTP，还提供一个 **MCP server**（JSON-RPC over stdio，不依赖 mcp SDK）：

```bash
python -m app.mcp_server          # stdio 传输
```

暴露 6 个工具：`search_experts`、`get_profile`、`compare_experts`、
`find_reviewers`、`ask`、`health`。

客户端配置（Cursor / Claude Desktop 的 mcpServers 段）：

```json
{
  "mcpServers": {
    "scholartrust": {
      "command": "/home/<user>/scholar-trust/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "env": { "PYTHONPATH": "/home/<user>/scholar-trust" }
    }
  }
}
```

实测握手与调用：

```
initialize → scholartrust 0.1.0 / protocol 2024-11-05
tools/list → 6 个工具
tools/call get_profile(527) → 钟南山 940 篇 / 深档 h=83
tools/call nope → JSON-RPC -32601 unknown tool
```

## 七、接口一览

```
GET  /stats                 规模统计
GET  /people/{pid}          画像        GET  /experts   专家墙（排序/筛选/网络）
GET  /works                 论文检索
GET  /search                混合检索（bm25/dense/hybrid）
GET  /graph/{pid}           合作网络    GET  /graph/path      最短合作链
GET  /dossiers              深档案列表  GET  /dossier/{pid}   单份档案 + 代表作
GET  /dossier/{pid}/changes 档案变更
POST /chat                  问答（Supervisor 调度：kb/task/web/hybrid）
GET  /chat/stream           SSE 逐字
GET  /chat/welcome          开场引导（示例 + 能力边界）
POST /task                  多步尽调任务（规划 → 子 Agent → 报告）
GET  /trace/{id}            链路追踪    GET  /audit           审计与成本
GET  /vfs?path=             读虚拟文件系统产物
GET  /skills                Skill 索引  GET  /health/trust    六项健康检查
GET  /memory                记忆层状态
```

## 八、目录

```
app/            judge(可信判定) search(检索) agent(单轮+工具) harness(多步)
                subagents(researcher/analyst/verifier) supervisor(调度)
                dossiers(档案) memory(缓存+diff) hooks(中间件) skills(Skills)
                websearch(AnySearch 兜底) onboard(引导) health(健康检查)
web/index.html  单文件 SPA（专家墙/检索/画像/图谱/问答/尽调/复核）
eval/           gold set、基线与路由评测集
skills/         Markdown 技能（渐进式披露）
docs/           m1~m28 里程碑报告
```

## 九、与 AMiner 的数据模型对照

对标 [AMiner 开放平台](https://open.aminer.cn/open/doc) 的 28 个接口后，我们的差距很清楚：

| AMiner 有 | 我们有 | 结论 |
|---|---|---|
| `person/detail` + `person/figure`（简介/教育/工作/兴趣） | `people` + `name_zh` + `headline` | 字段更薄，但**准入门槛**更严（他们没有可信判定） |
| `person/paper/relation`、`paper/relation`（引用链） | `work_authors` + `edges` | 等价，但我们**没有引用关系**（cited_by 只是一条边） |
| `organization/na`、`na/pro`（机构消歧） | **已补**：4,319 个机构实体 / 38,663 人挂载；近似重复只报告不合并 | 归一化 + 候选对，比自动合并保守 |
| `venue/*`（期刊实体、期刊论文关系） | **已补**：2,314 个期刊实体 / 10,599 篇挂载；`PubMed` 等仓储单列 | 支撑"常发期刊"与期刊分析 |
| `paper/qa/search`（topic_high/middle/low 三层权重） | `app/subagents.LEX` 一张中英词表 | 我们的主题匹配是简化版，可借鉴三层加权 |
| `patent/*`、`project/person` | 无 | 不在本项目范围（不做） |

**值得补的两项（按性价比）**：① `venue` 实体化（支撑期刊分析、审稿人推荐的"常发期刊"匹配）；
② 机构别名表（现在"广医一院/广州医科大学附属第一医院"会算成两个机构）。

## 十、已知限制（诚实清单）

1. **深档案只有 102 位**：全库 8,490 位发布专家里，多数只有画像，没有 h-index 与主题分布；
2. **中文名只有 41 人**：OpenAlex 只有拉丁转写，不做自动音译（"Jun Liu" 可能是刘军/刘俊）；
3. **中文语义检索弱**：语料是英文，中文主题词靠一张医学词表映射，冷门词搜不到；
4. **重名消歧没做**：只做了"同名多条 + 被引选主 + 手动合并"，系统性消歧是空白；
5. **nDCG 随语料变化**：0.2354 → 0.1687，是 gold set 固定而分母变大的必然结果，每次都要重录基线；
6. **单机单 worker**：检索约 20 rps 饱和，是 SQLite 的极限而非架构上限；
7. **没有安全沙箱**：没有 docker，subprocess 隔离不可靠，故意不做代码执行；
8. **路由靠关键词**：换个说法可能失效（"一起发过哪些论文"就被判成 hybrid）。
