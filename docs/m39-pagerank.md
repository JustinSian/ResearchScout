# M39：引用图 PageRank，以及一个反直觉结果

## 做了什么

在库内引用图（11,104 节点 / 30,874 边）上跑阻尼 0.85 的幂迭代，结果落 `work_rank`。

```bash
python -m app.rank      # 0.44 秒
```

对外：

| 入口 | 变化 |
|---|---|
| `GET /citations/top?sort=count\|pagerank` | 两种口径并列可选 |
| `GET /work/{wid}` | 多了 `pagerank` 与 `pagerank_pct`（分位） |
| `GET /work/{wid}/citations?offset=` | 引用列表分页 |
| `GET /work/{wid}/cited-by?offset=` | 被引列表分页 |
| 前端论文页 | 上一页/下一页 + PageRank 分位 |

## 反直觉结果：6 次引用排第一

| 排名 | 按库内被引次数 | 按 PageRank |
|---|---|---|
| 1 | Clinical Characteristics of Coronavirus 2019（88 次） | **Multidimensional Scaling by Optimizing Goodness of Fit…（6 次）** |
| 2 | Self-Consistent Equations…（64 次） | Identification of a Novel Coronavirus…（24 次） |
| 3 | A Novel Coronavirus from Patients with Pneumonia（63 次） | A Novel Coronavirus from Patients…（63 次） |

**一篇只有 6 次库内引用的论文，PageRank 排第一。** 原因：它被少数几篇"同样高分"的论文引用，
形成了小而密的簇；而 88 次被引的 COVID 论文，引用者大多是低权重的临床文章。

在完整引用图上这通常是对的（PageRank 就是为这种情形设计的）；但在我们这种
**只覆盖 5.3% 边**的稀疏子图上，它会放大"小圈子互相引用"，而不是"领域基石"。

## 决策：PageRank 只作为可选口径，不用于专家排名

- `/citations/top` 默认仍是 `count`（直观、可解释）；
- `sort=pagerank` 提供给想研究"引用结构"的调用方；
- **没有**把 PageRank 折进 `person_impact`：把稀疏图上的偏差乘到人身上，
  会让"小圈子里的高产作者"排到钟南山前面，这种排名没法解释。

这跟 m33 那次国家字段的教训是同一条：**能算 ≠ 该用**。

## 分页

`refs_of` / `citing_of` 增加 `offset`，响应带 `offset` 与 `total`；
前端论文页两列表各自独立翻页，按钮在首末页自动禁用。

实测：

```
/work/159/citations?limit=2&offset=0  → 2 条 / 共 22
/work/159/citations?limit=2&offset=2  → 2 条（不同内容）
/work/159                              → pagerank 0.0009791，分位 99.61%
```

## 测试

`tests/test_rank.py`（7 项）只测数学性质，不测具体数值：

- 分数归一（sum≈1）；
- 被引用者分数高于引用者；
- 链式 1←2←3 严格递减；
- 悬空节点不泄漏权重；
- 自环被忽略；
- 空图 / 单节点不炸；
- 边里出现未知节点时忽略而不抛异常。

全量 **109 项**通过。

## 遗留

1. **向前两跳没做**：只支持被引方向（谁引用它）；"它引用的论文又引用了谁"需要改 `chain` 的遍历方向，
   改动到一半发现函数体与签名不匹配，直接回退了（不做半成品）；
2. **PageRank 没做时间衰减**：老论文天然占优；
3. **没有做图上的社区检测**（论文聚类），只有人-人合作社区。
