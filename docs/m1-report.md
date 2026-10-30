# M1 报告：采集 + 索引 + 专家检索

## 交付

| 项 | 数字 |
|---|---|
| 论文 | **8,000 篇**（最高被引 8000 篇，含摘要还原） |
| 人物 | **36,083 人**（从论文 authorship 提取，去重按 OpenAlex author id） |
| 合作边 | **88,558 条**（同篇共著，限前 12 位作者） |
| 发布为"专家" | **4,814 人**（可信门槛 accept；其中作品≥2 的 4,631、≥5 的 716） |
| 数据库 | SQLite WAL，**121 MB** |
| 采集耗时 | **157 秒**（40 页 × 200 篇，页间 0.6s 节流） |
| API | 6 个端点，端口 8020，`/health` `/stats` `/people` `/people/{pid}` `/works` `/graph/{pid}` |

## 三个真实的工程发现（都不是文档里写的）

### ① 无 API key 的 OpenAlex 是"按请求计费"，不是按条数

响应头显示：每请求 `$0.0001`，每日预算 `$0.10`（=1000 次），**与 per_page 无关**。
最初按"人"采集（每人 1 次 works 请求）→ 一天最多 1000 人，且试跑就把预算打光（HTTP 401）。

改成 **works-first**：一页 200 篇论文的 `authorships` 里带几百位作者，
同样只花 1 次额度。8000 篇论文 → 36,083 人，只用了 40 次额度。

### ② cursor 翻页在无 key 时会 401，page 翻页正常

`cursor=*` 第 1 页 200，第 2 页稳定 401；而 `page=1/2/5/20` 全部 200。
这是踩出来的（试了 5 次重试仍 401），改成 page 翻页后全量采集零错误。

### ③ 可信门槛的两个陷阱（这是 M3 的核心素材）

**陷阱 A：绝对被引阈值被样本污染。**
样本本身就是"最高被引 8000 篇"，平均被引上千 → `cites>=500` 对 **100%** 的人成立，
accept 率虚高到 **65%**（23,562/36,083）。改成**样本内分位数**（p50=5504 / p90=18368）。

**陷阱 B：把 ORCID 当必要条件会把名人判掉。**
Geoffrey Hinton（13 篇、24 万被引、无 ORCID）得分 0.65 → 只能进 pending。
重新分配权重：身份 0.35（ORCID）/ 产出 0.25（≥5 篇）/ 影响 0.20（被引前 10%）/ 归属 0.15。

重判后：accept **4,814**（13%）、review 18,726、reject 12,543。
单测验证：Hinton 0.80 accept、单篇小作者 0.25 reject、会议名 0.00 reject。

## API 抽样

```
/people?q=machine learning  → Jerome Friedman(8篇/11.4万引)、John Platt、Chih-Jen Lin…
/people?q=Geoffrey Hinton   → Geoffrey E. Hinton（13 篇，243,424 引）
/works?q=transformer        → A Survey on Vision Transformer (2022, 4682 引)…
/graph/122?depth=1          → 26 节点 / 80 边（Bengio 的一跳合作网，含 Goodfellow）
```

## M1 遗留问题（进入 M2/M3）

1. 单篇作者占 80%（28,946/36,083）——默认 `min_works=2` 过滤，但库内仍在，M3 要决定"收录"还是"只做候选"。
2. `/people` 只有 FTS5 词面匹配，"ML 方向的华人学者"这类语义查询召回差 → M2 上向量 + RRF/DBSF。
3. 缺口：没有 gold set，还无法给出 nDCG/MRR；没有 Agent 问答；没有 SSE。
