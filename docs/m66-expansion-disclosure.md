# M66：补库公示板 + 姓名精确匹配

## 用户报的两个现象

1. 问「CRISPR base editing」→ 说没专家（其实已采入 Komor/Packer）；
2. 紧接着问「现在有吗」→ 仍说没有，并称"查到的人与要找的人不是同一人"。

**不是时间间隔问题**：24h 锁只阻止"重复采集"，不影响"已有数据能不能被检索"。

## 根因一：profile 按姓名命中同名异人

`_resolve_person(name)` 走 `search_people`（FTS 分词 OR），查 `Alexis C. Komor`
会命中 `Alexis J. Battle`（只共享 "Alexis"），查 `Michael S. Packer` 命中
`Milton Packer`（心脏病学）。Agent 一核验就发现"不是同一人"，于是判定证据不成立。

**修复**：先 `lower(name)=lower(?) OR name_zh=?` 精确匹配，命中就用；未命中才退回 FTS。

实测：重问同一主题 → `refused=False`，明确给出
「Alexis C. Komor（pid 47656，UCSD），碱基编辑奠基性论文第一作者」。

## 根因二：补库结果没有告诉用户

用户看不到"到底补没补、补了什么"，只能从回答里猜。

**修复**：新增**补库公示板**。每次触发补库后，在回答末尾附一句，并通过
SSE `expansion` 事件把结构化结果发给前端，渲染成独立面板：

| 情况 | 公示文案 |
|---|---|
| 补成功 | 库里原来没有「X」的专家，已从 OpenAlex 补充 N 位学者、M 篇论文并入库（并已用新数据重试） |
| 24h 内已补过 | 「X」在 24 小时内已补充过，本次未重复采集 |
| 尝试了但 0 入库 | 已尝试为「X」补充，但没有找到可靠的匹配（标题未命中），未入库 |
| 补库失败 | 库里没有「X」的专家；尝试补充失败：<原因> |
| 未开启 | 未执行：自动扩充已关闭 |

## 实测

```
问「circadian proteomics」
→ expansion_report: {attempted: true, ok: true, added_people: 0, added_works: 0}
→ 回答末尾：【库内补充】已尝试为「circadian proteomics」补充，
   但没有找到可靠的匹配（标题未命中），未入库。
```

## 已知取舍

- 补库用"标题命中"做验证（≥2 篇/关键词），**宁可少收不误收**；
  因此冷门主题（如 circadian proteomics）可能 0 入库——公示板会如实说，而不是假装补到了。


---

## 补充修复：中文主题清洗 + 翻译（用户报"建筑"补库失败）

### 发现的三个 bug

1. **主题被抽错**：`facets.parse("我想了解人工智能领域的专家")` → `"我想了解 工智能"`
   （丢了"人"）、`"我想认识建筑领域的专家"` → `"我想认识建筑"`。
   改成**从原句用 `clean_topic` 清洗**（它按前后缀剥"，比 facets 的中文切词可靠）。
2. **中文没翻译**：OpenAlex 是英文库，搜"建筑"必然 0 命中。
   新增 `ZH2EN`（50+ 常见学科）+ `to_english()`；检索与补库都用英文词。
3. **`_topic_experts` 不翻译**：中文概念查询直接进 FTS，永远 0 命中。
   现在进检索前先 `clean_topic → to_english`。

### 顺带修

- `remember` 工具：模型给 `direction`/`研究方向` 等非枚举 key 时映射到合法 key，不再报错；
- 人名/关系类问题（合作者/论文/介绍/导师…）不触发补库，避免拿整句去搜 OpenAlex。

### 实测

| 查询 | 清洗后主题 | 英文检索词 | 结果 |
|---|---|---|---|
| 我想了解人工智能领域的专家 | 人工智能 | artificial intelligence | 直接答出 Hinton 等 5 位 |
| 我想认识建筑领域的专家 | 建筑 | architecture | 仍 0 入库（见下） |

### "建筑"为什么还是补不进

`architecture` 在 OpenAlex 里高度多义：高被引的是**计算机体系结构**、
**认知架构**（John R. Anderson 的 cognitive architecture 甚至会让主题一致性判断误判为"相关"）。
真正的建筑学论文标题很少重复 "architecture" 两次以上，标题验证（≥2 篇）因此全拒。

**这是精度优先的取舍**：宁可 0 入库并如实公示，也不把计算机体系结构的人当建筑专家。

---

## 追加：用 OpenAlex 概念消歧，解决"建筑"这类多义主题补不进

### 根因链

1. 中文"建筑"→英文 `architecture`，OpenAlex 全文搜索把高被引的**计算机体系结构**排在前面；
2. 标题验证（≥2 篇命中）对建筑学论文太严（标题很少重复 "architecture" 两次）→ 0 命中；
3. 即便补进来，概念路径的作者被标成 `pending`（旧 `_validated` 没把 `concept` 当验证）→ 检索不到。

### 修复

| 改动 | 效果 |
|---|---|
| `CONCEPT_MAP` + `_authors_by_concept` | 中文主题 → OpenAlex 概念 id，用 `filter=concepts.id` 取**该领域**的论文（概念即字段级金标，不再靠标题猜） |
| `_validated` 纳入 `"concept"` | 概念路径采进来的人直接 `published`，可被检索 |
| 影子索引结果**任何主题都合并**（去掉"仅多词"门槛） | 单词概念（architecture）扩进来也能被 `find_expert` 前置 |
| 严格 0 命中时降级到"标题命中 ≥1"（标 `weak`） | 冷门主题不再一无所获，低置信但入库 |

### 实测（服务器）

```
问「我想认识建筑领域的专家」
→ 主题清洗 建筑 → architectural design → 概念 C190831278 (Building design)
→ 补充 8 位学者 / 74 篇论文并入库
→ 答出：Alexander Hollberg（Chalmers，BIM/LCA/可持续设计）、
        Tianzhen Hong（LBNL，建筑能耗）、…（refused=False）
```
