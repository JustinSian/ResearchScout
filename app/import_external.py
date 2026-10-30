"""把外部专家库（某学术图谱）的记录导入 ScholarTrust。

设计：**不绕过可信门槛**。外部记录走同一条链路：
  规范化 → 去重 → 打分（多了"外部已核验来源"这条证据）→ 发布 / 待审 → 留痕
这样"多源采集"和"可信采集"是同一套机制，而不是两套。
"""
from __future__ import annotations

import json
import time

from app.db import connect, init_db
from app.judge import judge

DDL = [
    "ALTER TABLE people ADD COLUMN source TEXT DEFAULT 'openalex'",
    "ALTER TABLE people ADD COLUMN source_ref TEXT",
    "ALTER TABLE people ADD COLUMN headline TEXT",
    "ALTER TABLE people ADD COLUMN name_zh TEXT",
]


def migrate(conn) -> None:
    """只补缺失的列。以前用 ddl.split()[-1] 取列名，拿到的是 'openalex'，
    导致每次导入都重复 ADD COLUMN → duplicate column name。"""
    import re

    cols = {r[1] for r in conn.execute("PRAGMA table_info(people)")}
    for ddl in DDL:
        m = re.search(r"ADD COLUMN (\w+)", ddl)
        col = m.group(1) if m else ""
        if col and col not in cols:
            conn.execute(ddl)
    conn.commit()


def normalize_hf(rec: dict) -> dict:
    """某学术图谱记录 → 我们的字段。字段缺失就留空，不猜。"""
    papers = rec.get("recent_papers") or []
    topics = list(rec.get("keywords") or [])
    for f in (rec.get("research_fields") or [])[:3]:
        topics.append(str(f).split("（")[0].split("、")[0][:20])
    seen, uniq = set(), []
    for t in topics:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return {
        # name 保持主库口径（英文名，便于与 OpenAlex 记录对齐）；
        # 中文名单独存 name_zh，供双语展示与中文搜索。
        "name": (rec.get("name_en") or rec.get("name") or "").strip(),
        "name_zh": (rec.get("name_zh") or rec.get("name") or "").strip(),
        "last_inst": rec.get("affiliation") or "",
        "headline": rec.get("position") or rec.get("title") or "",
        "topics": uniq[:10],
        "works_count": len(papers),
        "cited_by_count": int(rec.get("cites") or 0),
        "h_index": rec.get("h_index") or None,
        "source": "huafu",
        "source_ref": rec.get("id"),
        "external_verified": (rec.get("trust") == "verified"),
        "papers": [{"doi": p.get("doi") or "", "title": p.get("title") or "",
                    "year": p.get("year"), "venue": p.get("venue") or ""}
                   for p in papers if p.get("title")],
    }


def _dedup(conn, rec: dict) -> int | None:
    """跨源同一实体识别（顺序即优先级）。

    1. source_ref：同一条外部记录重复导入
    2. 英文名 + 机构：最可靠
    3. 英文名唯一命中：机构写法常有差异（"广州医科大学附属第一医院" vs 英文全称）
    4. 中文名：避免同一人重复入库
    """
    if rec.get("source_ref"):
        row = conn.execute("SELECT pid FROM people WHERE source_ref=?",
                           (rec["source_ref"],)).fetchone()
        if row:
            return int(row["pid"])
    row = conn.execute(
        "SELECT pid FROM people WHERE name=? AND IFNULL(last_inst,'')=? LIMIT 1",
        (rec["name"], rec["last_inst"] or "")).fetchone()
    if row:
        return int(row["pid"])
    rows = conn.execute(
        """SELECT pid FROM people WHERE name=?
           ORDER BY (status='published') DESC, cited_by_count DESC LIMIT 2""",
        (rec["name"],)).fetchall()
    if rows:
        # 同名多条时选"已发布 + 被引最高"的那条作为主记录，
        # 其余用 merge_people() 并进来（OpenAlex 自己就会把同一人拆成多个 author id）
        return int(rows[0]["pid"])
    if rec.get("name_zh"):
        row = conn.execute("SELECT pid FROM people WHERE name_zh=? LIMIT 1",
                           (rec["name_zh"],)).fetchone()
        if row:
            return int(row["pid"])
    return None


def merge_people(target: int, sources: list[int], log=print) -> dict:
    """把 sources 并进 target：搬论文/合作边，合并主题，删掉多余记录。

    这是跨源采集必须有的能力：OpenAlex 把同一人拆成多个 author id，
    某学术图谱又提供第三条记录，不合并的话专家墙会出现三个"钟南山"。
    """
    conn = connect()
    migrate(conn)
    trow = conn.execute("SELECT * FROM people WHERE pid=?", (target,)).fetchone()
    if not trow:
        raise KeyError(f"target {target} 不存在")
    topics = set(json.loads(trow["topics"] or "[]"))
    moved_papers = moved_edges = 0
    for pid in sources:
        if pid == target:
            continue
        srow = conn.execute("SELECT * FROM people WHERE pid=?", (pid,)).fetchone()
        if not srow:
            continue
        topics |= set(json.loads(srow["topics"] or "[]"))
        conn.execute("UPDATE OR IGNORE work_authors SET pid=? WHERE pid=?", (target, pid))
        conn.execute("DELETE FROM work_authors WHERE pid=?", (pid,))
        moved_papers += 1
        # 合作边：把 a/b 里的 source 换成 target，重复的取较大权重
        for r in conn.execute(
                "SELECT a,b,weight FROM edges WHERE a=? OR b=?", (pid, pid)):
            a = target if r["a"] == pid else r["a"]
            b = target if r["b"] == pid else r["b"]
            if a == b:
                continue
            old = conn.execute(
                "SELECT weight FROM edges WHERE a=? AND b=? AND kind='coauthor'",
                (a, b)).fetchone()
            if old:
                conn.execute("UPDATE edges SET weight=? WHERE a=? AND b=? AND kind='coauthor'",
                             (max(old["weight"] or 0, r["weight"] or 0), a, b))
            else:
                conn.execute("INSERT OR IGNORE INTO edges(a,b,kind,weight) "
                             "VALUES(?,?,'coauthor',?)", (a, b, r["weight"] or 1))
            conn.execute("DELETE FROM edges WHERE (a=? AND b=?) AND kind='coauthor'",
                         (r["a"], r["b"]))
            moved_edges += 1
        conn.execute("DELETE FROM people_fts WHERE rowid=?", (pid,))
        conn.execute("DELETE FROM people WHERE pid=?", (pid,))

    n_works = conn.execute("SELECT COUNT(*) FROM work_authors WHERE pid=?",
                           (target,)).fetchone()[0]
    conn.execute(
        """UPDATE people SET topics=?, works_count=MAX(works_count, ?),
               name_zh=COALESCE(name_zh, ?), updated_at=? WHERE pid=?""",
        (json.dumps(sorted(topics), ensure_ascii=False), n_works,
         trow["name_zh"], time.time(), target))
    nm = conn.execute("SELECT name,name_zh,topics,last_inst FROM people WHERE pid=?",
                      (target,)).fetchone()
    conn.execute("DELETE FROM people_fts WHERE rowid=?", (target,))
    conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                 (target, f'{nm["name"]} {nm["name_zh"] or ""}'.strip(),
                  " ".join(json.loads(nm["topics"] or "[]")), nm["last_inst"] or ""))
    conn.commit()
    conn.close()
    out = {"target": target, "merged": sources, "works": n_works,
           "topics": len(topics), "moved_edges": moved_edges}
    log(f"  合并 {sources} → {target}：{n_works} 篇论文，{len(topics)} 个主题")
    return out


def import_record(raw: dict, log=print) -> dict:
    init_db()
    conn = connect()
    migrate(conn)
    rec = normalize_hf(raw)

    feats = {
        "name": rec["name"], "orcid": None,
        "sample_works": rec["works_count"], "sample_cites": rec["cited_by_count"],
        "cite_p50": 5504, "cite_p90": 18368,      # 用主库的分位数做参照
        "has_inst": rec["last_inst"] or None,
        "topics": rec["topics"],
        "external_verified": rec["external_verified"],
        "headline": rec["headline"] or None,
    }
    judged = judge(feats)
    status = "published" if judged["verdict"] == "accept" else (
        "pending" if judged["verdict"] == "review" else "rejected")

    pid = _dedup(conn, rec)
    evidence = json.dumps({"features": feats, "reasons": judged["reasons"],
                           "source": "huafu", "raw_id": rec["source_ref"]},
                          ensure_ascii=False)
    if pid:
        conn.execute(
            """UPDATE people SET name_zh=?, last_inst=?, headline=?, topics=?,
                   works_count=MAX(works_count, ?), cited_by_count=MAX(cited_by_count, ?),
                   status=?, confidence=?, evidence=?, source='huafu', source_ref=?,
                   updated_at=? WHERE pid=?""",
            (rec["name_zh"], rec["last_inst"], rec["headline"],
             json.dumps(rec["topics"], ensure_ascii=False), rec["works_count"],
             rec["cited_by_count"], status, judged["score"], evidence,
             rec["source_ref"], time.time(), pid))
        action = "updated"
    else:
        # openalex_id 是 NOT NULL：外部记录用 ext: 前缀占位，保证可追溯
        ext_id = f"ext:huafu:{rec['source_ref'] or rec['name']}"
        cur = conn.execute(
            """INSERT INTO people(openalex_id,name,name_zh,last_inst,headline,topics,
                   works_count,cited_by_count,status,confidence,evidence,source,source_ref,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,'huafu',?,?)""",
            (ext_id, rec["name"], rec["name_zh"], rec["last_inst"], rec["headline"],
             json.dumps(rec["topics"], ensure_ascii=False), rec["works_count"],
             rec["cited_by_count"], status, judged["score"], evidence,
             rec["source_ref"], time.time()))
        pid = int(cur.lastrowid)
        action = "inserted"

    # 论文：按 DOI/标题去重后落库，并建立作者关系（这让画像页有内容）
    linked = 0
    for p in rec["papers"]:
        row = None
        if p["doi"]:
            row = conn.execute("SELECT wid FROM works WHERE doi=?", (p["doi"],)).fetchone()
        if not row and p["title"]:
            row = conn.execute("SELECT wid FROM works WHERE title=?", (p["title"],)).fetchone()
        if row:
            wid = int(row["wid"])
        else:
            cur = conn.execute(
                "INSERT INTO works(doi,title,year,cites,venue,type) VALUES(?,?,?,0,?,'article')",
                (p["doi"] or None, p["title"], p["year"], p["venue"] or None))
            wid = int(cur.lastrowid)
        conn.execute("INSERT OR IGNORE INTO work_authors(wid,pid) VALUES(?,?)", (wid, pid))
        linked += 1

    # FTS：只更新这一行，避免整表重建；名称列同时放中英文，中文提问也能命中
    conn.execute("DELETE FROM people_fts WHERE rowid=?", (pid,))
    conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                 (pid, f'{rec["name"]} {rec["name_zh"]}'.strip(),
                  " ".join(rec["topics"]), rec["last_inst"] or ""))

    conn.execute(
        """INSERT INTO candidates(kind,payload,evidence,score,verdict,reasons,created_at)
           VALUES('person',?,?,?,?,?,?)""",
        (json.dumps({"pid": pid, "name": rec["name"], "source": "huafu"},
                    ensure_ascii=False),
         json.dumps({"source": "huafu", "raw_id": rec["source_ref"]}),
         judged["score"], judged["verdict"],
         json.dumps(judged["reasons"], ensure_ascii=False), time.time()))
    if judged["verdict"] == "review":
        conn.execute("INSERT OR REPLACE INTO review_queue(cid,priority) "
                     "VALUES(last_insert_rowid(), ?)", (judged["score"],))
    conn.commit()
    conn.close()
    out = {"pid": pid, "action": action, "status": status, "score": judged["score"],
           "verdict": judged["verdict"], "papers_linked": linked,
           "reasons": [r["rule"] for r in judged["reasons"]]}
    log(f"  {action} pid={pid} {rec['name']} → {status}（{judged['score']:.2f}）"
        f" 论文 {linked} 篇")
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="规范化 JSON 文件")
    ap.add_argument("--merge-name", help="把同名记录合并到最佳候选（如 'Nanshan Zhong'）")
    a = ap.parse_args()

    if a.merge_name:
        conn = connect()
        rows = conn.execute(
            """SELECT pid FROM people WHERE name=?
               ORDER BY (status='published') DESC, cited_by_count DESC""",
            (a.merge_name,)).fetchall()
        pids = [int(r["pid"]) for r in rows]
        conn.close()
        if len(pids) < 2:
            print(f"  {a.merge_name}: 只有 {len(pids)} 条，无需合并")
        else:
            print(json.dumps(merge_people(pids[0], pids[1:]), ensure_ascii=False, indent=1))
    if a.path:
        raw = json.load(open(a.path, encoding="utf-8"))
        print(json.dumps(import_record(raw), ensure_ascii=False, indent=1))
