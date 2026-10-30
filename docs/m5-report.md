# M5 报告：安全 / 压测 / 公网 / 简历条目

## 1. 安全加固（审计发现 1 个高危）

| 问题 | 严重度 | 处置 |
|---|---|---|
| `/review/decide` **无鉴权写接口**：任何人可把候选发布进知识库（投毒） | 高 | 加 `X-API-Key`；**未配置密钥时返回 503（fail-closed）**，不是放行 |
| `/docs`、`/openapi.json`、`/redoc` 公开 | 中 | 默认关闭（`ST_DOCS_ENABLED=1` 才开） |
| `.env` 写完没被读取 → 密钥为空 → 写接口全 503 | 中（工程 bug） | config 加轻量 dotenv 加载 |

验证：

```
无 key  → 401
错 key  → 401
对 key  → 200
/docs /openapi.json /redoc → 404
异常输入（含 <script>）→ 无堆栈泄漏
```

## 2. 压测（本机直连，单 worker，避开隧道）

| 路径 | 并发 | 吞吐 | p50 | p95 |
|---|---|---|---|---|
| `/search` | 1 | 14.4 rps | 56ms | 133ms |
| `/search` | 4 | **20.2 rps** | 178ms | 352ms |
| `/search` | 8 | 18.6 rps | 413ms | 697ms |
| `/search` | 16 | 11.6 rps | 1377ms | 1789ms |
| `/chat`（图查询） | 1 | 146 rps | 6ms | 9ms |
| `/chat` | 4 | **170 rps** | 22ms | 33ms |
| `/chat` | 16 | 77.8 rps | 197ms | 368ms |

观察：
- 检索在 **并发 4 附近饱和（~20 rps）**，之后 p95 线性恶化 → 单 SQLite 连接的读放大；
- `/chat` 在"合作关系"这类图查询上非常快（6ms）：它不走 CE 打分；
  真正贵的是 `find_expert`（CE 打分 10 篇论文，约 0.3~0.5s/次）。
- 资源：uvicorn RSS 759MB（含 bge-small + reranker int8 常驻），DB 133MB。

## 3. 公网

```
https://<public-url>
  /health 200   /stats 200   /search 200   /chat 200
  POST /review/decide（无 key）401
```

## 4. 与 README 的对应

| 能力 | 端点 |
|---|---|
| 检索（bm25/dense/hybrid + rrf/dbsf） | `GET /search` |
| 专家列表/画像/合作图谱 | `/people`、`/people/{pid}`、`/graph/{pid}` |
| 论文检索 | `/works` |
| 复核队列（可信采集闭环） | `GET /review/queue`、`POST /review/decide`（需 key） |
| Agent（5 工具 + 引用 + 拒答） | `POST /chat`、`GET /chat/stream`（SSE） |

## 5. 遗留

- 单 worker + SQLite：并发上限 ~20 rps，若要更高需要 WAL 多读连接或换 PG（与医药项目同一结论）。
- 没有鉴权的"读"接口仍是公开的（演示站有意为之），生产需要接入租户隔离。
- `find_expert` 的 CE 打分是延迟主要来源，可用缓存（同一 query 重复请求）优化。
