# M38：两跳引用链

## 一句话

问"两跳引用：X"不再只给你"谁引用了 X"，而是给出 **X ← A ← B** 的链路——
B 引用了 A，A 引用了 X，全部是库内边，可逐条复核。

## 实现

`app/citations.py`：

```python
def chain(wid, hops=2, max_nodes=40) -> {nodes, edges, paths, truncated}
def build_paths(start, edges, hops, max_paths=10)  # 纯函数，单独可测
```

- 从目标论文出发，沿**被引方向**做 BFS（`work_refs.ref_wid IN (frontier)`）；
- 只走 `ref_wid` 非空的边——库外引用没有落库，链自然停在那一层；
- 硬上限：`hops ≤ 2`、`nodes ≤ 40`，超出置 `truncated=True`（不静默截断）；
- `paths` 用节点 id 列表表示，渲染时再换成标题。

## 对话入口

`plan_node` 里对引用意图再分一层：

```
"引用了谁"        → hops=1（谁引用了它 / 它引用了谁）
"两跳引用" / "间接" / "谁影响了" → hops=2
```

回答会多出一段：

```
- 两跳链（仅库内，A ← B 表示 B 引用了 A）：
  1.《Clinical Characteristics of Coronavirus Disease 2019 in China》
    ←《Neurologic Manifestations of Hospitalized Patients...》
    ←《...》
  （节点超过上限，只展示前 40 篇）
```

## 实测

以库内被引最高的论文为起点：

```
节点: 40   边: 199   截断: True
路径数: 8  长度分布: [3]     ← 全部是两跳
```

也就是说：**这条链是真的走通了**，不是"看起来有两跳"。

## 坑

`_cite_query()` 只剥了"引用/被引/论文"，没剥"两跳"。
于是 `两跳引用：<标题>` 被拼成 `两跳 <标题>` 去 `LIKE` 匹配 → 找不到 → 拒答 → 转网络检索。
修法是把"两跳/二跳/多跳/间接/传播/影响"一起剥掉。

**教训**：关键词路由加新词时，清洗函数必须同步；否则"路由对了、参数错了"，
而且失败会静默降级成网络检索，看起来像"库里没数据"。

## 测试

`tests/test_chain.py`（6 项）：单跳、两跳、hops 截断、无边、成环不死循环、路径上限。

## 遗留

1. 只做"被引方向"，没有"引用了谁的两跳"（向前两跳）；
2. `truncated` 只报告"节点超限"，没有分页参数——大图需要 `offset`；
3. 路径按 BFS 顺序取前 8 条，**不是**按引用强度/年份排序。
