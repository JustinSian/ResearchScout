"""配置：环境变量覆盖，默认指向项目内 data 目录。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def load_env(path: Path | None = None) -> None:
    """把 .env 读进 os.environ（已存在的环境变量优先）。

    为什么手写：项目不依赖 pydantic-settings，而 .env 是部署时最自然的配置方式。
    踩过的坑：写完 .env 却没加载 → ST_API_KEY 为空 → 写接口全部 503。
    """
    p = path or (ROOT / ".env")
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env()

DB_PATH = Path(os.environ.get("ST_DB", DATA_DIR / "scholar.db"))
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# 采集规模
INGEST_AUTHORS = int(os.environ.get("ST_AUTHORS", "500"))
# 采样口径：默认只取有 ORCID 的高置信学者；跑第二遍时换成宽松口径，用来喂可信门槛
INGEST_FILTER = os.environ.get("ST_FILTER", "has_orcid:true,works_count:>20")
INGEST_SORT = os.environ.get("ST_SORT", "cited_by_count:desc")
WORKS_PER_AUTHOR = int(os.environ.get("ST_WORKS", "50"))

# 可信门槛（0~1）：accept / review / reject 三档
GATE_ACCEPT = float(os.environ.get("ST_GATE_ACCEPT", "0.80"))
GATE_REVIEW = float(os.environ.get("ST_GATE_REVIEW", "0.50"))

# 检索
# 拒答门槛：cross-encoder 对"查询 × 候选论文"的最高分低于它就不作答。
# 依据见 M4 报告：乱编主题 CE 最高 0.425，真实方向 ≥0.858
CE_REFUSE = float(os.environ.get("ST_CE_REFUSE", "0.60"))

RRF_K = int(os.environ.get("ST_RRF_K", "60"))
CANDIDATES = int(os.environ.get("ST_CANDIDATES", "100"))
