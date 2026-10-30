"""门槛扫描：GATE_ACCEPT 取不同值时，"够格专家"的精度/召回如何变化。

正样本定义（可复现、不依赖主观标注）：样本内 works_count >= 3 的人。
  precision = 发布集中的正样本 / 发布集
  recall    = 发布集中的正样本 / 全部正样本
用 people.evidence 里存的特征重算分数（不改库），扫描阈值。
"""
from __future__ import annotations

import json
import math
import random

from app.db import connect
from app.judge import judge


def bootstrap_ci(values: list[float], n: int = 1000, seed: int = 11) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        s = [values[rng.randrange(len(values))] for _ in range(len(values))]
        means.append(sum(s) / len(s))
    means.sort()
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def main() -> None:
    conn = connect()
    rows = conn.execute("SELECT pid, evidence FROM people").fetchall()
    feats = []
    for r in rows:
        try:
            ev = json.loads(r["evidence"] or "{}")
        except Exception:
            continue
        f = ev.get("features")
        if f:
            feats.append(f)
    print(f"可重算的样本: {len(feats)}")
    positives = [f for f in feats if int(f.get("sample_works") or 0) >= 3]
    print(f"正样本（works>=3）: {len(positives)}")

    print("\nGATE_ACCEPT | 发布数 | precision | recall | 说明")
    for gate in (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85):
        published = []
        for f in feats:
            r = judge({**f, "cite_p50": f.get("cite_p50", 0), "cite_p90": f.get("cite_p90", 0),
                       "gate_accept": gate})
            # judge 内部用常量阈值；这里用返回值与自定义阈值比较
            if r["score"] >= gate:
                published.append(f)
        pub_pos = sum(1 for f in published if int(f.get("sample_works") or 0) >= 3)
        precision = pub_pos / max(len(published), 1)
        recall = pub_pos / max(len(positives), 1)
        print(f"   {gate:.2f}     | {len(published):>6} | {precision:>8.1%} | {recall:>6.1%} |")

    # 附带：官方配置（0.80）的混淆矩阵
    gate = 0.80
    published = [f for f in feats
                 if judge({**f, "cite_p50": f.get("cite_p50", 0),
                           "cite_p90": f.get("cite_p90", 0)})["score"] >= gate]
    pub_pos = sum(1 for f in published if int(f.get("sample_works") or 0) >= 3)
    print(f"\n当前配置 GATE_ACCEPT={gate}: 发布 {len(published)}，其中专家 {pub_pos} "
          f"({pub_pos/max(len(published),1):.1%})，专家召回 {pub_pos/max(len(positives),1):.1%}")
    # bootstrap CI on precision（按发布集重采样）
    flags = [1.0 if int(f.get("sample_works") or 0) >= 3 else 0.0 for f in published]
    lo, hi = bootstrap_ci(flags)
    print(f"precision 95% CI: [{lo:.3f}, {hi:.3f}]")
    conn.close()


if __name__ == "__main__":
    main()
