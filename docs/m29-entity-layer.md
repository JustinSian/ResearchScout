# M29：实体层补强（期刊 venue + 机构归一）

对标 AMiner 的 `venue/*` 与 `organization/na` 之后，发现我们这两层是空的：
期刊只是 `works.venue` 的一段文本，机构只是 `people.last_inst` 的自由字符串。
这两层不补，"审稿人匹配"就少了两个关键信号（是否常发目标期刊、是否有同机构冲突）。

## 一、期刊实体化

| 项 | 结果 |
|---|---|
| 期刊实体 | **2,314 个**（期刊 2,308 / 仓储 6） |
| 论文挂载 | **10,599 / 11,104（95%）** |
| 归一化 | 小写 + 去标点 + 压缩空白；同 key 合并，展示名取高频写法 |
| 仓储识别 | `PubMed`(231)、`PubMed Central`(37)、`Figshare`、`Research Square` 等单列 `kind=repository`，不参与期刊统计 |

顺带修了一个数据质量问题：**`PubMed` 原本被当成期刊排在第三位**（231 篇），
它其实是索引库，不该出现在"高频期刊"里。

## 二、机构归一

| 项 | 结果 |
|---|---|
| 机构实体 | **4,319 个**（从 4,320 个 `last_inst` 字符串收敛而来） |
| 挂载专家 | **38,663 人** |
| 类型分布 | 医院 863 / 大学 1,382 / 研究所 576 / 企业 23 / 未知 1,475 |

归一化规则：
- 展开缩写：`univ→university`、`hosp→hospital`、`lab→laboratory`、`med→medical` 等 20 条；
- 去掉 `of/the/and/for` 连接词——否则 `Hospital of Guangzhou Medical` 与
  `Hospital, Guangzhou Medical` 会分成两家；
- 大小写、标点、空白统一。

**自动合并只发生在归一化后完全一致的机构名**，这是刻意的安全边界。

## 三、近似重复：只报告，不合并

`near_duplicates()` 用 token Jaccard 找候选对，标注三种关系：

| 关系 | 含义 |
|---|---|
| `parent_child` | 一家是另一家的上级（如 University of Washington ↔ 其 Medical Center） |
| `similar` | 名称相近但不确定 |
| `same_tokens_confirm` | 词集合相同但归一化后不同 → **多半是同名不同机构，必须人工确认** |

实测前 6 条候选：

```
1.0  [same_tokens_confirm] University of York        ‖  York University
1.0  [same_tokens_confirm] University of Miami       ‖  Miami University
1.0  [same_tokens_confirm] The University of Adelaide ‖  Adelaide University
1.0  [same_tokens_confirm] Zhejiang Sci-Tech University ‖ Zhejiang University of Science and Technology
```

前三条是**同名不同机构**（英国约克 vs 加拿大约克、迈阿密大学 vs 迈阿密大学(俄亥俄)），
第四条才是真正的同一所学校的两种译法。**机器分不出来，所以只报告。**

## 四、踩坑记录（同一个问题错了两次）

近似重复第一版：把 `university/hospital` 这类通用词过滤掉再算 Jaccard。

```
"Guangzhou Medical University" vs "Guangzhou University"
→ 都只剩 {guangzhou} → Jaccard = 1.0  ❌
```

第二版：过滤长度 <3 的词。

```
"Wuxi No.2 People's Hospital" vs "Wuxi People's Hospital"
→ no/2 被丢掉 → 词集合相同 → Jaccard = 1.0  ❌
```

**结论：机构名的区分信息非常可能就藏在短词里（编号、A&F、No.2），
比较时一个词都不能少。** 现在 `_tokens` 保留全部词，阈值提到 0.85。

## 五、接进系统

| 位置 | 变化 |
|---|---|
| `GET /venues`、`GET /venue/{vid}` | 期刊列表与详情（含高频作者、高被引论文） |
| `GET /institutions`、`GET /institutions/duplicates` | 机构列表与待确认候选对 |
| `GET /people/{pid}/affiliation` | 某人机构 + 常发期刊 |
| analyst 子 Agent | 输出新增 `institution` 与 `journals`（审稿人匹配的两个信号） |
| `works.venue_id` / `people.inst_id` | 已回填，后续统计全部走 join |

实测（钟南山）：

```
机构: 广州医科大学附属第一医院 / 国家呼吸医学中心 [hospital]
期刊: Journal of Thoracic Disease 42 / CHEST Journal 35 / European Respiratory Journal 24
```

## 六、遗留

1. **同机构冲突检测还没接进审稿人匹配**：现在只是把机构带进分析结果，
   还没做"候选人与作者是否同机构"的判定（需要先解析稿件作者的机构）；
2. **机构层级缺失**：医院 ↔ 大学是上下级关系，现在只有平铺实体，
   `parent_child` 候选对没有落库；
3. **未知类型 1,475 个**：多是拼写不规范或非英中机构名，没有继续清洗。
