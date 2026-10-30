"""向量编码（bge-small-en-v1.5，CLS pooling + L2 归一化）。

为什么不用 sentence-transformers：环境里只有 transformers + torch，
bge 的用法就两行（CLS + normalize），自己写反而少一个依赖、也便于控制 batch 与线程。
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parents[1] / "data" / "models" / "bge-small-en"
_LOCK = threading.Lock()
_STATE: dict = {}


def _load():
    with _LOCK:
        if _STATE:
            return _STATE["tok"], _STATE["model"]
        import torch
        from transformers import AutoModel, AutoTokenizer

        torch.set_num_threads(4)
        tok = AutoTokenizer.from_pretrained(str(MODEL_DIR))
        model = AutoModel.from_pretrained(str(MODEL_DIR)).eval()
        _STATE.update(tok=tok, model=model)
        return tok, model


def encode(texts: list[str], batch_size: int = 64, max_length: int = 256) -> np.ndarray:
    """返回 L2 归一化后的 float32 矩阵 (n, 384)。"""
    import torch

    tok, model = _load()
    out: list[np.ndarray] = []
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            enc = tok(batch, padding=True, truncation=True,
                      max_length=max_length, return_tensors="pt")
            hidden = model(**enc).last_hidden_state[:, 0]      # CLS
            hidden = torch.nn.functional.normalize(hidden, p=2, dim=1)
            out.append(hidden.cpu().numpy().astype(np.float32))
    if not out:
        return np.zeros((0, 384), dtype=np.float32)
    return np.vstack(out)


def encode_one(text: str) -> np.ndarray:
    return encode([text])[0]


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
