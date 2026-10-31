# M37：`topic_high` / `topic_low`——把三层权重的控制权交出去

## 背景

M35 把"主题覆盖度"做成了内部排序依据（自动推断）。但 AMiner 的 `paper/qa/search`
允许调用方**手工指定**三层关键词：high 必含、middle 加分、low 可选。

这一轮把 high/low 开放成 API 参数，语义与 AMiner 对齐。

## 语义

| 参数 | 含义 | 写法 |
|---|---|---|
| `topic_high` | **必须命中**；组间 AND、组内 OR | JSON `[["a","b"],["c"]]` = `(a OR b) AND c`；或逗号 `a,b` = `a OR b` |
| `topic_low` | **只加分不拦人** | 逗号分隔 |

```
GET /search?q=lung cancer&topic_high=[["respiratory","pulmonary"]]&topic_low=immunotherapy
```

## 匹配来源：概念 ∪ 论文标题

第一版只看 `people.topics`，结果 high 过滤直接 0 条——**因为 OpenAlex 的概念太粗**：
肺癌方向的临床医生，概念常常写成 "Lung cancer" 而不是 "respiratory"。

所以匹配改成两个来源取并集：

1. `people_fts`（name / topics / inst）；
2. 该人的论文标题（`work_authors × works.title LIKE`）。

`topic_low` 的加分也是两者相加。

## 参数与排序的优先级

| 场景 | 行为 |
|---|---|
| `sort=relevance`（默认） | `low_hits` 参与排序（多命中者靠前） |
| `sort=cites` / `works` | **显式排序优先**，low 只作为返回字段 `low_hits` 展示 |
| `kind=works` | 忽略 high/low，返回 `note` 说明 |
| `topic_high` 解析为空 | `HTTP 400`，不静默忽略 |
| 两个参数都不传 | 与旧行为完全一致（回归底线） |

## 实测

```
基线（q=lung cancer，无 high/low）      5 条 | Minhua Peng / Jolanda Nikolla / Hyuna Sung
high=[["respiratory","pulmonary"]]      5 条 | Minhua Peng / Yi-Long Wu / Ming Ouyang（high 生效）
low=respiratory,immunotherapy           low_hits = [3, 2, 2, 2, 1]（加分生效）
topic_high=[[\"a\"                         HTTP 400
kind=works + high                        note: 只对 kind=people 生效
```

## 测试

`tests/test_topic_tiers.py`（7 项）：嵌套 JSON、扁平列表、逗号单组、非法 JSON 不抛异常、
AND/OR 语义、引号转义、空输入不产生表达式。

全量 **96 项**通过。

## 遗留

1. `topic_high` 只在 `kind=people` 上生效；works 侧需要类似机制（当前用 `q`）；
2. 标题匹配是 `LIKE`，没有走分词——"respiratory" 不会命中 "respiratory failure" 之外的变体
   （英文形态变化少，暂时可接受）；
3. 没有做"high 命中数"的阈值控制（AMiner 的 middle 层），只有 AND/OR 二值语义。
