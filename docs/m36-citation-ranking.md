# M36：引用加权排名（`sort=impact`）

## 问题

专家墙原来只有 `sort=cites`，用的是 OpenAlex **全站被引**。它有两个偏差：

1. 被综述作者和大团队通讯作者主导——写一篇 NEJM 综述，被引 60 万，但不代表他在本库语料里"被使用"；
2. 与我们的 11k 篇语料无关——库里所有论文都被引 0 次的人，也可能因为全站被引高而排第一。

实测两种排序的前五**完全不重合**：

| sort=cites | sort=impact |
|---|---|
| Ahmedin Jemal（605,260） | **李兰娟（库内被引 1,807）** |
| Rebecca L. Siegel（514,500） | 何建行（1,063） |
| Freddie Bray（418,316） | 钟南山（920） |
| Jacques Ferlay（399,411） | 梁文华（478） |
| John P. Perdew（336,730） | Ahmedin Jemal（415） |

## 口径

`person_impact` 表，全量重算 0.35 秒：

| 字段 | 含义 |
|---|---|
| `in_corpus_cites` | 其论文被**库内**引用的总次数（同一篇被引两次算两次） |
| `cited_works` | 被引过的**不同**论文数（防止一篇爆款撑起全部） |
| `impact_per_work` | `in_corpus_cites / max(works_count,1)`，压制"靠篇数堆量" |

聚合 SQL（`app/impact.py` 的 `IMPACT_SQL`，测试用同一段）：

```sql
INSERT INTO person_impact
SELECT wa.pid, COUNT(*), COUNT(DISTINCT r.ref_wid),
       ROUND(1.0*COUNT(*)/MAX(MAX(p.works_count,1),1), 2)
FROM work_refs r
JOIN work_authors wa ON wa.wid = r.ref_wid
JOIN people p       ON p.pid  = wa.pid
WHERE r.ref_wid IS NOT NULL
GROUP BY wa.pid;
```

## 为什么这比 `cited_by_count` 可信

1. **可逐条追溯**：每个人的每次被引都能用 `/work/{wid}/cited-by` 复核；
2. **口径写死**：不混入全站数据，不受 OpenAlex 更新影响；
3. **可重建**：`python -m app.impact` 秒级重算，引用表更新后不会"数字对不上"。

## 边界（必须说清）

- 这是**库内**被引，不是学术影响力绝对值：库只有 11k 篇，链回率 5.3%，
  冷门领域的人可能被系统性低估；
- `impact_per_work` 对"少而精"有利，对"高产团队"不利，**两个指标要一起看**；
- 引用数据没构建时，`/experts?sort=impact` 会**退回按被引**，并在响应里写明
  `note`，不会假装有数据。

## 落地清单

| 文件 | 改动 |
|---|---|
| `app/impact.py` | 新表 + 构建脚本（幂等，写 `meta('person_impact')`） |
| `app/experts.py` | `BASE` 增加 impact 三列；`SORTS["impact"]`；空表时回退并带 `note` |
| `app/api.py` | `/experts` 的 sort 白名单加 `impact` |
| `app/snapshot.py` | 快照带 `person_impact`，**只复制 demo 那 60 人**（否则 3.3 万行塞进 6MB 库） |
| `tests/test_impact.py` | 5 项：口径、除零、库外引用不计、排序字段注册、快照含表 |

## 验收

```
引用加权构建：33296 人 / 最高 1807 次（pid=503）/ 0.35s
sort=impact 前 10：1807, 1063, 920, 478, 415, 324, 316, 310, 288, 282（单调不增 ✓）
快照：person_impact 60 行，Top3 = 李兰娟 1807 / 何建行 1063 / 钟南山 920
测试：89 项全过
```
