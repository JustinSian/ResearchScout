# 部署说明

ResearchScout 默认是**单机演示模式**；公开部署前请看「公网模式」一节。

## 一键启动

```bash
git clone https://github.com/JustinSian/ResearchScout.git
cd ResearchScout
bash scripts/setup.sh
# 编辑 .env，至少填 LLM_API_KEY
ST_DB=data/snapshot/demo.db .venv/bin/uvicorn app.api:app --host 0.0.0.0 --port 8020
```

打开 <http://127.0.0.1:8020>。

## 环境变量（`.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_API_KEY` | 空 | 必填，模型 API key（任意 OpenAI 兼容端点） |
| `LLM_BASE_URL` | `https://api.deepseek.com` | 端点 |
| `LLM_MODEL` | `deepseek-flash` | 模型名（需支持 function calling） |
| `LLM_PRICE_INPUT/OUTPUT` | 0.30 / 1.20 | 美元/1M tokens，成本核算用 |
| `LLM_JSON_MODE` | `json_object` | `text` 供不支持 response_format 的提供商 |
| `ST_DB` | `data/scholar.db` | 主库路径；演示用 `data/snapshot/demo.db` |
| `ST_RERANK_DIR` | 见代码 | 交叉编码器目录（可选，缺失时退回词法） |
| `ST_API_KEY` | 空 | 写接口 + 读鉴权（见下）的 key |
| `ST_READ_AUTH` | `0` | `1` = 读接口也要 `X-API-Key` |
| `ST_CHAT_BURST` / `ST_CHAT_RPM` | 10 / 30 | `/chat` 每-IP 令牌桶限流 |
| `ST_OPENALEX_KEY` | 空 | 库自动扩充用（可选） |
| `ANYSEARCH_API_KEY` | 空 | 联网检索用（可选） |

## 安全模型

| 操作 | 鉴权 |
|---|---|
| 读接口 | 默认公开；`ST_READ_AUTH=1` 时要求 `X-API-Key: ST_API_KEY` |
| 写接口（复核发布、库扩充、模型配置） | 始终要求 `X-API-Key: ST_API_KEY` |
| `/chat`（烧 LLM 钱） | 每-IP 限流（`ST_CHAT_BURST`/`ST_CHAT_RPM`），429 |

**公网模式**（推荐）：

```bash
export ST_READ_AUTH=1
export ST_API_KEY=<强随机串>
```

前端首次访问会提示输入 key（存 localStorage，之后自动带）。

## 数据

- 仓库自带 `data/snapshot/demo.db`（约 6.9 MB，60 位专家/1200 篇论文的演示子集）；
- 完整库（约 4.7 万学者/1.1 万论文）需用采集脚本从 OpenAlex 构建（数小时，受 API 限速），
  见 `app/ingest_works.py`、`app/build_dossiers.py`；
- 任何旧库首次打开会自动迁移到当前 schema（`connect()` 惰性升级）。

## 已知边界（上线前须知）

1. **单机 SQLite**：单进程 + WAL，适合小规模/内网；几十并发写会排队；
2. **Agent 延迟**：单轮 p50 约 7–8s（工具循环多次 LLM 调用）；
3. **记忆/经验**：长期记忆按 `uid` 隔离（由调用方传入）；共享经验记忆是全局的，无隐私边界；
4. **重排模型**：不随仓库分发（体积大），缺失时降级为词法匹配，`/status` 会标注；
5. **成本**：每轮 Agent 约 $0.002–0.03（DeepSeek 计价），限流是防刷底线。

## 反向代理 / HTTPS

生产建议前面挂 Nginx/Caddy 做 TLS。应用本身不处理 HTTPS；`X-Forwarded-*` 未做限流 IP 解析，
若走代理，限流会看到代理 IP（可用 `ST_TRUST_PROXY` 场景再细化）。
