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
