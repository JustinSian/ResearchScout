# M40：向前两跳 + PageRank 时间衰减

## 一、向前两跳（上轮回退的那个）

上一轮改到一半发现函数体与签名不匹配，直接回退了。这次重写：

```
GET /work/{wid}/chain?hops=2&direction=cited_by|refs
```

| direction | 含义 | 形状 |
|---|---|---|
| `cited_by`（默认） | 谁引用了它 | X ← A ← B |
| `refs` | 它引用了谁 | X → A → B |

`build_paths()` 只认"被引方向"（dst←src）；向前模式在调用前把边反转，
复用同一套遍历，避免写两份容易分叉的 BFS。

实测（wid=159，即那篇 88 次库内被引的 COVID 论文）：

```
direction=cited_by → 节点 40，路径 8 条，每条长度 3
direction=refs     → 节点 21，路径 8 条，每条长度 3
```

向前方向节点更少，是因为它引用的 22 篇里只有部分在本库、且它们引用的论文大多在库外——
这正是 5.3% 链回率的直接体现。

## 二、PageRank 时间衰减

纯 PageRank 有一个已知偏差：**老论文被引历史长，天然占优**。
这里用"偏好随机跳转"（personalized PageRank）修正：跳转概率按半衰期 8 年偏向新论文。

```
weights[w] = 0.5 ^ ((newest - year[w]) / 8)
```

结果写进 `work_rank.score_recent`，与原始 `score` 并列：

| 口径 | 第 1 | 第 2 | 第 3 |
|---|---|---|---|
| `score`（原版） | 1964 · Multidimensional Scaling（0.00286） | 2003 · Identification of a Novel Coronavirus | 2020 · A Novel Coronavirus from Patients |
| `score_recent`（衰减） | **2020 · A Novel Coronavirus from Patients（0.00557）** | 2003 · Identification of a Novel Coronavirus | 1994 · CLUSTAL W |

衰减后前三名全部是 1994 年之后的论文，2020 年那篇直接翻倍——符合"近作更相关"的直觉。

`/citations/top?sort=pagerank|pagerank_recent|count` 三选一。

## 三、实现细节

- `pagerank()` 增加 `weights` 参数：权重即"随机跳转"分布，缺省均匀；
- 权重为 0 的节点不会被清零（用 `1e-9` 兜底），否则归一化会失败；
- 年份缺失的论文按"12 年前"处理（比中位数更保守，避免误抬）；
- `work_rank` 加列用 `ALTER TABLE` 迁移，老库直接兼容。

## 测试

`tests/test_rank.py` 增到 9 项，新增：

- 加权后目标节点分数上升（`weights[2]=5` 时 2 号分数 > 基线）；
- 加权后仍归一（sum≈1），且权重为 0 的节点分数 > 0。

全量 **111 项**通过。

## 遗留

1. 半衰期 8 年是我拍的，**没有用真实数据调过**——要调的话应该用"近 5 年被引占比"做目标；
2. 时间衰减只作用于 PageRank，不影响 `count` 口径；
3. 向前两跳没有接进对话（只有 API），因为问法边界太模糊（"它引用的论文又引用了谁"容易歧义）。
