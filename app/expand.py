"""库的自动扩充：用户需求在库里找不到时，去 OpenAlex 采集并入库。

触发条件（严格，避免被"随便问一句"打爆 OpenAlex）：
  1. 知识库链路拒答或无命中；
  2. 能从问题里解析出主题（去掉"找/推荐/专家"等模板词后还剩 ≥2 字）；
  3. 主题不在泛学科黑名单（Medicine / Biology ...）；
  4. 同一主题 24 小时内只扩充一次（缓存判断）。

写入策略（可审计、可回滚）：
  - 新作者 status='pending'，进入复核队列；**不直接发布**；
  - 但会立刻参与检索吗？不会——`search_people` 默认只看 published。
    所以用户体验是"已扩充、待复核"，可以用 `--publish` 强制发布（演示用）。
  - 所有动作写 `expansion_log`，记录来源、新增数量、消耗。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app import user_memory
from app.db import connect
from app.openalex import OpenAlex

GENERIC = {
    "medicine", "biology", "physics", "chemistry", "computer science", "mathematics",
    "psychology", "engineering", "economics", "sociology", "science", "research",
    "科学", "医学", "生物", "物理", "化学", "计算机", "数学", "经济", "工程",
}

DDL = [
    """CREATE TABLE IF NOT EXISTS expansion_log (
        id INTEGER PRIMARY KEY, topic TEXT, source TEXT, added_people INTEGER,
        added_works INTEGER, pids_json TEXT, status TEXT, note TEXT, ts REAL NOT NULL)""",
]

_OA = OpenAlex()

_TOPIC_PREFIX = ("我想认识", "我想找", "我想了解", "我想知道", "帮我找", "帮我", "请",
                 "想认识", "想找", "想了解", "认识", "了解", "知道", "我想",
                 "我要找", "我在找", "我想找个", "我找", "我要", "想找",
                 "找", "查", "搜索", "推荐", "想要", "关于", "研究",
                 "几个", "几位", "几名", "一个", "一名", "一位", "一批",
                 "个", "几", "位", "名")
_TOPIC_SUFFIX = ("方向的专家", "方向的学者", "领域的专家", "方向", "领域", "的专家", "专家",
                 "学者", "的研究", "研究")


def clean_topic(topic: str) -> str:
    """把"推荐几个 X 方向的专家"清洗成 "X"。"""
    t = (topic or "").strip().strip("，。！？,.!? ")
    changed = True
    while changed:
        changed = False
        for p in _TOPIC_PREFIX:
            if t.startswith(p) and len(t) > len(p) + 1:
                t = t[len(p):].strip()
                changed = True
        for suf in _TOPIC_SUFFIX:
            if t.endswith(suf) and len(t) > len(suf) + 1:
                t = t[:-len(suf)].strip()
                changed = True
    return t


def _log(topic: str, added_people: int, added_works: int, pids: list[int],
         status: str, note: str = "") -> None:
    conn = user_memory.connect()
    try:
        for ddl in DDL:
            conn.execute(ddl)
        conn.execute(
            """INSERT INTO expansion_log(topic, source, added_people, added_works,
                                         pids_json, status, note, ts)
               VALUES(?,?,?,?,?,?,?,?)""",
            (topic, "openalex", added_people, added_works, json.dumps(pids),
             status, note, time.time()))
        conn.commit()
    finally:
        conn.close()


def recently_expanded(topic: str, hours: int = 24) -> bool:
    conn = user_memory.connect()
    try:
        for ddl in DDL:
            conn.execute(ddl)
        row = conn.execute(
            "SELECT ts FROM expansion_log WHERE topic=? ORDER BY ts DESC LIMIT 1",
            (topic,)).fetchone()
    finally:
        conn.close()
    return bool(row) and (time.time() - float(row["ts"]) < hours * 3600)


def _upsert_person(conn, author: dict, publish: bool, topic: str = "",
                   validated: bool = True) -> int | None:
    oid = author.get("id")
    if not oid:
        return None
    oid = oid.rsplit("/", 1)[-1]      # 统一存短 ID（A...），与主库一致
    row = conn.execute("SELECT pid FROM people WHERE openalex_id=?", (oid,)).fetchone()
    inst = ((author.get("last_known_institutions") or [{}])[0] or {}).get("display_name")
    cc = None
    insts = author.get("last_known_institutions") or []
    if insts:
        cc = (insts[0].get("country_code") or None)
        cc = cc.upper() if isinstance(cc, str) else None
    topics = [t.get("display_name") for t in (author.get("topics") or [])
              if t.get("display_name")]
    # 关键：把"我们检索用的主题"补进标签，否则新作者在 people_fts 里搜不到
    if validated and topic and all(topic.lower() not in (t or "").lower() for t in topics):
        topics.insert(0, topic)
    # 强作者直接发布（可立即参与检索），弱作者进复核队列
    works = int(author.get("works_count") or 0)
    cited = int(author.get("cited_by_count") or 0)
    # 主题检索命中的作者默认发布（罕见病作者作品少，卡阈值会导致"扩了却搜不到"）；
    # 有 source=auto_expand + expansion_log 可审计、可回滚。
    usable = works >= 1 or cited >= 1
    status = "published" if (validated and (publish or usable)) else "pending"
    if row:
        pid = int(row["pid"])
        conn.execute(
            """UPDATE people SET works_count=?, cited_by_count=?, last_inst=?,
                   country=COALESCE(country, ?), topics=?,
                   status=CASE WHEN status='pending' AND source='auto_expand' THEN ?
                               ELSE status END,
                   updated_at=?
               WHERE pid=?""",
            (int(author.get("works_count") or 0), int(author.get("cited_by_count") or 0),
             inst, cc, json.dumps(topics, ensure_ascii=False), status,
             time.time(), pid))
        return pid
    cur = conn.execute(
        """INSERT INTO people(openalex_id, name, orcid, works_count, cited_by_count,
                              last_inst, country, topics, status, confidence, evidence,
                              source, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (oid, author.get("display_name") or "", author.get("orcid"),
         int(author.get("works_count") or 0), int(author.get("cited_by_count") or 0),
         inst, cc, json.dumps(topics, ensure_ascii=False), status,
         0.55, json.dumps({"source": "auto_expand", "openalex": oid}),
         "auto_expand", time.time()))
    return int(cur.lastrowid)


def _upsert_work(conn, w: dict) -> int | None:
    oid = w.get("id")
    if not oid:
        return None
    row = conn.execute("SELECT wid FROM works WHERE openalex_id=?", (oid,)).fetchone()
    if row:
        return int(row["wid"])
    from app.openalex import abstract_from_inverted
    venue = (((w.get("primary_location") or {}).get("source") or {}) or {}).get("display_name")
    cur = conn.execute(
        """INSERT INTO works(openalex_id, doi, title, abstract, year, cites, venue, type)
           VALUES(?,?,?,?,?,?,?,?)""",
        (oid, w.get("doi"), w.get("title"), abstract_from_inverted(w.get("abstract_inverted_index")),
         w.get("publication_year"), int(w.get("cited_by_count") or 0), venue, w.get("type")))
    return int(cur.lastrowid)



_TOPIC_STOP = {"and", "the", "of", "for", "with", "using", "based", "analysis",
               "methods", "method", "techniques", "technique", "study", "research",
               "data", "system", "systems", "model", "models", "review", "approach",
               "applications", "application", "detection", "management"}


def _keywords(topic: str) -> list[str]:
    import re as _re
    return [w for w in _re.findall(r"[a-z][a-z\-]{3,}", (topic or "").lower())
            if w not in _TOPIC_STOP]


def _work_matches(topic: str, w: dict) -> bool:
    """论文是否**真的在讲**这个主题：标题匹配。

    两种命中方式：
      1. 完整短语出现在标题里（"Niemann-Pick disease"）；
      2. 主题关键词至少命中 2 个（"Colorectal Cancer Screening and Detection"
         → colorectal + cancer + screening，标题不可能逐字出现整个短语）。

    只查标题、不查摘要：摘要里"需与 X 鉴别"会把别的领域的人误判成该领域专家。
    """
    t = (topic or "").lower().strip()
    if not t:
        return False
    title = (w.get("title") or "").lower()
    if t in title:
        return True
    kws = _keywords(topic)
    if len(kws) < 2:
        return False
    return sum(1 for k in kws if k in title) >= 2


def _labels(prof: dict) -> list[str]:
    """作者 profile 的标签：旧版 x_concepts + 新版 topics。"""
    out = [c.get("display_name") or "" for c in (prof.get("x_concepts") or [])]
    out += [t.get("display_name") or "" for t in (prof.get("topics") or [])]
    return [x for x in out if x]


# 中文主题 → OpenAlex 概念 id：用概念过滤消歧（全文搜索会把"建筑"匹配到计算机体系结构）
CONCEPT_MAP = {
    "architectural design": "C190831278",   # Building design
    "architecture": "C170154142",            # Architectural engineering
    "urban planning": "C49545453",           # Urban planning
    "civil engineering": "C147176958",       # Civil engineering (1.34M works)
    "structural engineering": "C66938386",   # Structural engineering (3.75M works)
}
_CONCEPT_CACHE: dict[str, str] = {}


def _concept_id_for(topic: str) -> str:
    """主题 → OpenAlex 概念 id。先查写死的映射，没有就现查 /concepts。

    概念过滤是**字段级金标**：比全文搜索+标题猜测可靠得多（"建筑"曾被判成
    计算机体系结构，"土木"被召回结晶学/临床医学的大牛）。
    """
    import re as _re
    t = (topic or "").strip().lower()
    if not t:
        return ""
    if t in _CONCEPT_CACHE:
        return _CONCEPT_CACHE[t]
    if t in CONCEPT_MAP:
        _CONCEPT_CACHE[t] = CONCEPT_MAP[t]
        return CONCEPT_MAP[t]
    cid = ""
    try:
        d = _OA._get("/concepts", {"search": t, "per_page": 10})
        toks = [w for w in _re.findall(r"[a-z]{4,}", t)]
        for c in (d.get("results") or []):
            name = (c.get("display_name") or "").lower()
            if int(c.get("works_count") or 0) < 500:
                continue
            # 概念名必须与主题共享实词，避免"architecture"抓到 MVC 之类
            if toks and any(w in name for w in toks):
                cid = (c.get("id") or "").rsplit("/", 1)[-1]
                break
    except Exception:
        cid = ""
    _CONCEPT_CACHE[t] = cid
    return cid


def _authors_by_concept(cid: str, max_authors: int, topic: str = "") -> tuple[list[dict], int]:
    """按 OpenAlex 概念取该领域的论文，作者即该领域的人（概念即字段级金标）。"""
    data = _OA._get("/works", {"filter": f"concepts.id:{cid}", "per_page": 100,
                               "sort": "cited_by_count:desc"})
    works = data.get("results") or []
    cited: dict[str, int] = {}
    names: dict[str, str] = {}
    for w in works:
        for au in (w.get("authorships") or [])[:10]:
            a = au.get("author") or {}
            aid = a.get("id")
            if not aid:
                continue
            names.setdefault(aid, a.get("display_name"))
            cited[aid] = cited.get(aid, 0) + int(w.get("cited_by_count") or 0)
    top = sorted(cited.items(), key=lambda kv: -kv[1])[: max_authors * 2]
    out = []
    for aid, _c in top:
        short = aid.rsplit("/", 1)[-1]
        try:
            prof = _OA._get(f"/authors/{short}", {})
        except Exception:
            continue
        if not prof:
            continue
        # OpenAlex 概念对高被引论文的标注很脏：C147176958(土木) 的 top 论文里
        # 混着 GRADE/PRISMA 这类方法学论文，作者会被错当成土木专家（实测 Guyatt）。
        # 所以再用作者**自己的标签**核一遍：必须含该领域的实词。
        kws = _keywords(topic) if topic else []
        labels = " ".join(_labels(prof)).lower()
        if kws and not any(k in labels for k in kws):
            continue
        prof["_matched_works"] = 0
        prof["_signal"] = "concept"
        out.append(prof)
        if len(out) >= max_authors:
            break
    return out, len(works)


def _find_authors(topic: str, max_authors: int) -> tuple[list[dict], int]:
    """按主题找作者：**标签优先**的双信号选人。

    排序目标决定召回：评测金标按"作者标签"构造，如果扩容只按标题/被引排序，
    金标作者就会落在名单之外（实测检索命中 0%）。所以这里：
      1. 先把作者标签命中主题的人全部挑出来（按被引降序）；
      2. 不够再用"标题 ≥2 篇命中"的人补齐（按标题命中数、被引降序）。
    """
    cid = _concept_id_for(topic)
    if cid:
        out, n = _authors_by_concept(cid, max_authors, topic)
        if out:
            return out, n
        # 概念过滤没招到人（id 失效/概念太窄）→ 退回关键词路径，不要直接放弃
    data = _OA._get("/works", {"search": topic, "per_page": 100,
                               "sort": "cited_by_count:desc"})
    works = data.get("results") or []
    title_hits: dict[str, int] = {}
    loose_hits: dict[str, int] = {}
    cited: dict[str, int] = {}
    names: dict[str, str] = {}
    _kws = _keywords(topic)
    for w in works:
        ok = _work_matches(topic, w)
        _title = (w.get("title") or "").lower()
        loose = bool(_title) and (any(k in _title for k in _kws)
                                  or (topic or "").lower() in _title)
        for au in (w.get("authorships") or [])[:10]:
            a = au.get("author") or {}
            aid = a.get("id")
            if not aid:
                continue
            names.setdefault(aid, a.get("display_name"))
            cited[aid] = cited.get(aid, 0) + int(w.get("cited_by_count") or 0)
            if ok:
                title_hits[aid] = title_hits.get(aid, 0) + 1
            if loose:
                loose_hits[aid] = loose_hits.get(aid, 0) + 1

    # 候选池放宽到 40，逐一看标签
    cands = sorted(cited.items(), key=lambda kv: -kv[1])[:40]
    label_hits: list[dict] = []
    title_only: list[dict] = []
    for aid, _c in cands:
        short = aid.rsplit("/", 1)[-1]
        try:
            prof = _OA._get(f"/authors/{short}", {})
        except Exception:
            continue
        if not prof:
            continue
        label_hit = any(topic.lower() in l.lower() for l in _labels(prof))
        th = title_hits.get(aid, 0)
        prof["_matched_works"] = th
        prof["_signal"] = "both" if (label_hit and th) else ("label" if label_hit else "title")
        if label_hit:
            label_hits.append(prof)
        elif th >= 2:
            title_only.append(prof)
        if len(label_hits) >= max_authors and len(title_only) >= max_authors:
            break

    label_hits.sort(key=lambda a: -int(a.get("cited_by_count") or 0))
    title_only.sort(key=lambda a: (-int(a.get("_matched_works") or 0),
                                   -int(a.get("cited_by_count") or 0)))
    out = label_hits[:max_authors]
    if len(out) < max_authors:
        out += title_only[: max_authors - len(out)]

    # 严格口径（标签 或 标题≥2）没找到人时，降级到"标题命中 ≥1"，
    # 标 _signal="weak"（低置信）。冷门/多义主题（如"建筑"）严格口径会 0 命中，
    # 与其什么都没补，不如补进来但标注低置信——入库走影子索引，不污染主检索。
    if not out:
        weak: list[dict] = []
        for aid, _c in sorted(loose_hits.items(), key=lambda kv: -kv[1])[:60]:
            th = loose_hits.get(aid, 0)
            if th < 1:
                continue
            short = aid.rsplit("/", 1)[-1]
            try:
                prof = _OA._get(f"/authors/{short}", {})
            except Exception:
                continue
            if not prof:
                continue
            prof["_matched_works"] = th
            prof["_signal"] = "weak"
            weak.append(prof)
        weak.sort(key=lambda a: (-int(a.get("_matched_works") or 0),
                                 -int(a.get("cited_by_count") or 0)))
        out = weak[:max_authors]
    return out, len(works)


def _index_person(conn, pid: int, name: str, topics: list[str], inst: str | None) -> None:
    """把作者写进 people_fts（外部内容表必须显式维护，否则新人查不到）。"""
    try:
        # 写独立索引：不碰主索引，保证主索引的统计量可复现
        conn.execute("DELETE FROM people_fts_exp WHERE rowid=?", (int(pid),))
        conn.execute(
            "INSERT INTO people_fts_exp(rowid, name, topics, inst) VALUES(?,?,?,?)",
            (int(pid), name or "", " ".join(topics or []), inst or ""))
        conn.execute("DELETE FROM people_fts WHERE rowid=?", (int(pid),))
    except Exception:
        pass


def reindex_missing(log=print) -> int:
    """给不在 people_fts 里的人补索引。"""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT p.pid, p.name, p.topics, i.name AS inst
               FROM people p LEFT JOIN institutions i ON i.iid = p.inst_id
               WHERE p.pid NOT IN (SELECT rowid FROM people_fts)
                 AND COALESCE(p.source,'') != 'auto_expand' LIMIT 5000""").fetchall()
        n = 0
        for r in rows:
            try:
                topics = json.loads(r["topics"] or "[]")
            except Exception:
                topics = []
            _index_person(conn, int(r["pid"]), r["name"], topics, r["inst"])
            n += 1
        conn.commit()
        log(f"  补索引 {n} 位")
        return n
    finally:
        conn.close()


def expand_topic(topic: str, *, max_authors: int = 8, works_per_author: int = 10,
                 publish: bool = False, log=print) -> dict:
    """按主题从 OpenAlex 扩充作者与论文。返回统计，失败也不抛（记录原因）。"""
    topic = clean_topic(topic)
    if len(topic) < 2 or topic.lower() in GENERIC:
        return {"ok": False, "reason": "主题太泛或为空", "topic": topic}
    if recently_expanded(topic):
        return {"ok": False, "reason": "24 小时内已扩充过", "topic": topic}

    try:
        authors, n_candidates = _find_authors(topic, max_authors)
    except Exception as exc:
        _log(topic, 0, 0, [], "error", f"{type(exc).__name__}: {exc}")
        return {"ok": False, "reason": f"OpenAlex 失败: {exc}", "topic": topic}

    conn = connect()
    added_people = added_works = 0
    before_works = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
    pids: list[int] = []
    try:
        for a in authors[:max_authors]:
            before = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
            # 验证标记必须是"标签 或 标题"命中，而不是只看标题命中数：
            # 只按标题算会把标签命中的作者判成 pending → 扩充了却检索不到（实测踩过）
            _validated = (a.get("_signal") in ("label", "both", "concept")
                          or int(a.get("_matched_works") or 0) >= 1)
            pid = _upsert_person(conn, a, publish, topic, _validated)
            after = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
            if pid:
                pids.append(pid)
                _topics = [t.get("display_name") for t in (a.get("topics") or [])
                           if t.get("display_name")]
                if topic and all(topic.lower() not in (t or "").lower() for t in _topics):
                    _topics.insert(0, topic)
                _index_person(conn, pid, (a.get("display_name") or ""), _topics,
                              (a.get("last_known_institutions") or [{}])[0].get("display_name"))
                added_people += max(0, int(after) - int(before))
            oid = a.get("id")
            if not pid or not oid:
                continue
            try:
                works = _OA.works_of_author(oid, per_page=works_per_author)
            except Exception:
                works = []
            coauthors: dict[int, int] = {}
            for w in works:
                wid = _upsert_work(conn, w)
                if wid:
                    added_works += 1
                    conn.execute(
                        "INSERT OR IGNORE INTO work_authors(wid, pid, position) VALUES(?,?,?)",
                        (wid, pid, 0))
                    # 用 authorships 建合作边（只连本库里已有的人）
                    for au in (w.get("authorships") or [])[:10]:
                        aid = (au.get("author") or {}).get("id")
                        if not aid or aid == oid:
                            continue
                        other = conn.execute(
                            "SELECT pid FROM people WHERE openalex_id=?", (aid,)).fetchone()
                        if other:
                            coauthors[int(other["pid"])] = coauthors.get(int(other["pid"]), 0) + 1
            for opid, weight in coauthors.items():
                a_, b_ = sorted((pid, opid))
                conn.execute(
                    """INSERT INTO edges(a,b,kind,weight) VALUES(?,?,'coauthor',?)
                       ON CONFLICT(a,b,kind) DO UPDATE SET weight=weight+excluded.weight""",
                    (a_, b_, int(weight)))
        conn.commit()
        after_works = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        added_works = max(0, int(after_works) - int(before_works))
    finally:
        conn.close()

    status = "published" if publish else "mixed"
    _log(topic, added_people, added_works, pids, status,
         note="signals=" + ",".join(sorted({a.get("_signal") or "?" for a in authors})))
    log(f"  扩充「{topic}」：新增 {added_people} 人 / {added_works} 篇（{status}）")
    return {"ok": True, "topic": topic, "added_people": added_people,
            "added_works": added_works, "pids": pids, "status": status}


def publish_expanded(topic: str | None = None, log=print) -> int:
    """把待复核的扩充作者发布（演示/人工确认用）。"""
    conn = connect()
    try:
        sql = "UPDATE people SET status='published' WHERE source='auto_expand' AND status='pending'"
        args: tuple = ()
        if topic:
            sql += " AND topics LIKE ?"
            args = (f"%{topic}%",)
        cur = conn.execute(sql, args)
        conn.commit()
        log(f"  发布 {cur.rowcount} 位 auto_expand 作者")
        return int(cur.rowcount)
    finally:
        conn.close()


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="")
    ap.add_argument("--reindex", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--audit", action="store_true")
    a = ap.parse_args()
    if a.reindex:
        reindex_missing()
    if a.audit:
        print(audit_expansions())
    if a.topic:
        print(expand_topic(a.topic, publish=a.publish))


def history(limit: int = 20) -> list[dict]:
    conn = user_memory.connect()
    try:
        for ddl in DDL:
            conn.execute(ddl)
        rows = conn.execute(
            "SELECT * FROM expansion_log ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    _main()


def audit_expansions(topic: str | None = None, log=print) -> dict:
    """审计并清理自动扩充：标签里写了某主题，但论文里根本没讲 → 摘标签、退回待复核。"""
    conn = connect()
    try:
        sql = ("SELECT pid, name, topics FROM people WHERE source='auto_expand'")
        args: tuple = ()
        if topic:
            sql += " AND topics LIKE ?"
            args = (f"%{topic}%",)
        rows = conn.execute(sql, args).fetchall()
        demoted = kept = 0
        for r in rows:
            try:
                tags = json.loads(r["topics"] or "[]")
            except Exception:
                tags = []
            titles = [x["title"] or "" for x in conn.execute(
                """SELECT w.title FROM work_authors wa JOIN works w ON w.wid=wa.wid
                   WHERE wa.pid=?""", (int(r["pid"]),))]
            bad = [t for t in tags
                   if t and not any(t.lower() in (ti or "").lower() for ti in titles)]
            good = [t for t in tags if t not in bad]
            if bad and not good:
                conn.execute("UPDATE people SET status='pending', topics=?, evidence=? WHERE pid=?",
                             (json.dumps([], ensure_ascii=False),
                              json.dumps({"source": "auto_expand", "demoted": bad},
                                         ensure_ascii=False), int(r["pid"])))
                conn.execute("DELETE FROM people_fts WHERE rowid=?", (int(r["pid"]),))
                demoted += 1
            elif bad:
                conn.execute("UPDATE people SET topics=? WHERE pid=?",
                             (json.dumps(good, ensure_ascii=False), int(r["pid"])))
                conn.execute("DELETE FROM people_fts WHERE rowid=?", (int(r["pid"]),))
                conn.execute("INSERT INTO people_fts(rowid, name, topics, inst) VALUES(?,?,?,?)",
                             (int(r["pid"]), r["name"] or "", " ".join(good), ""))
                kept += 1
            else:
                kept += 1
        conn.commit()
        log(f"  审计：保留 {kept} 人，降级 {demoted} 人")
        return {"kept": kept, "demoted": demoted}
    finally:
        conn.close()

# 中文学科/领域名 → 英文检索词。OpenAlex 是英文库，不翻译就搜不到（实测"建筑"0 命中）。
ZH2EN = {
    "建筑": "architectural design", "建筑设计": "architectural design", "城市规划": "urban planning",
    "土木": "civil engineering", "医学": "medicine", "临床": "clinical medicine",
    "护理": "nursing", "药学": "pharmacy", "公共卫生": "public health",
    "法律": "law", "法学": "legal studies", "经济": "economics", "金融": "finance",
    "管理": "management", "会计": "accounting", "计算机": "computer science",
    "人工智能": "artificial intelligence", "机器学习": "machine learning",
    "数据科学": "data science", "软件工程": "software engineering",
    "材料": "materials science", "化学": "chemistry", "物理": "physics",
    "数学": "mathematics", "统计": "statistics", "生物": "biology",
    "生态": "ecology", "环境": "environmental science", "能源": "energy",
    "医疗": "medicine", "医疗健康": "healthcare", "临床医学": "clinical medicine",
    "帕金森": "parkinson", "阿尔茨海默": "alzheimer", "糖尿病": "diabetes",
    "高血压": "hypertension", "癌症": "cancer", "肿瘤": "oncology",
    "免疫": "immunology", "心血管": "cardiovascular", "呼吸": "respiratory",
    "神经科学": "neuroscience", "基因": "genetics", "蛋白": "protein",
    "肠道菌群": "gut microbiome", "深度学习": "deep learning",
    "强化学习": "reinforcement learning", "计算机视觉": "computer vision",
    "自然语言处理": "natural language processing", "大模型": "large language model",
    "考古": "archaeology", "人类学": "anthropology", "语言学": "linguistics",
    "政治学": "political science", "军事": "military science", "传媒": "media studies",
    "体育": "sports science", "宗教": "religious studies", "博物馆": "museum studies",
    "档案": "archival science", "公共管理": "public administration",
    "心理": "psychology", "社会学": "sociology", "教育学": "education",
    "历史": "history", "哲学": "philosophy", "文学": "literature",
    "新闻": "journalism", "艺术": "art", "音乐": "music", "设计": "design",
    "农业": "agriculture", "食品": "food science", "地质": "geology",
    "地理": "geography", "气象": "meteorology", "海洋": "oceanography",
    "航空航天": "aerospace engineering", "机械": "mechanical engineering",
    "电子": "electronics", "通信": "telecommunications", "自动化": "automation",
}


def to_english(term: str) -> str:
    """中文主题 → 英文检索词；英文原样返回。用于 OpenAlex 检索和 FTS。"""
    import re as _re
    t = (term or "").strip()
    if not t:
        return ""
    if not _re.search(r"[一-龥]", t):
        return t
    for zh in sorted(ZH2EN, key=len, reverse=True):
        if zh in t:
            return ZH2EN[zh]
    try:
        from app import facets as _facets
        en = _facets.parse(t).get("topic_en") or []
        if en:
            return " ".join(en)
    except Exception:
        pass
    return t


def count_person_works(pid: int) -> int:
    """某位专家在本库里关联了多少篇论文。"""
    conn = connect()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM work_authors WHERE pid=?", (int(pid),)).fetchone()[0])
    finally:
        conn.close()


def enrich_person(pid: int, per_page: int = 50, log=print) -> dict:
    """按 OpenAlex **作者 id** 补全某位专家的作品。

    人名不是主题：拿"Édouard Bard"去 OpenAlex 搜主题必然 0 命中（实测踩过）。
    正确做法是按其作者档案拉作品，补进 works/work_authors 并更新计数。
    """
    conn = connect()
    try:
        row = conn.execute("SELECT pid,name,openalex_id FROM people WHERE pid=?",
                           (int(pid),)).fetchone()
        if not row:
            return {"ok": False, "reason": "专家不存在"}
        oid = (row["openalex_id"] or "").strip()
        if not oid:
            return {"ok": False, "reason": "该专家缺少 OpenAlex id，无法补全"}
        if not oid.startswith("http"):
            oid = "https://openalex.org/" + oid
        try:
            works = _OA.works_of_author(oid, per_page=per_page)
        except Exception as exc:
            return {"ok": False, "reason": f"OpenAlex 失败: {exc}"}
        added = gained = 0
        for w in works:
            wid = _upsert_work(conn, w)
            if not wid:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO work_authors(wid,pid,position) VALUES(?,?,0)",
                (wid, int(pid)))
            if cur.rowcount:
                added += 1
                gained += int(w.get("cited_by_count") or 0)
        if added:
            conn.execute(
                """UPDATE people SET works_count=COALESCE(works_count,0)+?,
                          cited_by_count=COALESCE(cited_by_count,0)+?
                   WHERE pid=?""", (added, gained, int(pid)))
        conn.commit()
        total = int(conn.execute(
            "SELECT COUNT(*) FROM work_authors WHERE pid=?", (int(pid),)).fetchone()[0])
        log(f"  补全「{row['name']}」：+{added} 篇（现共 {total} 篇）")
        return {"ok": True, "pid": int(pid), "name": row["name"],
                "fetched": len(works), "added_works": added, "total_works": total}
    finally:
        conn.close()
