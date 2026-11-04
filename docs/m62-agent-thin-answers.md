# M62：修"专家详情为空"——根因是工具结果被截断，不是缺档案

## 现象

问"钟南山的研究方向"，Agent 回答"只有一条基础记录，没有研究方向字段，无法介绍"。
一度怀疑是深档案（dossier）没建（dossier 表只有 102 行 vs 47,654 人）。

## 真正的根因（两处，主次分明）

### 主因：工具结果回传时被白名单截断

`agent_llm.py` 把工具结果序列化喂给模型时，只保留
`pid/wid/name/name_zh/title/year/cites/inst` 这几个字段：

```python
"items": [{k: v for k, v in it.items()
           if k in ("pid", "wid", "name", "name_zh", "title", "year", "cites", "inst")} ...]
```

于是 `research_area` 的 `recent`/`early`/`top_venues`、`profile` 的 `topics`/`country`/`h_index`
在到达模型之前就被扔掉了——模型拿到的只有"姓名 + 被引"，当然说"没有研究方向字段"。

修复：改为 `_RESULT_FIELDS` 白名单，保留 `recent/early/top_venues/topics/topics_zh/country/h_index/works/…`。

### 次因：深档案只覆盖 102 人

`research_area` 原实现完全依赖 dossier；没有档案就返回空。加了**本地兜底** `_local_area`：
用 `work_authors→works` 现算 h-index、活跃年份、期刊、近五年/早期标题词频，**覆盖全部 47,654 人**（不联网）。

（顺带：`top_venues` 会混入 "PubMed/CrossRef" 假期刊，兜底里加了噪声过滤。）

## 修复后实测

| 问题 | 修复前 | 修复后 |
|---|---|---|
| 钟南山的研究方向 | "没有研究方向字段" | 呼吸系统疾病；近年 asthma/lung/COPD/airway/COVID；h=83、938 篇、1981–2026 |
| 介绍 Yoshua Bengio | 详情空 | 深度学习奠基者（并诚实标注"库内仅 9 篇样本、h=9 受样本量限制"） |
| 钟南山的合作者 | 空/不足 | 冉丕鑫 / 关伟杰 / 杨子峰 / 赖克方 等（含机构与作品数） |

## 回归

- 常规 14 条：14/14
- 难例 30 条（3 次投票）：29/30（`h-memory-override` 单测 3/3，eval 抖动非回归）
- 全量单测通过

## 教训

**"模型说没有 X 字段"先查它到底收到了什么**——问题往往在序列化那一层，而不是数据层。
