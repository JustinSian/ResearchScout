"""重算 endorse 标记：区分"靠自己达标"和"靠网络背书才达标"。

问题：上一版只要用过 `coauthor_of_verified` 这条证据就打 endorse='network'，
于是钟南山网络里 3,542 人全部被标成网络背书——但其中很多人本身就有
几十篇论文和机构，没有网络背书也能过。

正确口径：把 `coauthor_of_verified` 去掉再判一次：
- 仍然 accept  → endorse=NULL（独立核验）
- 否则 accept  → endorse='network'（只因网络背书才进发布集）

全程离线（读 evidence 里的特征），不消耗 API 额度。
"""
from __future__ import annotations

import json

from app.db import connect, init_db
from app.judge import judge


def main() -> None:
    init_db()
    conn = connect()
    rows = conn.execute("SELECT pid, name, evidence, endorse FROM people").fetchall()
    net = own = 0
    for r in rows:
        try:
            ev = json.loads(r["evidence"] or "{}")
            feats = ev.get("features") or {}
        except Exception:
            continue
        if not feats:
            continue
        f = dict(feats)
        has_signal = bool(f.pop("coauthor_of_verified", False))
        if not has_signal:
            # 从没用过网络背书的人，清掉可能残留的标记
            if r["endorse"]:
                conn.execute("UPDATE people SET endorse=NULL WHERE pid=?", (r["pid"],))
            continue
        alone = judge({**f, "cite_p50": f.get("cite_p50", 5504),
                       "cite_p90": f.get("cite_p90", 18368)})
        status_alone = "accept" if alone["verdict"] == "accept" else alone["verdict"]
        if status_alone == "accept":
            conn.execute("UPDATE people SET endorse=NULL WHERE pid=?", (r["pid"],))
            own += 1
        else:
            conn.execute("UPDATE people SET endorse='network' WHERE pid=?", (r["pid"],))
            net += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM people WHERE status='published'").fetchone()[0]
    n_net = conn.execute("SELECT COUNT(*) FROM people WHERE status='published' AND endorse='network'").fetchone()[0]
    conn.close()
    print(f"  靠自己达标 {own} 人 / 仅靠网络 {net} 人")
    print(f"  已发布 {total}：独立 {total-n_net} / 网络背书 {n_net}")


if __name__ == "__main__":
    main()
