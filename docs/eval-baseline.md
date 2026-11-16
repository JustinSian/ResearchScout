# ResearchScout 评测基线

- 日期：2026-11-16
- 模型：DeepSeek `deepseek-flash`（https://api.deepseek.com）
- 数据：`data/snapshot/demo.db`（60 位学者 / 1200 篇论文 / 101 条合作边）
- 目的：在改动前记录一份可复现的基线，后续优化只和这份比

---

## 1. 单测

```bash
python -m pytest tests -q
```

| 指标 | 结果 |
|---|---|
| 通过 | **482 passed** |
| 耗时 | 14.88s |
| 失败 | 0 |

> README 写的是 375 项，实际已增长到 482；文档待更新。

---

## 2. Agent 级评测（14 条）

```bash
ST_DB=data/snapshot/demo.db python -m app.eval_agent
```

| 指标 | 结果 |
|---|---|
| 通过率 | **14 / 14（100%）** |
| 工具命中 | 100% |
| 记忆使用 | 21% |
| 平均轮数 | 1.14 |
| 单次成本 | 约 **$0.0026**（总计 $0.0367） |
| p50 / p95 | 8.4s / 20.7s |

**稳定性说明**：首次运行时 `a-refuse-2`（“谁研究 medieval icelandic poetry”）因模型用常识作答判失败，通过率 13/14；修复记忆说明后复跑 14/14。该 case 属已知边界，README 也记录过“历史 3 次有 1 次失败”，后续应单独盯。

**慢 case**：`a-cite` 约 25s、`a-multi` 约 21s，主要花在补全与多步检索。

---

## 3. 记忆评测（50 条）

```bash
ST_DB=data/snapshot/demo.db python -m app.eval_memory
```

| 能力 | 通过率 |
|---|---|
| abstain（拒答） | **100%**（10/10） |
| extraction（抽取） | **100%**（10/10） |
| multi_session（跨会话） | **100%**（10/10） |
| temporal（时间推理） | **100%**（10/10） |
| update（知识更新） | **100%**（10/10） |
| **总计** | **50/50（100%）** |

- 过期事实误用：**0%**
- 记忆进检索：76%
- p95 延迟：38.5ms

### 从 19/50 到 50/50 的原因

首次运行只有 19/50（38%），定位到**产品逻辑**而非记忆能力：

1. demo 库只有 60 人，评测里的“阿尔茨海默病 / 肠道菌群”等方向查不到专家；
2. 查不到 → agent 走拒答；
3. 记忆说明原先只在**非拒答**时追加（`app/supervisor.py`：`if hint and not merged.get("refused")`）；
4. 评测要求 `note=true` → 拒答路径必然失败。

修复：拒答时也附长期记忆说明（`if hint:`）。重跑后 50/50，证明抽取、时间推理、更新都正常，之前的低分是“拒答把记忆说明吞了”。

> 这也说明：**评测数字低时，先分清是能力问题还是展示/数据问题。**

---

## 4. 本次发现的问题（按优先级）

| # | 问题 | 状态 |
|---|---|---|
| 1 | 拒答时不附记忆说明，评测误判 | ✅ 已修（`app/supervisor.py`） |
| 2 | README 测试数 375 过期（实际 482） | 文档待更新 |
| 3 | `a-refuse-2` 边界拒答不稳定 | 已知，后续单独盯 |
| 4 | demo 库只有 60 人，部分主题覆盖不足 | 数据方案待定（W2） |

---

## 5. 下一步（W2）

1. 把 `a-refuse-2` 的拒答判定做稳（提示词或判定规则），目标 3 次运行都 14/14。
2. 决定数据方案：
   - A. 用 `scripts/` 重建全量库（需要 OpenAlex key，耗时长）；
   - B. 建“评测专用小库”，只包含 case 涉及的主题；
   - C. 继续用 demo，接受部分主题覆盖不足。
3. 把 Agent 评测扩到 20–30 条，再谈检索优化。

---

## 6. 复现命令

```bash
cd E:\yolov7\researchscout
python -m pytest tests -q

$env:ST_DB="data/snapshot/demo.db"
python -m app.eval_agent
python -m app.eval_memory
```
