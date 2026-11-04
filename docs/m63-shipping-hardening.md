# M63：可上架硬化（对外发布前的一批护栏）

## 目标

让 GitHub clone 出来的人能一条命令跑起来，且公开部署时不被刷爆账单、CI 是绿的。

## 本轮改动

| 改动 | 文件 | 解决 |
|---|---|---|
| 交叉编码器可选降级 | `rerank_ce.py` | 模型缺失时 `scores()` 退回词法重叠，`available()` 暴露状态；不再有硬依赖 |
| 一键启动 | `scripts/setup.sh` | 建 venv/装依赖/生成 .env/检查演示库与模型 |
| 聚合状态 | `GET /status` | LLM / 重排 / 数据库 / 配置来源，一眼定位 |
| **每-IP 限流** | `api.py` `/chat` 三端点 | 令牌桶（默认 10 burst / 30 rpm），429 防账单被刷爆 |
| **惰性建表/迁移** | `db.py` | `connect()` 首次打开自动 `migrate`+建表，旧快照自动升级 |
| **CI 修复** | `.github/workflows/ci.yml` | 装 langgraph/numpy 等、`ST_DB=演示快照`、全量测试对快照跑 → 徽章变绿 |

## 限流

```
ST_CHAT_BURST  默认 10（突发容量）
ST_CHAT_RPM    默认 30（每分钟补充）
```

- 只挂在 `/chat` 三个端点（真正烧 LLM 钱的地方）；
- 每-IP 令牌桶，进程内，适合单机/小规模部署；
- 超限返回 429；`_chat_gate(None)` 容错（直接调用不崩）。

## 惰性迁移顺序（踩过的坑）

`connect()` 里必须先 `migrate()`（补缺失列）再 `executescript()`（建表/索引）：
- 反过来时，旧表缺列会让 `CREATE INDEX ... ON people(name)` 直接炸；
- `migrate()` 本身也要判断表是否存在（fresh DB 无表时 `PRAGMA` 返回空集，
  不能对不存在的表 ALTER）。

## 验证

- 本地 `ST_DB=data/snapshot/demo.db` 下 300 项全过、`compileall` 通过；
- 服务器实测：`/status` 返回 LLM/重排/数据库状态；`/chat` 介绍钟南山、研究方向均正常；
- 限流单测：突发容量耗尽后第 N+1 次 429、不同 IP 互不影响。
