# M17 交付：100 位专家全量档案（dossier）

## 结果

| 指标 | 改造前 | 现在 |
|---|---|---|
| 建档专家 | 0 | **102 人** |
| 档案论文条目 | — | **23,242 篇** |
| 人均论文 | 1.7 篇（全库平均） | **227.9 篇**（建档专家） |
| 单专家最高 | 35 篇 | **1,174 篇**（李兰娟） |

头部档案：

| 专家 | 全量论文 | h-index | 被引合计 | 年份跨度 |
|---|---|---|---|---|
| 李兰娟 Lanjuan Li | 1,174 | 117 | 101,527 | 1998–2026 |
| Ahmedin Jemal | 1,096 | 173 | 742,394 | 1993–2026 |
| 何建行 Jianxing He | 1,038 | 74 | 68,497 | 1986–2026 |
| 钟南山 Nanshan Zhong | 938 | 83 | 68,984 | 1981–2026 |
| Ying Cheng | 888 | 85 | 43,778 | 1989–2026 |

## 选人口径（B 方案）

三个种子的合作网络，先放种子本人，其余按**共同论文数**降序：

```
527 钟南山 / 503 李兰娟 / 498 何建行
→ Wenhua Liang(527篇) / Hengrui Liang(220) / Hongcui Cao(283) / Jun Liu(477) / 关伟杰(288) ...
```

全部落在呼吸、重症、感染方向，网络能连成片。

## 数据结构

```sql
dossier(pid, author_id, fetched_at, works_total, works_loaded, metrics_json)
dossier_work(pid, wid, title, year, cites, venue, doi, is_rep)
```

`metrics_json` 里是尽调 Agent 需要的派生指标：

```json
{"works": 938, "works_total_openalex": 938, "cites_sum": 68984, "cites_median": 12,
 "h_index": 83, "first_year": 1981, "last_year": 2026, "recent_5y_share": 0.213,
 "top_venues": [["Journal of Thoracic Disease", 34], ...],
 "rep_papers": [{"title": "...", "year": 2020, "cites": 30248}, ...]}
```

## 接口

```
GET /dossiers?limit=100        汇总（作品数/h-index/年份跨度）
GET /dossier/{pid}?papers=20   单份档案（指标 + 代表作 + 最新/高被引论文）
```

前端画像页新增「深档案」卡片：论文数、h-index、被引合计、年份跨度、近五年占比、
高频期刊、代表作前 5。

## 成本

- 每位专家 1~6 次请求（200 篇/页），102 人共约 **130 次**；
- API key 生效后额度 **10,000 次/天**（$1/天），实测 `x-ratelimit-limit: 10000`。

## 踩的坑

1. **key 申请后必须先点邮件激活**：激活前带 key 请求一律 403，
   我误以为是传参方式不对，试了 query / Bearer / api_key 头三种都失败。
   代码里做了"403 自动降级"，所以激活前也能继续用无 key 模式跑。
2. **`openalex.py` 读环境变量太早**：`app.config.load_env()` 还没执行就 `os.environ.get`，
   导致"key 已配置: False"，看起来像没写进 .env。在模块顶部显式 `load_env()` 才对。

## 下一步（M18）

有了这 102 份档案，Agent 才可能回答：

- "比较钟南山和李兰娟近五年的方向差异"（需要主题迁移 + 代表作）
- "谁更适合审这篇肺癌免疫治疗稿件"（需要 h-index + 领域匹配 + 利益冲突）
- "这位专家 2010 年后产出骤降，为什么"（需要年份分布 + 异常检测）

M18 要做的是**任务规划 + 子 Agent + 虚拟文件系统**，让这些多步问题跑起来。
