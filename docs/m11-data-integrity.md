# M11：合作图谱帮我抓到一次真实的数据错位（附一次我自己的误判）

## 1. 怎么发现的

用户让我"自己看看钟南山的合作网络"。检查合作者名单时，两个人明显不对劲：

```
Graeme Henkelman  476 篇 / 8.5 万被引   ← 计算化学
Hannes Jónsson    425 篇 / 8.5 万被引   ← 计算化学
```

追到他名下 2000 年那篇论文：

| | 标题 | 作者数 | 作者示例 |
|---|---|---|---|
| 本地库（修复前） | Improved tangent estimate in the nudged elastic band method | **37** | Wei-jie Guan、Wenhua Liang、Lanjuan Li（新冠/呼吸） |
| OpenAlex | 同一篇 | **2** | Graeme Henkelman、Hannes Jónsson |

**标题是 2000 年的化学论文，作者却是 2020 年新冠论文的作者**——确实错位了。

## 2. 根因

`app/ingest_works.py`：

```python
cur = conn.execute("INSERT INTO works(...) ON CONFLICT(openalex_id) DO UPDATE SET cites=...")
if cur.lastrowid:
    wid = int(cur.lastrowid)     # ← 错在这里
```

SQLite 的 UPSERT 走"更新"分支时**不刷新 `lastrowid`**，返回的是连接上一条 INSERT 的 rowid——
循环里上一条往往就是 `people`。于是 `wid` 拿到了某个 `pid`，作者列表被写到错误的论文上。
第一次试跑已插入 1 页（200 篇），全量重跑时这 200 篇命中冲突，之后就一路错位。

## 3. 修复

1. 代码：不再用 `lastrowid`，一律 `SELECT wid FROM works WHERE openalex_id=?` 回查；
2. 数据：`app/rebuild_links.py` 重新拉 40 页（8000 篇），清空并重建
   `work_authors`（48,935 行）、共著边（88,611 条），并按论文概念重算每个人的
   `works_count / cited_by_count / topics`，最后重跑可信判定（published 4,817）。

## 4. 我中途的一次误判（重要）

修完后我用了一个自创指标验收："**两篇论文共享完全相同的作者集合 = 有问题**"，
得到"修复后仍有 863 篇"，于是以为没修好。

查下去发现这个指标是错的：

```
11 篇共享同一作者集合 → 全是 Albert Bandura 的单作者论文
 9 篇共享同一作者集合 → 全是 Mark Newman 的单作者论文
 8 篇共享同一作者集合 → 全是 Michael Porter 的单作者论文
```

**同一个人的多篇单作者论文，本来就会共享作者集合。** 这个指纹在"单作者论文多"的数据集上会大量误报。

真正有效的验收方式是**逐篇与 OpenAlex 对照**：

```
随机抽 12 篇，比对"本地作者集合"与"OpenAlex 作者集合"的交集
→ 12/12 一致（其中 1 篇双方都是 0 作者，是我的判定写错，不是数据错）
```

## 5. 修复后的钟南山

| 项目 | 修复前 | 修复后 |
|---|---|---|
| 名下论文 | 10 篇 | **8 篇**（1346、6850 根本不是他的） |
| 合作者 | 111 人（含 2 位化学家） | **75 人** |
| 其中已发布 | 84 | **61** |
| 共同论文权重 | 多为 1~3 | 多为 1 |

网络里现在全是呼吸/重症/传染病领域的人：

```
Kwok-Yung Yuen（1,650 篇 / 15.9 万被引）
Lanjuan Li   （1,177 篇 / 10.2 万被引）   ← 李兰娟
David S.C. Hui、Jianxing He、Bin Du、Wenhua Liang、Wei-jie Guan …
```

图谱：61 节点（共 76）、129 条边、11 个机构/平台。

## 6. 遗留

1. **权重几乎都是 1**：库里只有他 8 篇论文，看不出"谁合作更密"。要解决得补更多他的论文（OpenAlex 按 author id 拉全量）。
2. **192 篇论文没有作者**：这些在 OpenAlex 侧本身就没有 `authorships`（书、报告、声明等），不是本地问题。
3. **7 篇导入论文 `openalex_id` 为空**（某学术图谱来源），靠 DOI 关联，`works` 表里因此有 7 行 NULL。
4. 之前基于错误关联得到的结论（代表作列表、works_count、可信判定分布）都已随重建更新；
   回归基线已重录：`tool_acc 0.958 / refuse_acc 1.0 / nDCG bm25 0.2354`。

## 7. 教训

1. **"接口 200 + 节点不为 0" 不能当验收**。我上一轮就是这么验收图谱的。
2. **验收指标本身要先被验证**。我自创的"共享作者集合"指标在单作者数据上误报率极高；
   而"逐篇对照上游数据"虽然贵，但结论可靠。
3. **SQLite UPSERT 后不要信 `lastrowid`**。
4. **把关系画出来、按异常维度排序，是抓数据错误最快的办法**——
   我正是因为"化学家出现在呼吸专家网络里"才顺着查下去的。
