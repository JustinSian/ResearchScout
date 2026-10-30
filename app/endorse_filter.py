"""网络背书的领域校验：只放行"和种子在同一领域"的人。

问题（M15 抽样）：238 位网络背书里有 7.5% 是"C 类"——企业或完全无关领域
（食品科学、环境监测），他们只是恰好与种子共著 ≥2 篇。

做法：
1. 种子的"领域签名"= 与它有 ≥2 篇共同论文的合作者里，出现频率最高的 30 个主题；
   （不直接用种子自己的 topics，因为外部导入的中文主题和 OpenAlex 英文概念对不上）
2. 候选人的主题与签名有交集 → 保留；
3. 没有交集 → 降级为 pending（不是 reject，留给人工复核），并记录原因。

用法：
  python -m app.endorse_filter          # 只看会动多少人
  python -m app.endorse_filter --apply  # 真正执行
"""
from __future__ import annotations

import argparse
import collections
import json

from app.db import connect, init_db


def _global_prevalence(conn) -> dict[str, float]:
    """每个主题在全体发布专家中的出现率，用于剔除"Biology/Chemistry"这类通用词。"""
    rows = conn.execute(
        "SELECT topics FROM people WHERE status='published'").fetchall()
    counter: collections.Counter[str] = collections.Counter()
    total = 0
    for r in rows:
        total += 1
        for t in json.loads(r["topics"] or "[]"):
            counter[t] += 1
    return {t: c / max(total, 1) for t, c in counter.items()}


def _seed_signature(conn, seed: int, top: int = 30,
                    prevalence: dict[str, float] | None = None) -> set[str]:
    """种子的"领域签名"：与它共同论文 ≥2 篇的合作者里高频、且**有区分度**的主题。

    第一版直接用 top-30，结果签名里全是 Biology / Chemistry / Computer science，
    238 人全部"重叠"，过滤器形同虚设。所以加两条约束：
      - 在合作者中出现 ≥3 次（不是偶然标签）
      - 全局出现率 < 15%（不是通用词）
    """
    rows = conn.execute(
        """SELECT p.topics FROM edges e
           JOIN people p ON p.pid = CASE WHEN e.a=? THEN e.b ELSE e.a END
           WHERE (e.a=? OR e.b=?) AND e.kind='coauthor' AND e.weight >= 2""",
        (seed, seed, seed)).fetchall()
    counter: collections.Counter[str] = collections.Counter()
    for r in rows:
        for t in json.loads(r["topics"] or "[]"):
            counter[t] += 1
    prev = prevalence if prevalence is not None else _global_prevalence(conn)
    sig = [t for t, n in counter.most_common() if n >= 3 and prev.get(t, 0) < 0.15]
    return set(sig[:top])


def run(apply: bool = False, log=print) -> dict:
    init_db()
    conn = connect()
    rows = conn.execute(
        """SELECT pid, name, topics, evidence FROM people
           WHERE status='published' AND endorse='network'""").fetchall()
    by_seed: dict[int, list] = collections.defaultdict(list)
    for r in rows:
        try:
            src = (json.loads(r["evidence"] or "{}").get("source") or "")
        except Exception:
            src = ""
        seed = int(src.split(":")[-1]) if "network_of:" in src else 0
        by_seed[seed].append(r)

    prev = _global_prevalence(conn)
    demote, keep = [], []
    for seed, people in by_seed.items():
        if not seed:
            log(f"  种子未知 {len(people)} 人，跳过")
            continue
        sig = _seed_signature(conn, seed, prevalence=prev)
        log(f"  种子 {seed} 签名 {len(sig)} 个主题: {sorted(sig)[:8]}")
        for r in people:
            topics = set(json.loads(r["topics"] or "[]"))
            if topics & sig:
                keep.append(r)
            else:
                demote.append((r, seed, sorted(topics)[:4]))

    log(f"  保留 {len(keep)} / 降级 {len(demote)}")
    for r, seed, tp in demote[:12]:
        log(f"    - {r['name'][:26]:<28} 主题 {tp}")

    if apply and demote:
        for r, seed, _tp in demote:
            ev = json.loads(r["evidence"] or "{}")
            ev["demoted"] = {"reason": "no_domain_overlap", "seed": seed}
            conn.execute(
                """UPDATE people SET status='pending', endorse=NULL, evidence=?,
                       confidence=MIN(confidence, 0.5) WHERE pid=?""",
                (json.dumps(ev, ensure_ascii=False), r["pid"]))
        conn.commit()
        log(f"  已降级 {len(demote)} 人（状态改为 pending，等人工复核）")
    conn.close()
    return {"keep": len(keep), "demote": len(demote), "applied": bool(apply)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    print(run(a.apply, log=lambda s: print(s, flush=True)))
