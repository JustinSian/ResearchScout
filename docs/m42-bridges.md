# M42：桥接检测（谁把两个圈子粘在一起）

## 问题

m41 发现社区 8（307 篇）是**混合**的：GLOBOCAN（癌症统计）和 BWA（序列比对）被放在同一堆。
当时只能"人肉解释"，没有机制。

这一轮补上机制：找社区里的**割点**（articulation point）——去掉它，社区会断成几块。

## 实现

`app/cluster.py`：

```python
articulation_points(nodes, edges) -> set[int]   # Tarjan，迭代 DFS（防深链爆栈）
components_after_removal(nodes, edges, removed) -> [sizes]  # 降序
bridges(cluster_id, limit) -> [{wid, title, parts, separated, largest_share}]
bridge_status(wid) -> {is_bridge, parts, ...}
```

排序规则：先看"断成几块"（parts），再看"有多少节点被隔离出主块"（separated）。

只在该社区的**诱导子图**上算边，不跨社区。

## 结果：机制印证了猜测

社区 8（癌症统计 + 生信）的桥接论文：

| 论文 | 去掉后 | 说明 |
|---|---|---|
| Global cancer statistics 2018 (GLOBOCAN) | 3 块 / 隔离 2 篇 | 癌症统计综述 |
| featureCounts | 2 块 / 隔离 1 篇 | RNA-seq 计数工具 |
| Ultrafast and memory-efficient alignment (BWA) | 2 块 / 隔离 1 篇 | 序列比对工具 |
| A Simple Sequentially Rejective Multiple Test (Bonferroni) | 2 块 / 隔离 1 篇 | 统计校正方法 |

**四个桥全是方法/统计论文**，正好解释了"为什么癌症统计会和生信混在一起"：
它们是被这些通用方法论文连起来的，而不是同一主题。

其他社区也检出合理的桥：

| 社区 | 桥 |
|---|---|
| 2（密度泛函） | The nature of the chemical bond—1992 · Revealing Noncovalent Interactions · Vortices in high-temperature superconductors |
| 24（管理学） | Explicating dynamic capabilities · Theory Building From Cases · A Dynamic Theory of Organizational Knowledge Creation |

## 界面

论文页新增标记（仅当该文是桥）：

```
研究社区 #8（共 307 篇互相引用的论文）
[社区桥接点] 去掉它，这个社区会断成 3 块
```

## 口径与局限

- **割点只抓"单点断开"**：如果两个子圈子由**两条**路径连接（如 A-B 和 C-D），就检测不到；
- 隔离规模很小（1–2 篇）：这说明这些社区内部其实很紧密，桥接只挂住几条细枝，
  不是"两大阵营被强行拼接"——这点和直觉不同，值得记下来；
- 没有做**边介数**（edge betweenness），那才能找到"两条路径连接"的情形。

## 测试

`tests/test_bridges.py`（9 项）：

- 路径图的中间两点是割点
- 三角形没有割点
- 两个团 + 一条桥 → 恰好桥两端是割点
- 星形图的中心是割点
- 断开的两团不产生额外割点
- 去掉桥点后连通块 `[3, 2]`
- 去掉非割点后仍是单一连通块
- 空图 / 单点
- 确定性

全量 **127 项**通过。

## 遗留

1. 双路径桥接（多连边）检测不到 → 需要边介数；
2. `bridges()` 对每个社区都重算全图诱导边，社区多时慢（当前 3,406 个社区，单次调用只算一个，可接受）；
3. 桥接论文的"跨领域程度"没有量化（只知道断成几块，不知道两块主题差异多大）。
