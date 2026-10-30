"""测量：能否用"查询与候选的最大相似度"区分'有答案'和'没答案'。

好查询（库里确实有人研究）与坏查询（编造主题 / 无意义串）各若干条，
分别看 BM25 分数与稠密相似度的分布，找可分阈值。
"""
from __future__ import annotations

from app.embed import encode_one
from app.search import _load_matrix, search_dense, search_people

GOOD = ["graph neural network", "transformer", "dementia", "cancer immunotherapy",
        "reinforcement learning", "protein structure"]
BAD = ["quantum gravity phenology", "medieval icelandic poetry", "zzz qwerty wibble",
       "underwater basket weaving", "phlogiston thermodynamics"]


def stats(q: str) -> dict:
    lex = search_people(q, limit=20)
    den = search_dense(q, kind="people", limit=20)
    ids, mat = _load_matrix("people")
    qv = encode_one(q).astype("float32")
    import numpy as np
    sims = mat @ qv
    vocab_hit = 0
    if den:
        vocab_hit = 1
    return {"q": q,
            "bm25_top": round(lex[0][1], 2) if lex else 0.0,
            "bm25_n": len(lex),
            "dense_top": round(max(sims), 4),
            "dense_n": len(den)}


def main() -> None:
    print(f"{'query':<32}{'bm25_top':>10}{'bm25_n':>8}{'dense_max':>11}{'dense_n':>9}")
    for label, qs in (("GOOD", GOOD), ("BAD", BAD)):
        print(f"-- {label} --")
        for q in qs:
            s = stats(q)
            print(f"{s['q'][:30]:<32}{s['bm25_top']:>10}{s['bm25_n']:>8}"
                  f"{s['dense_top']:>11}{s['dense_n']:>9}")


if __name__ == "__main__":
    main()
