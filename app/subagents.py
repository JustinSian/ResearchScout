"""三个子 Agent：researcher / analyst / verifier。

为什么要拆角色：一个"全能 Agent"会把检索结果、指标计算、事实核对全塞进同一个上下文，
最后既贵又容易自相矛盾。拆开之后：

  researcher：只负责"找候选 + 给证据指针"，不解释、不判断
  analyst   ：只负责"读档案算指标"，不检索（避免被召回结果带偏）
  verifier  ：只负责"每条结论能不能回溯"，不生成新结论

每个角色的产出都写成结构化 JSON 落到 VFS（/agents/{role}/{tid}.json），
主上下文只拿一行摘要 + 路径 —— 这就是"上下文管理"的可测量版本。
"""
from __future__ import annotations

import json
import re
import time

from app.db import connect

_ZH_STOP = set("的 了 和 与 或 是 在 有 对 这 那 一个 一篇 关于 研究 方向 领域 专家 学者 谁 什么 如何 请 帮我".split())

# 医学主题词表：中文 → 英文同义词。
# 为什么必须映射：语料是英文（OpenAlex），中文主题词在 FTS 里命中率极低。
# 只收本领域高频词，宁可漏也不要乱扩。
LEX = {
    "肺癌": ["lung cancer", "nsclc"],
    "肝癌": ["liver cancer", "hepatocellular"],
    "肝移植": ["liver transplantation"],
    "肝炎": ["hepatitis"],
    "免疫治疗": ["immunotherapy", "immune checkpoint", "pd-1", "pd-l1"],
    "肿瘤": ["cancer", "oncology"],
    "癌症": ["cancer", "oncology"],
    "化疗": ["chemotherapy"],
    "靶向": ["targeted therapy"],
    "慢阻肺": ["copd", "chronic obstructive"],
    "哮喘": ["asthma"],
    "呼吸": ["respiratory", "pulmonary"],
    "肺炎": ["pneumonia"],
    "新冠肺炎": ["covid-19", "sars-cov-2"],
    "结核": ["tuberculosis"],
    "脓毒症": ["sepsis"],
    "重症": ["critical care", "intensive care"],
    "心脏病": ["cardiology", "cardiovascular"],
    "糖尿病": ["diabetes"],
    "肠道菌群": ["gut microbiota", "microbiome"],
    "微生物组": ["microbiome"],
    "肝病": ["liver disease", "hepatology"],
    "移植": ["transplantation"],
    "儿科": ["pediatric"],
    "影像": ["imaging", "radiology"],
    "生物信息": ["bioinformatics"],
}


def _keywords(text: str, limit: int = 8) -> list[str]:
    """抽取**用于检索的英文词**：先查主题词表，再补英文原词。

    中文不能直接拿去搜英文语料（"肺癌" 在 works_fts 里几乎不命中），
    所以这里输出的是英文检索词；中文原词另存 `labels` 供报告展示。
    """
    text = text or ""
    out: list[str] = []
    for zh, ens in LEX.items():
        if zh in text:
            out.extend(ens)
    out += [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text)]
    seen, uniq = set(), []
    for w in out:
        if w and w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq[:limit]


def topic_labels(text: str) -> list[str]:
    """中文展示用：命中词表的中文主题。"""
    return [zh for zh in LEX if zh in (text or "")]


# ---------------- researcher ----------------
def researcher(query: str, limit: int = 8, log=print) -> dict:
    """多通道召回：人物 FTS + 论文 FTS → 聚合到作者 → 去重排序。

    只返回候选与证据指针，不做解释（解释交给 analyst/verifier）。
    """
    from app.search import search_people, search_works

    kws = _keywords(query)
    seen: dict[int, dict] = {}
    for kw in kws:
        for pid, score in search_people(kw, limit=20):
            d = seen.setdefault(pid, {"pid": pid, "hits": 0, "best": 0.0, "via": set()})
            d["hits"] += 1
            d["best"] = max(d["best"], float(score))
            d["via"].add(f"people:{kw}")
        # 论文通道：命中论文 → 作者（能捞到人物 FTS 漏掉的人）
        for wid, score in search_works(kw, limit=20):
            conn = connect()
            try:
                for r in conn.execute("SELECT pid FROM work_authors WHERE wid=?", (wid,)):
                    pid = int(r["pid"])
                    d = seen.setdefault(pid, {"pid": pid, "hits": 0, "best": 0.0, "via": set()})
                    d["hits"] += 1
                    d["best"] = max(d["best"], float(score) * 0.5)
                    d["via"].add(f"works:{kw}")
            finally:
                conn.close()

    conn = connect()
    try:
        cands = []
        for pid, d in seen.items():
            row = conn.execute(
                """SELECT p.name,p.name_zh,p.last_inst,p.status,
                          (SELECT COUNT(*) FROM dossier dz WHERE dz.pid=p.pid) AS has_dossier
                   FROM people p WHERE p.pid=?""", (pid,)).fetchone()
            if not row or row["status"] != "published":
                continue
            d.update(name=row["name"], name_zh=row["name_zh"], inst=row["last_inst"],
                     has_dossier=bool(row["has_dossier"]),
                     via=sorted(d["via"]))
            # 有深档案的优先（能出尽调结论）
            d["score"] = round(d["best"] * (1.0 if d["has_dossier"] else 0.6), 4)
            cands.append(d)
    finally:
        conn.close()
    cands.sort(key=lambda x: (-x["score"], -x["hits"]))
    out = {"role": "researcher", "query": query, "keywords": kws,
           "count": len(cands), "candidates": cands[:limit]}
    log(f"    researcher: {len(kws)} 个检索词 → {len(cands)} 个候选（保留 {len(out['candidates'])}）")
    return out


# ---------------- analyst ----------------
def analyst(pids: list[int], log=print) -> dict:
    """只读档案算指标：h-index、产出节奏、近五年/早期主题。不做检索。"""
    from app.dossiers import dossier
    from app.harness import _term_profile

    rows = []
    for pid in pids:
        d = dossier(pid)
        if not d:
            rows.append({"pid": pid, "has_dossier": False})
            continue
        m = d["metrics"]
        last = m.get("last_year") or 2026
        rows.append({
            "pid": pid, "has_dossier": True,
            "name": d["name_zh"] or d["name"], "inst": d.get("inst"),
            "works": m.get("works"), "h_index": m.get("h_index"),
            "cites": m.get("cites_sum"), "median_cites": m.get("cites_median"),
            "first_year": m.get("first_year"), "last_year": last,
            "recent_5y_share": m.get("recent_5y_share"),
            "top_venues": (m.get("top_venues") or [])[:3],
            "recent_terms": _term_profile(pid, last - 4, last + 1, top=6),
            "early_terms": _term_profile(pid, (m.get("first_year") or last), last - 5, top=6),
        })
    out = {"role": "analyst", "count": len(rows), "authors": rows}
    log(f"    analyst: {sum(1 for r in rows if r['has_dossier'])}/{len(rows)} 人有档案")
    return out


# ---------------- verifier ----------------
def verifier(claims: list[dict], log=print) -> dict:
    """逐条核对：能不能回溯到库里的字段/边/标题。

    claim 形如：
      {"kind": "metric", "pid": 527, "field": "h_index", "value": 83}
      {"kind": "term",   "pid": 527, "term": "asthma"}
      {"kind": "coauthor", "a": 527, "b": 503, "shared": 7}
    """
    from app.dossiers import dossier

    verified, rejected = [], []
    cache: dict[int, dict] = {}

    def dz(pid: int) -> dict | None:
        if pid not in cache:
            cache[pid] = dossier(pid) or {}
        return cache[pid] or None

    for c in claims:
        kind = c.get("kind")
        try:
            if kind == "metric":
                d = dz(int(c["pid"]))
                m = (d or {}).get("metrics") or {}
                got = m.get(c["field"])
                ok = got == c["value"]
                (verified if ok else rejected).append(
                    {**c, "got": got, "why": "" if ok else f"档案里是 {got}"})
            elif kind == "term":
                d = dz(int(c["pid"]))
                pid = int(c["pid"])
                conn = connect()
                try:
                    n = conn.execute(
                        """SELECT COUNT(*) FROM dossier_work
                           WHERE pid=? AND lower(title) LIKE ?""",
                        (pid, f"%{c['term'].lower()}%")).fetchone()[0]
                finally:
                    conn.close()
                ok = n > 0
                (verified if ok else rejected).append(
                    {**c, "got": n, "why": "" if ok else "标题里没有这个词"})
            elif kind == "domain":
                from app.harness import _term_profile
                d = dz(int(c["pid"]))
                last = ((d or {}).get("metrics") or {}).get("last_year") or 2026
                terms = set(w.lower() for w, _n in
                            _term_profile(int(c["pid"]), last - 4, last + 1, top=20))
                topic_words = {x for t in (c.get("topic") or []) for x in t.split() if len(x) > 2}
                hit = topic_words & terms
                # 只命中 1 个词不算同领域（"cancer" 这种词几乎人人都蹭得到）
                ok = len(hit) >= int(c.get("min_hits") or 2)
                (verified if ok else rejected).append(
                    {**c, "got": sorted(hit)[:5],
                     "why": "" if ok else "近五年标题里没有任何主题词（疑似同名不同人/领域不符）"})
            elif kind == "coauthor":
                conn = connect()
                try:
                    row = conn.execute(
                        """SELECT weight FROM edges WHERE kind='coauthor' AND
                           ((a=? AND b=?) OR (a=? AND b=?))""",
                        (int(c["a"]), int(c["b"]), int(c["b"]), int(c["a"]))).fetchone()
                finally:
                    conn.close()
                got = int(row["weight"]) if row else 0
                ok = got == c["shared"]
                (verified if ok else rejected).append(
                    {**c, "got": got, "why": "" if ok else f"边权重是 {got}"})
            else:
                rejected.append({**c, "why": "未知的 claim 类型"})
        except Exception as exc:
            rejected.append({**c, "why": f"校验异常: {type(exc).__name__}"})

    total = len(claims)
    out = {"role": "verifier", "total": total, "verified": verified, "rejected": rejected,
           "pass_rate": round(len(verified) / total, 3) if total else 1.0}
    log(f"    verifier: {len(verified)}/{total} 条通过（{out['pass_rate']:.0%}）")
    return out


if __name__ == "__main__":
    import sys
    print(json.dumps(researcher(sys.argv[1] if len(sys.argv) > 1 else "肺癌 免疫治疗"), ensure_ascii=False)[:800])
