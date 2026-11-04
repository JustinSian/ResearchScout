# M65：专家库自动扩充——打通"没查到→补库→下次能答"三步

## 用户要的三步

1. 告诉用户库内没有；
2. 去外部源补充该领域专家；
3. 补充后下次直接能答。

## 现状（本次打通）

| 步 | 实现 | 状态 |
|---|---|---|
| 1 告诉用户没有 | `_finish` 无证据守卫 + `_looks_refusal` | ✅ 默认开启 |
| 2 补充 | `expand_topic()`：OpenAlex 主题检索 → 标题验证 → 入库 + 影子索引 | ✅ 默认开启（`ST_AUTO_EXPAND=1`） |
| 3 下次能答 | 影子索引 `people_fts_exp` + 本次修的合并逻辑 | ✅ 本次修复 |

## 本次发现并修掉的 bug

`_exp_experts`（影子索引检索）**定义了但从没被调用**——之前重构时合并逻辑丢了。

后果：扩充进来的人（如 Alexis Komor / Michael Packer）虽然已 published + 已入影子索引，
但 `find_expert` 只查主索引；主索引被 FTS 宽松匹配塞满无关的人（Podsakoff/Barney 管理学），
于是"扩充成功却永远查不到"。

修复：`_topic_experts` 里，**多词主题**若影子索引有结果，把它们排到最前（去重后再接主结果）。

## 现场演示（服务器实测）

```
第 1 问「找研究 CRISPR base editing 的专家」
  → refused=True，明确说"库里没有该方向的人"，不编造
  → expansion: +2 人 / +24 论文（OpenAlex 采入 Alexis C. Komor、Michael S. Packer）

第 2 问（修完合并逻辑后）
  → refused=False
  → 列出 Alexis C. Komor（US，71 篇，15,337 被引）、Michael S. Packer
  → 并主动标注混入的无关记录（Podsakoff/Barney 是误召回，不要采信）
```

## 约束（照旧，写进 DEPLOY）

- 来源是 **OpenAlex**（学术 API），不是网页搜索；
- 同一主题 24h 只采一次（`recently_expanded` 防抖）；
- 扩充的人进**影子索引**，不污染主索引（之前 R@10 回归的根因，已隔离）；
- 新增作者默认 published（有来源标记 `auto_expand`，可 `audit_expansions` 回滚）。

## 已知瑕疵（非阻断）

画像核验时，`profile(name=...)` 按姓名取人，偶会命中同名异人（如 Alexis J. Battle vs
Alexis C. Komor）。Agent 会据实标注"核验失败"，但理想是 profile 优先按 pid。
