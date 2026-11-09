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
    # 通用学科（让 facet 检索不只覆盖医学）
    "机器学习": ["machine learning"], "人工智能": ["artificial intelligence"],
    "深度学习": ["deep learning"], "神经网络": ["neural network"],
    "计算机视觉": ["computer vision"], "自然语言处理": ["natural language processing"],
    "大模型": ["large language model"], "数据挖掘": ["data mining"],
    "强化学习": ["reinforcement learning"], "图神经网络": ["graph neural network"],
    "材料": ["materials"], "化学": ["chemistry"], "物理": ["physics"],
    "数学": ["mathematics"], "统计": ["statistics"], "计算机": ["computer science"],
    "基因": ["gene"], "蛋白质": ["protein"], "免疫": ["immunology"],
    "神经科学": ["neuroscience"], "心理学": ["psychology"], "经济学": ["economics"],
}

# 生成英文检索词
def _keywords(text: str, limit: int = 8) -> list[str]:
    """抽取**用于检索的英文词**：先查主题词表，再补英文原词。

    中文不能直接拿去搜英文语料（"肺癌" 在 works_fts 里几乎不命中），
    所以这里输出的是英文检索词；中文原词另存 `labels` 供报告展示。
    """
    text = text or ""
    out: list[str] = []
    # 1、从中文主题词表翻译
    for zh, ens in LEX.items():
        if zh in text:
            out.extend(ens)
    # 2、补充用户原句中的英文词
    out += [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text)]
    # 3、去重并限制数量
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
"""
researcher 使用多路召回提高覆盖率：人物索引擅长直接找专家，论文索引可以找出人物索引遗漏的作者
两条结果按 PID 聚合去重，再根据匹配分数和是否有可用档案排序，输出结构化候选和来源指针，为后续分析和核验提供输入。
"""
def researcher(query: str, limit: int = 8, log=print) -> dict:
    """多通道召回：人物 FTS + 论文 FTS → 聚合到作者 → 去重排序。

    只返回候选与证据指针，不做解释（解释交给 analyst/verifier）。
    """
    from app.search import search_people, search_works
    # 1、提取关键词
    kws = _keywords(query)
    # 2、用 seen 聚合候选
    seen: dict[int, dict] = {}
    for kw in kws:
        # 3、人物索引通道 如果同一个人被多个关键词命中，hits 会增加；best 保留最高分；via 记录来源。
        for pid, score in search_people(kw, limit=20):
            d = seen.setdefault(pid, {"pid": pid, "hits": 0, "best": 0.0, "via": set()})
            d["hits"] += 1
            d["best"] = max(d["best"], float(score))
            d["via"].add(f"people:{kw}")
        # 4、论文通道：论文搜索可以找出人物索引中没有出现的作者。
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
            # 5、查询候选人的基础信息
            row = conn.execute(
                """SELECT p.name,p.name_zh,p.last_inst,p.status,
                          (SELECT COUNT(*) FROM dossier dz WHERE dz.pid=p.pid) AS has_dossier
                   FROM people p WHERE p.pid=?""", (pid,)).fetchone()
            if not row or row["status"] != "published":
                continue
            d.update(name=row["name"], name_zh=row["name_zh"], inst=row["last_inst"],
                     has_dossier=bool(row["has_dossier"]),
                     via=sorted(d["via"]))
            # 6. 有深档案的专家优先
            d["score"] = round(d["best"] * (1.0 if d["has_dossier"] else 0.6), 4)
            cands.append(d)
    finally:
        conn.close()
    # 7. 排序和限制结果
    cands.sort(key=lambda x: (-x["score"], -x["hits"]))
    out = {"role": "researcher", "query": query, "keywords": kws,
           "count": len(cands), "candidates": cands[:limit]}
    log(f"    researcher: {len(kws)} 个检索词 → {len(cands)} 个候选（保留 {len(out['candidates'])}）")
    return out


# ---------------- analyst ----------------
# 查询专家机构
def _institution_of(pid: int) -> dict | None:
    """机构实体（归一后的）。失败返回 None，不影响分析主流程。"""
    try:
        from app.institutions import for_person
        return for_person(pid)
    except Exception:
        return None

# 查询常发期刊
def _person_journals(pid: int, limit: int = 3) -> list[dict]:
    """常发期刊。失败返回空表。"""
    try:
        from app.venues import person_venues
        return person_venues(pid, limit=limit)
    except Exception:
        return []

# 计算候选人的分析指标
def analyst(pids: list[int], log=print) -> dict:
    """只读档案算指标：h-index、产出节奏、近五年/早期主题、机构与常发期刊。不做检索。"""
    from app.dossiers import dossier
    from app.harness import _term_profile
    # 逐个读取深度档案
    rows = []
    for pid in pids:
        d = dossier(pid)
        if not d:
            rows.append({"pid": pid, "has_dossier": False})
            continue
        # 提取基本指标
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
            # 统计近期和早期主题
            "recent_terms": _term_profile(pid, last - 4, last + 1, top=6),
            "early_terms": _term_profile(pid, (m.get("first_year") or last), last - 5, top=6),
            # 机构归一 + 常发期刊：审稿人匹配的两个关键信号
            # （是否需要回避同机构 / 是否常发目标期刊）
            "institution": _institution_of(pid),
            "journals": _person_journals(pid, limit=3),
        })
    out = {"role": "analyst", "count": len(rows), "authors": rows}
    log(f"    analyst: {sum(1 for r in rows if r['has_dossier'])}/{len(rows)} 人有档案")
    return out


# ---------------- verifier ----------------
_TERM_STATS: dict | None = None

# 预先统计每个词被哪些作者使用
def _term_stats() -> dict:
    """一次扫全 dossier_work，算每个词在各作者身上的出现次数（进程内缓存）。

    缓存的意义：这个统计要扫全表（约 10 万行），每次请求都算会拖慢 verifier。
    """
    global _TERM_STATS
    if _TERM_STATS is not None:
        return _TERM_STATS
    conn = connect()
    try:
        # 首次调用读取所有论文标题
        rows = conn.execute("SELECT pid, lower(title) AS t FROM dossier_work").fetchall()
    finally:
        conn.close()
    counts: dict[str, dict[int, int]] = {}
    for r in rows:
        pid = int(r[0])
        for w in set((r[1] or "").split()):
            if len(w) < 4:
                continue
            d = counts.setdefault(w, {})
            d[pid] = d.get(pid, 0) + 1
    _TERM_STATS = {"counts": counts}
    return _TERM_STATS

# 判断主题词是否足够属于某些作者
def _specific_terms(pid: int, words: set[str], min_count: int = 5,
                    ratio: float = 0.8) -> set[str]:
    """这些词里，哪些"属于这个人"。

    判据（比"作者排行前 N"稳）：此人对该词的使用量既**够多**（≥ min_count 篇），
    又不低于**平均携带者的使用量**（≥ ratio × 均值）。

    为什么不用"排行前 20"：钟南山的 `asthma` 数量不如专科哮喘研究者多，
    排行法会把他自己的真实主题判成"不属于他"（实测误报 18%）。
    相对强度对"跨人混淆"同样敏感：把 B 的招牌词安到 A 头上，
    A 的使用量通常远低于均值，判据会拒绝。
    """
    if not words:
        return set()
    st = _term_stats()["counts"]
    out = set()
    for w in words:
        d = st.get(w.lower()) or {}
        n = int(d.get(int(pid), 0))
        if n <= 0 or len(d) < 3:
            continue
        base = sum(d.values()) / len(d)
        if n >= min_count and n >= ratio * base:
            out.add(w.lower())
    return out

# 逐条校验claim claim是待核验的结构化事实
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
                enough = len(hit) >= int(c.get("min_hits") or 2)
                # 命中的词里至少要有一个"属于这个人"（作者排行前 20），
                # 否则可能只是撞了同领域的通用词 —— 跨人混淆靠这一层拦
                specific = _specific_terms(int(c["pid"]), hit) if hit else set()
                ok = enough and bool(specific)
                why = ""
                if not enough:
                    why = "近五年标题里没有任何主题词（疑似同名不同人/领域不符）"
                elif not specific:
                    why = "命中的词都太通用，作者排行里没有此人（疑似领域错配）"
                (verified if ok else rejected).append(
                    {**c, "got": sorted(hit)[:5], "specific": sorted(specific)[:3],
                     "why": why})
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
