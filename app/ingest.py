"""采集 + 可信判定。

与某学术图谱的差别：某学术图谱把"可信门槛"写在文档里（目标 98%/99%），但缺实测数字。
这里把判定做成**可计算、可审计**的打分器：
每个候选记录都带 evidence（来源 URL、原始字段、命中规则）与 score、verdict，
后续 M3 直接用 candidates 表算分子/分母与置信区间。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from app.config import (GATE_ACCEPT, GATE_REVIEW, INGEST_AUTHORS, INGEST_FILTER,
                        INGEST_SORT, WORKS_PER_AUTHOR)
from app.db import (connect, init_db, link_author, rebuild_fts, set_meta,
                    upsert_edge, upsert_institution, upsert_person, upsert_work)
from app.openalex import OpenAlex, abstract_from_inverted

# 明显不是"人"的词：机构 / 会议 / 期刊 / 部门
NON_PERSON = re.compile(
    r"\b(universit|institut|college|school|department|faculty|academy|society|"
    r"association|conference|proceedings|symposium|workshop|journal|series|"
    r"ministry|agency|council|center|centre|laborator|hospital|foundation|"
    r"committee|federation|network|consortium|group)\b",
    re.I,
)


def judge_person(author: dict) -> dict:
    """判断一个 OpenAlex author 记录"是不是一个真实的人"。

    评分设计（每条规则给出理由，方便复核）：
      +0.55 有 ORCID（强身份标识）
      +0.20 有 works_count > 0
      +0.15 有 topics
      -0.50 名字命中机构/会议词
      -0.30 名字全大写且与 topics 名称重合（典型是主题实体）
      -0.40 名字长度 < 3
    """
    name = (author.get("display_name") or "").strip()
    reasons: list[dict[str, Any]] = []
    score = 0.0

    if author.get("orcid"):
        score += 0.55
        reasons.append({"rule": "has_orcid", "delta": 0.55, "detail": author["orcid"]})
    else:
        reasons.append({"rule": "no_orcid", "delta": 0.0, "detail": ""})

    if (author.get("works_count") or 0) > 0:
        score += 0.20
        reasons.append({"rule": "has_works", "delta": 0.20,
                        "detail": author.get("works_count")})

    topics = [t.get("display_name", "") for t in (author.get("topics") or [])]
    if topics:
        score += 0.15
        reasons.append({"rule": "has_topics", "delta": 0.15, "detail": topics[:3]})

    if NON_PERSON.search(name):
        score -= 0.50
        reasons.append({"rule": "non_person_word", "delta": -0.50,
                        "detail": NON_PERSON.search(name).group(0)})

    if name.isupper() and len(name) > 6 and name.lower() in {t.lower() for t in topics}:
        score -= 0.30
        reasons.append({"rule": "name_is_topic", "delta": -0.30, "detail": name})

    if len(name) < 3:
        score -= 0.40
        reasons.append({"rule": "name_too_short", "delta": -0.40, "detail": name})

    score = max(0.0, min(1.0, score))
    verdict = ("accept" if score >= GATE_ACCEPT
               else "review" if score >= GATE_REVIEW else "reject")
    return {"score": round(score, 3), "verdict": verdict, "reasons": reasons}


def _inst(author: dict) -> dict | None:
    insts = author.get("last_known_institutions") or []
    if not insts:
        return None
    first = insts[0]
    return {
        "openalex_id": (first.get("id") or "").split("/")[-1] or None,
        "name": first.get("display_name") or "",
        "country": first.get("country_code"),
        "type": first.get("type"),
    }


def ingest(limit: int = INGEST_AUTHORS, works_per_author: int = WORKS_PER_AUTHOR,
           log=print) -> dict:
    init_db()
    oa = OpenAlex()
    conn = connect()
    n_people = n_works = n_edges = n_accept = n_review = n_reject = 0
    t0 = time.time()

    for i, author in enumerate(
            oa.iter_authors(limit=limit, filter_=INGEST_FILTER, sort=INGEST_SORT), 1):
        oid = (author.get("id") or "").split("/")[-1]
        if not oid:
            continue
        inst = _inst(author)
        if inst:
            upsert_institution(conn, inst)
        topics = [t.get("display_name", "") for t in (author.get("topics") or [])][:8]
        judged = judge_person(author)

        pid = upsert_person(conn, {
            "openalex_id": oid,
            "name": (author.get("display_name") or "").strip(),
            "orcid": author.get("orcid"),
            "works_count": author.get("works_count") or 0,
            "cited_by_count": author.get("cited_by_count") or 0,
            "h_index": (author.get("summary_stats") or {}).get("h_index"),
            "last_inst": inst["name"] if inst else None,
            "country": inst.get("country") if inst else None,
            "topics": json.dumps(topics, ensure_ascii=False),
            "status": "published" if judged["verdict"] == "accept" else "pending",
            "confidence": judged["score"],
            "evidence": json.dumps({
                "source": f"https://api.openalex.org/authors/{oid}",
                "fields": {"display_name": author.get("display_name"),
                           "orcid": author.get("orcid"),
                           "works_count": author.get("works_count")},
                "reasons": judged["reasons"],
            }, ensure_ascii=False),
            "updated_at": time.time(),
        })
        n_people += 1
        n_accept += judged["verdict"] == "accept"
        n_review += judged["verdict"] == "review"
        n_reject += judged["verdict"] == "reject"

        conn.execute("""INSERT INTO candidates(kind,payload,evidence,score,verdict,reasons,created_at)
                        VALUES(?,?,?,?,?,?,?)""",
                     ("person", json.dumps({"openalex_id": oid,
                                            "name": author.get("display_name")},
                                           ensure_ascii=False),
                      json.dumps({"source": f"https://api.openalex.org/authors/{oid}"}),
                      judged["score"], judged["verdict"],
                      json.dumps(judged["reasons"], ensure_ascii=False), time.time()))

        # 论文 + 合作边（合作者用 author.id，不用 display_name，避免同名/机构名污染）
        try:
            works = oa.works_of_author(oid, per_page=works_per_author)
        except Exception as exc:                      # noqa: BLE001
            log(f"  [{i}] works 拉取失败 {oid}: {type(exc).__name__}")
            works = []

        author_pids: list[int] = []
        for w in works:
            wid_open = (w.get("id") or "").split("/")[-1]
            if not wid_open:
                continue
            loc = w.get("primary_location") or {}
            venue = ((loc.get("source") or {}) or {}).get("display_name")
            wid = upsert_work(conn, {
                "openalex_id": wid_open,
                "doi": w.get("doi"),
                "title": w.get("title") or "",
                "abstract": abstract_from_inverted(w.get("abstract_inverted_index")),
                "year": w.get("publication_year"),
                "cites": w.get("cited_by_count") or 0,
                "venue": venue,
                "type": w.get("type"),
            })
            n_works += 1
            for pos, au in enumerate(w.get("authorships") or []):
                a_obj = au.get("author") or {}
                a_oid = (a_obj.get("id") or "").split("/")[-1]
                if not a_oid:
                    continue
                if a_oid == oid:
                    link_author(conn, wid, pid, pos)
                    continue
                # 只连已经在库里的人，避免把全世界作者都建进来
                row = conn.execute("SELECT pid FROM people WHERE openalex_id=?",
                                   (a_oid,)).fetchone()
                if row:
                    other = int(row["pid"])
                    link_author(conn, wid, other, pos)
                    upsert_edge(conn, pid, other)
                    n_edges += 1

        conn.commit()
        if i % 25 == 0 or i == limit:
            log(f"  [{i}/{limit}] 人 {n_people} 论文 {n_works} 边 {n_edges} "
                f"({time.time()-t0:.0f}s)")

    log("重建全文索引…")
    rebuild_fts(conn)
    set_meta(conn, "last_ingest", {"ts": time.time(), "people": n_people,
                                   "works": n_works, "edges": n_edges})
    conn.commit()
    conn.close()
    return {"people": n_people, "works": n_works, "edges": n_edges,
            "accept": n_accept, "review": n_review, "reject": n_reject,
            "seconds": round(time.time() - t0, 1)}


if __name__ == "__main__":
    print(ingest(log=lambda s: print(s, flush=True)))
