# M3 报告：可信门槛的实测精度

## 结论先行

人工抽样 60 条（accept / review / reject 各 20）后发现一个**反直觉的事实**：

> 三档 60 条**全部是真实人名**，没有一条是机构/会议名。

原因是采集走 `authorships`（论文作者列表），源头本来就是人；
而"机构被当成作者"的问题只出现在另一种采集路径（`/authors?search=`）里，我没有走那条路。

所以"person vs non-person 的精度"这个指标在这里几乎**没有区分度**——
门槛真正在做的是**筛产出**，不是判"是不是人"。

## 1. 非人实体：库里极少，且全部被挡住

名字命中机构/会议词（university / institute / group / society…）的记录：**17 / 36,083**

```
The National Institute of Neurological Disorders…   rejected  score 0.00
The National Lung Screening Trial Research Team…    rejected  score 0.00
the ADVANCE Collaborative Group                     rejected  score 0.00
ESC Scientific Document Group                       rejected  score 0.00
The ALLHAT Officers and Coordinators for the…       rejected  score 0.00
```

**17/17 = 100% 被挡在发布集之外**（n 很小，只说明"该抓的抓到了"，不能声称总体精度）。

## 2. 门槛实际在区分"产出"

人工抽样的构成：

| 档位 | 样本数 | works<2 占比 |
|---|---|---|
| accept | 20 | **0%** |
| review | 20 | 95% |
| reject | 20 | 95% |

即：accept 全是多篇作者，review/reject 绝大多数是"只出现过一次"的作者。
**门槛的效果 ≈ 一个产出过滤器**，而不是身份分类器。

## 3. 专家筛选的精度 / 召回（可复现定义，不依赖主观标注）

正样本定义：样本内 `works_count >= N`。

| 定义 | 总数 | 发布数 | 召回 |
|---|---|---|---|
| works>=2 | 7,137 | 4,631 | **64.9%** |
| works>=3 | 2,705 | 2,203 | **81.4%** |
| works>=5 | 752 | 716 | **95.2%** |

发布集纯度：**4,631/4,631 = 100% 满足 works>=2**（这是策略保证的，不是测出来的）。

## 4. 阈值扫描（用 evidence 里存的特征重算，不改库）

| GATE_ACCEPT | 发布数 | precision(works>=3) | recall(works>=3) |
|---|---|---|---|
| 0.55 | 22,570 | 11.8% | 98.3% |
| 0.65 | 20,116 | 12.5% | 92.6% |
| 0.70 | 10,388 | 23.9% | 91.9% |
| 0.75 | 10,165 | 22.3% | 83.8% |
| **0.80（当前）** | 4,814 | **45.8%** | **81.4%** |
| 0.85 | 4,754 | 45.1% | 79.2% |

precision 的 95% bootstrap CI（0.80 档）: **[0.443, 0.471]**

## 5. 一条被数据推翻的设计假设

原本以为"必须有人工复核才能保证质量"，但实测发现：**待审队列 18,909 条里只有约 2.5k 是 works>=2**，
其余 87% 是单篇作者——让复核者按分数从高到低看，会把时间全花在"只露过一次脸"的人身上。

因此把队列排序改成 **先按"是否够格（works>=2）"，再按分数**：
复核产能直接对准能进库的候选。

## 6. 落地改动

| 改动 | 说明 |
|---|---|
| **策略：单篇作者不自动发布** | `published` 必须同时满足 accept + works>=2；单篇留在 pending |
| 复核队列 API | `GET /review/queue`、`POST /review/decide`（带 decided_by / note 留痕） |
| 队列排序 | 按"够格优先"重排（上面的分析直接得出） |
| 阈值可配 + 可扫描 | `GATE_ACCEPT/GATE_REVIEW` + `app.gate_sweep` |

## 7. 现状

```
people 36,083 | published 4,631（全部 works>=2）| pending 18,909 | rejected 12,543
非人实体 17 条，全部 rejected
```

## 8. 遗留

- 人工抽样只有 60 条、非人实体只有 17 条，**CI 会很宽**；要下"总体精度 98%"的结论需要更大标注集。
- 目前没有"同名不同人"（重名合并）检测：同一姓名不同 ORCID 可能被当成一个人。
  这是学术图谱最难的问题之一，M3 未覆盖，应写进限制。

---

## 附录：专家墙上线后发现的一个漏网实体（2026-10-29）

做完前端专家墙，按"合作者最多"排序时**第 1 名是 `Authors Task Force Members`**——
一条集体署名（9 篇 / 5.1 万被引）通过了可信门槛，而 60 条人工抽样里没有抽到它。

这暴露了 M3 抽样法的局限：**抽样能估比例，抓不到长尾里的具体错误**。
一个按"合作者数"排序的界面，比随机抽样更容易把这类记录顶到最前面。

处置：
- `NON_PERSON` 词表补入 `task force / authors / members / investigators / panel /
  writing / collaborat / trial / initiative / consensus`；
- 加两个单测（集体署名必拒、真实学者必过）；
- 重判后 published 4,633 → 4,630，专家墙前 5 名恢复为真实学者
  （Volkmar Falk、Eric Lander、Eugene Braunwald…）。

**教训**：数据质量不能只靠抽样验收，还要有"按不同维度排序看一眼"的界面——
排序会放大长尾错误。