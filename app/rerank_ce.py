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


def scores(query: str, passages: list[str]) -> list[float]:
    """返回每个 passage 与 query 的相关性（sigmoid 后的 0~1）。"""
    if not passages:
        return []
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
