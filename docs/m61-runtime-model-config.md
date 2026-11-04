# M61：运行时模型配置（换模型不用重启）

## 目标

让用户**在界面上**填 Base URL / 模型 / Key，点一下就能换模型，不需要改 `.env`、不需要重启。

## 实现

| 层 | 内容 |
|---|---|
| 配置存储 | `data/model_config.json`（`data/*` 已在 .gitignore，不进仓库）；权限 0600 |
| 优先级 | **配置文件 > 环境变量 > 内置默认** |
| 生效方式 | 写 `os.environ`（`llm` 每次调用都重新读），**当前进程立即生效** |
| 脱敏 | `GET /model/status` 永不返回明文 key，只给 `has_key` + 末四位 |

### API

| 端点 | 鉴权 | 说明 |
|---|---|---|
| `GET /model/status` | 无（脱敏） | 当前 model / base_url / 是否配 key / json 模式 / 单价 / 来源（file\|env） |
| `POST /model/config` | `X-API-Key` | 校验后合并写入并热生效；空值表示清除该字段 |
| `POST /model/test` | `X-API-Key` | 真实调一次 `chat_text`，再探测 **function calling** 是否可用 |

### 前端

导航新增「模型设置」：Base URL / 模型 / Key / json 模式 +「测试连接」「保存配置」，
X-API-Key 复用 `localStorage.st_key`。

## 验证（服务器实测）

```
无 key 保存                  → 401
测试连接                    → {"ok":true,"reply":"pong","tool_calling":"callable","ms":783}
保存（等价 no-op）          → source: env → file
非法 URL                    → 400
首页含"模型设置"入口         → 1
Agent 仍可用                → ok=true
```

## 设计取舍

1. **不做模型自动探测**：`/model/test` 用一个极简 `ping` 工具探测；
   若返回未调用工具则标 `unknown`，明确报错（tool 相关 4xx）才标 `unsupported`。
2. **key 落盘明文**：与 `.env` 同等信任级别；文档已注明公网部署应改用密钥管理。
3. **配置优先级**：文件覆盖环境变量——这让"界面改"优先于"部署配置"，符合用户直觉；
   但需要注意：运维改了环境变量不会覆盖用户已有文件配置。
