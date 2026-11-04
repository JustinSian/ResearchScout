"""Cross-encoder 打分（复用医药项目已下载的 bge-reranker ONNX，int8）。

为什么需要：M4 的拒答一度失效——乱编主题（quantum gravity phenology）也能返回专家。
实测两个便宜信号都分不开好坏查询：
  dense_max  GOOD 0.667~0.795 / BAD 0.594~0.735  ← 重叠
  bm25_top   GOOD 6.31~12.67 / BAD 0~9.81        ← 重叠
这与医药项目的结论一致：只有 cross-encoder 能判"这条资料到底答不答得上这个问题"。
"""
from __future__ import annotations

import os
from pathlib import Path

MODEL_DIR = Path(os.environ.get(
    "ST_RERANK_DIR",
    str(Path.home() / "med-compliance-agent/data/models/bge-reranker-onnx")))

_STATE: dict = {}
_AVAILABLE: bool | None = None   # None = 未探测


def _load():
    if _STATE:
        return _STATE["sess"], _STATE["tok"]
    import onnxruntime as ort
    from transformers import AutoTokenizer

    onnx_path = MODEL_DIR / "model_int8.onnx"
    if not onnx_path.exists():
        onnx_path = MODEL_DIR / "model.onnx"
    if not onnx_path.exists():
        raise FileNotFoundError(f"reranker 模型不存在: {MODEL_DIR}")
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    sess = ort.InferenceSession(str(onnx_path), so, providers=["CPUExecutionProvider"])
    tok = AutoTokenizer.from_pretrained(str(MODEL_DIR))
    _STATE.update(sess=sess, tok=tok)
    return sess, tok


def available() -> bool:
    """交叉编码器是否可用（模型存在且能加载）。惰性探测并缓存。

    模型**可选**：缺失时 `scores()` 退回词法重叠打分，产品仍能跑，
    只是"库里到底有没有"的判定精度下降（m4 里这是最强的拒答信号）。
    """
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    try:
        _load()
        _AVAILABLE = True
    except Exception:
        _AVAILABLE = False
    return _AVAILABLE


def _lexical_scores(query: str, passages: list[str]) -> list[float]:
    """无模型时的兜底：query 与 passage 的实词重叠度（0~1）。"""
    import re
    q = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}", (query or "").lower()))
    if not q:
        return [0.0] * len(passages)
    out = []
    for p in passages:
        toks = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}", (p or "").lower()))
        out.append(len(q & toks) / len(q))
    return out


def scores(query: str, passages: list[str]) -> list[float]:
    """返回每个 passage 与 query 的相关性（sigmoid 后的 0~1）。

    模型缺失时退回词法重叠（无硬依赖，clone 即跑）。
    """
    if not passages:
        return []
    if not available():
        return _lexical_scores(query, passages)
    import numpy as np

    sess, tok = _load()
    enc = tok([query] * len(passages), passages, padding=True, truncation=True,
              max_length=512, return_tensors="np")
    feed = {k: v.astype(np.int64) for k, v in enc.items()
            if k in ("input_ids", "attention_mask", "token_type_ids")}
    logits = sess.run(None, feed)[0].reshape(-1)
    return [float(1.0 / (1.0 + np.exp(-x))) for x in logits]


def max_score(query: str, passages: list[str]) -> float:
    s = scores(query, passages)
    return max(s) if s else 0.0
