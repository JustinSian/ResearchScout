"""回填 work_authors.position（作者位次）——从 OpenAlex 的 authorships 重新导入。

背景：主库导入时没有写位次，导致 `position` 覆盖率只有 1%（845/82,977），
"一作/通讯"这类问题无法回答，R_core 指标也失效。

做法：
  1. 只取 `position IS NULL` 的工作（可断点续跑）；
  2. 用 `filter=openalex_id:W1|W2|...` 批量拉取（每批 50 篇，约 240 个请求）；
  3. 按 authorships 顺序写回 position（0 = 第一作者）；
  4. 作者不在 people 表里的跳过（常见于被截断的合作者列表）。

用法：
  python -m app.backfill_positions --batch 50          # 全量
  python -m app.backfill_positions --limit 2000        # 试跑
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from app.db import connect
from app.openalex import OpenAlex

ROOT = Path(__file__).resolve().parents[1]
_OA = OpenAlex()


def short_id(url: str) -> str:
    return (url or "").rsplit("/", 1)[-1]


def pending_works(limit: int | None = None) -> list[tuple[int, str]]:
    """返回 (wid, openalex_id) —— 已有 position 的跳过。"""
    conn = connect()
    try:
        sql = """SELECT w.wid, w.openalex_id FROM works w
                 WHERE w.openalex_id IS NOT NULL AND EXISTS (
                     SELECT 1 FROM work_authors wa
                     WHERE wa.wid = w.wid AND wa.position IS NULL)"""
        args: tuple = ()
        if limit:
            sql += " LIMIT ?"
            args = (int(limit),)
        return [(int(r["wid"]), r["openalex_id"]) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def fetch(batch: list[tuple[int, str]]) -> dict[str, list[dict]]:
    ids = "|".join(short_id(oid) for _wid, oid in batch)
    data = _OA._get("/works", {"filter": f"openalex_id:{ids}", "per_page": 50,
                               "select": "id,authorships"})
    return {short_id(w.get("id")): (w.get("authorships") or [])
            for w in (data.get("results") or [])}


def run(batch_size: int = 50, limit: int | None = None, log=print) -> dict:
    works = pending_works(limit)
    log(f"  待回填 {len(works)} 篇")
    conn = connect()
    updated = missing = 0
    t0 = time.perf_counter()
    try:
        for i in range(0, len(works), batch_size):
            chunk = works[i:i + batch_size]
            try:
                authorships = fetch(chunk)
            except Exception as exc:
                log(f"  批次 {i//batch_size} 失败：{exc}")
                continue
            for wid, oid in chunk:
                aus = authorships.get(short_id(oid)) or []
                if not aus:
                    missing += 1
                    continue
                for pos, au in enumerate(aus):
                    aid = ((au.get("author") or {}).get("id") or "")
                    if not aid:
                        continue
                    # people 表存短 ID（A...），authorships 给完整 URL，两种都要试
                    row = conn.execute(
                        "SELECT pid FROM people WHERE openalex_id IN (?, ?)",
                        (aid, short_id(aid))).fetchone()
                    if not row:
                        continue
                    conn.execute("UPDATE work_authors SET position=? WHERE wid=? AND pid=?",
                                 (pos, wid, int(row["pid"])))
                    updated += 1
            conn.commit()
            if (i // batch_size) % 20 == 0:
                log(f"  进度 {i + len(chunk)}/{len(works)} · 已写 {updated} 条 · "
                    f"{time.perf_counter() - t0:.0f}s")
    finally:
        conn.close()
    log(f"  完成：写入 {updated} 条位次，{missing} 篇无 authorships，"
        f"耗时 {time.perf_counter() - t0:.0f}s")
    return {"works": len(works), "updated": updated, "missing": missing}


def coverage() -> dict:
    conn = connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM work_authors").fetchone()[0]
        with_pos = conn.execute(
            "SELECT COUNT(*) FROM work_authors WHERE position IS NOT NULL").fetchone()[0]
        return {"total": int(total), "with_position": int(with_pos),
                "ratio": round(with_pos / total, 4) if total else 0.0}
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    print("  回填前覆盖率:", coverage())
    run(a.batch, a.limit or None)
    print("  回填后覆盖率:", coverage())


if __name__ == "__main__":
    main()
