"""ScholarTrust Agent：LangGraph 编排 + 5 个结构化工具 + 引用 + 拒答。

设计取舍（与某学术图谱的 DeerFlow 式 runtime 对比）：
- 不做子代理/sandbox/MCP：那些是平台能力，不是这个项目的核心；
- 规划器用**确定性规则**而不是 LLM：演示环境无模型依赖，且每一步可解释、可单测；
- 任何工具返回空 → 明确说"资料不足"，而不是编一个答案（这是医药项目里验证过的教训）。

图结构：
    planner ──(collaborator)──> tool_collaborators ─┐
            ──(profile)──────> tool_profile ────────┤
            ──(works)────────> tool_works ──────────┼─> compose ─> END
            ──(compare)──────> tool_compare ────────┤
            ──(search)───────> tool_search ─────────┘
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.db import connect
from app.labels import label_inst, label_topic
from app.search import (graph_neighbors, person_profile, search_people,
                        search_works)


class AgentState(TypedDict, total=False):
    query: str
    query_en: str
    plan: list[dict]          # [{tool, args, ok, n, latency_ms}]
    evidence: list[dict]      # 每条工具结果的结构化证据
    answer: str
    citations: list[dict]
    refused: bool
    reason: str


# ---------------- 工具 ----------------
def _person_brief(row) -> dict:
    return {"pid": row["pid"], "name": row["name"], "inst": row["last_inst"],
            "works": row["works_count"], "cites": row["cited_by_count"],
            "score": round(float(row["confidence"] or 0), 3)}


def tool_find_expert(query: str, limit: int = 5) -> dict:
    """按研究方向找专家：**论文优先**，再用 CE 判相关性，最后聚合到作者。

    为什么不是"直接检索人物"：人物的 topics 是 OpenAlex 的粗粒度概念
    （"Computer science" 这种），CE 对 "graph neural network" 只有 0.025 分；
    而论文标题/摘要里通常写着具体方法。实测这条路径把 GOOD 查询的 CE 分从 0.03 拉到 0.9+。

    但如果查询里带了**结构化条件**（国家/机构类型/机构名/被引门槛），
    先走 facet 过滤——这类问题用论文相关性排序回答不了（"中国的呼吸科专家"
    里"中国"是硬条件，不是语义相似度能表达的）。
    """
    from app import facets as facet_mod
    parsed = facet_mod.parse(query)
    structured = any([parsed.get("country"), parsed.get("inst_kind"),
                      parsed.get("inst_name"), parsed.get("cites_min")])
    if structured:
        # 用户说"前 20 条"时，facet 里的 limit 应优先于工具默认的 5
        res = facet_mod.search(parsed, limit=int(parsed.get("limit") or limit))
        if res["count"]:
            return {"tool": "find_expert", "mode": "facet",
                    "items": res["items"], "count": res["count"], "total": res["total"],
                    "facets": {k: v for k, v in parsed.items()
                               if k not in ("notes", "topic_en") and v},
                    "notes": parsed.get("notes") or [],
                    "relaxed": res.get("relaxed", ""),
                    # 多轮收窄：只提结果里真实存在的选项
                    "refinements": facet_mod.suggest(parsed, res["items"], res["total"])}

    import math

    from app.config import CE_REFUSE
    from app.rerank_ce import scores as ce_scores

    w_hits = search_works(query, limit=10)
    conn = connect()
    try:
        docs, wid_scores = [], []
        for wid, _s in w_hits:
            row = conn.execute("SELECT wid,title,abstract FROM works WHERE wid=?",
                               (wid,)).fetchone()
            if row:
                docs.append(f"{row['title']}. {(row['abstract'] or '')[:400]}")
                wid_scores.append(wid)
        ce = ce_scores(query, docs) if docs else []
        keep = [(wid, s) for wid, s in zip(wid_scores, ce) if s >= CE_REFUSE]
        keep.sort(key=lambda kv: -kv[1])
        if not keep:
            return {"tool": "find_expert", "items": [], "count": 0,
                    "ce_max": round(max(ce), 3) if ce else 0.0}

        # 匹配到的论文 → 作者 → 按"命中论文的 CE 分之和"聚合
        agg: dict[int, float] = {}
        hits_per_pid: dict[int, int] = {}
        for wid, s in keep[:8]:
            for r in conn.execute("SELECT pid FROM work_authors WHERE wid=?", (wid,)):
                pid = int(r["pid"])
                agg[pid] = agg.get(pid, 0.0) + s
                hits_per_pid[pid] = hits_per_pid.get(pid, 0) + 1
        ranked = sorted(agg.items(), key=lambda kv: -kv[1])[:limit]
        items = []
        for pid, s in ranked:
            row = conn.execute(
                "SELECT pid,name,last_inst,works_count,cited_by_count,confidence "
                "FROM people WHERE pid=?", (pid,)).fetchone()
            if row:
                items.append({**_person_brief(row), "match": round(s, 3),
                              "n_matched_works": hits_per_pid.get(pid, 0)})
        # 句子级证据：chunk 检索"排专家"被证伪（M6），但它是唯一能说明
        # "原文哪句话支撑结论"的通道，所以只用于引用，不参与排序。
        from app.search_chunks import passages_for
        for it in items[:3]:
            it["passages"] = passages_for(it["pid"], query, k=2)
        return {"tool": "find_expert", "items": items, "count": len(items),
                "ce_max": round(max(ce), 3) if ce else 0.0}
    finally:
        conn.close()


def _resolve_person(pid: int | None, name: str | None) -> dict | None:
    conn = connect()
    try:
        if pid:
            row = conn.execute("SELECT pid,name,name_zh,last_inst FROM people WHERE pid=?",
                               (pid,)).fetchone()
        elif name:
            hits = search_people(name, limit=1)
            row = (conn.execute("SELECT pid,name,name_zh,last_inst FROM people WHERE pid=?",
                                (hits[0][0],)).fetchone() if hits else None)
        else:
            row = None
        return dict(row) if row else None
    finally:
        conn.close()


def tool_profile(pid: int | None = None, name: str | None = None) -> dict:
    p = _resolve_person(pid, name)
    if not p:
        return {"tool": "profile", "items": [], "count": 0}
    prof = person_profile(int(p["pid"]))
    return {"tool": "profile", "items": [prof] if prof else [], "count": 1 if prof else 0}


def tool_collaborators(pid: int | None = None, name: str | None = None,
                       depth: int = 1, limit: int = 8) -> dict:
    """合作网络。M2 证明这类问题检索给不出答案，必须走图。"""
    p = _resolve_person(pid, name)
    if not p:
        return {"tool": "collaborators", "items": [], "count": 0}
    g = graph_neighbors(int(p["pid"]), depth=depth)
    nodes = [n for n in g["nodes"] if n.get("depth", 0) > 0]
    weights = {}
    for e in g["edges"]:
        if e["a"] == int(p["pid"]):
            weights[e["b"]] = e["weight"]
        elif e["b"] == int(p["pid"]):
            weights[e["a"]] = e["weight"]
    for n in nodes:
        n["weight"] = weights.get(n["pid"], 0)
    nodes.sort(key=lambda n: (-n.get("weight", 0), -(n.get("works_count") or 0)))
    return {"tool": "collaborators",
            "anchor": {"pid": p["pid"], "name": p["name"], "name_zh": p.get("name_zh")},
            "items": nodes[:limit], "count": len(nodes)}


def tool_works(query: str, limit: int = 5) -> dict:
    hits = search_works(query, limit=limit)
    conn = connect()
    try:
        items = []
        for wid, score in hits:
            row = conn.execute("SELECT wid,title,year,cites,venue FROM works WHERE wid=?",
                               (wid,)).fetchone()
            if row:
                items.append({**dict(row), "match": round(float(score), 3)})
        return {"tool": "works", "items": items, "count": len(items)}
    finally:
        conn.close()


def tool_task(query: str) -> dict:
    """把问题交给多步尽调任务（harness），返回它生成的报告。

    为什么聊天里要调它：审稿人匹配需要 researcher/analyst/verifier 三步协作，
    单轮工具做不到。这里只取最终报告，涉及的人作为引用。
    """
    from app.harness import run as run_task, vfs_read
    try:
        out = run_task(query)
    except Exception as exc:
        return {"tool": "task", "items": [], "count": 0,
                "note": f"任务执行失败：{type(exc).__name__}: {exc}"}
    report = vfs_read(out.get("report_path") or "") or ""
    return {"tool": "task", "count": 1,
            "items": [{"tid": out["tid"], "report": report,
                       "people": out.get("people") or [],
                       "cached": bool(out.get("cached"))}],
            "report_path": out.get("report_path")}


def tool_relation(name_a: str, name_b: str) -> dict:
    """两位专家之间的关系：直接共著？若有，给共同论文数；否则找最短合作链。"""
    a = _resolve_person(None, name_a)
    b = _resolve_person(None, name_b)
    if not a or not b:
        return {"tool": "relation", "items": [], "count": 0,
                "missing": [n for n, p in ((name_a, a), (name_b, b)) if not p]}
    pa, pb = int(a["pid"]), int(b["pid"])
    conn = connect()
    try:
        edge = conn.execute(
            """SELECT weight FROM edges WHERE kind='coauthor' AND
               ((a=? AND b=?) OR (a=? AND b=?))""", (pa, pb, pb, pa)).fetchone()
    finally:
        conn.close()
    if edge:
        return {"tool": "relation", "count": 1, "items": [{
            "a": a, "b": b, "direct": True, "shared": int(edge["weight"])}]}
    from app.graph_analytics import shortest_path
    sp = shortest_path(pa, pb, max_depth=4)
    return {"tool": "relation", "count": 1, "items": [{
        "a": a, "b": b, "direct": False, "shared": 0,
        "path": sp.get("path") or [], "hops": sp.get("hops")}]}


def tool_research_area(pid: int | None = None, name: str | None = None) -> dict:
    """研究方向：读深档案（全量成果），给出近五年/早期高频主题与指标。

    这是 M17 建档之后才可能回答的问题——单靠 FTS 检索不到"某人的研究方向"。
    """
    from app.dossiers import dossier
    from app.harness import _term_profile

    p = _resolve_person(pid, name)
    if not p:
        return {"tool": "research_area", "items": [], "count": 0}
    d = dossier(int(p["pid"]))
    if not d:
        return {"tool": "research_area", "items": [], "count": 0,
                "note": "该专家还没有深档案"}
    m = d["metrics"]
    last = m.get("last_year") or 2026
    pid_ = int(p["pid"])
    return {"tool": "research_area", "count": 1, "items": [{
        "pid": pid_, "name": d["name_zh"] or d["name"],
        "works": m.get("works"), "h_index": m.get("h_index"), "cites": m.get("cites_sum"),
        "first_year": m.get("first_year"), "last_year": last,
        "recent": _term_profile(pid_, last - 4, last + 1, top=8),
        "early": _term_profile(pid_, (m.get("first_year") or last), last - 5, top=8),
        "top_venues": (m.get("top_venues") or [])[:3]}]}


def tool_compare(names: list[str]) -> dict:
    """对比 2~3 位专家：作品数、被引、合作者数、主题交集。"""
    conn = connect()
    try:
        rows = []
        for nm in names[:3]:
            hits = search_people(nm, limit=1)
            if not hits:
                continue
            r = conn.execute(
                """SELECT p.pid,p.name,p.name_zh,p.last_inst,p.works_count,p.cited_by_count,
                          p.topics,(SELECT COUNT(*) FROM edges e
                                     WHERE e.a=p.pid OR e.b=p.pid) AS collab
                   FROM people p WHERE p.pid=?""", (hits[0][0],)).fetchone()
            if r:
                d = dict(r)
                d["topics"] = json.loads(d["topics"] or "[]")
                d["topics_zh"] = [label_topic(x) for x in d["topics"]]
                rows.append(d)
        return {"tool": "compare", "items": rows, "count": len(rows)}
    finally:
        conn.close()


# ---------------- 规划（确定性规则） ----------------
_CN_COLLAB = re.compile(r"合作|搭档|同僚|一起写|co-?author|collaborat|works? with|和谁", re.I)
_CN_PROFILE = re.compile(r"介绍|画像|简历|是谁|背景|profile|who is|about", re.I)
_CN_WORKS = re.compile(r"论文|文章|著作|paper|publication", re.I)
_CN_COMPARE = re.compile(r"对比|比较|谁更|差异|区别|compare|versus|vs\b", re.I)
# "研究方向/专长"类问题：交给档案（dossier），不是找专家
_CN_AREA = re.compile(r"研究方向|研究领域|方向|领域|专长|擅长|做什么研究|研究什么|学术画像", re.I)
# "关系/合作"类问题
_CN_REL = re.compile(r"关系|合作|共著|一起写|同事", re.I)
# 审稿类问题：必须优先于"比较"判断——"谁更适合审…"里含"谁更"，会被误判成比较
_CN_REVIEW = re.compile(r"审稿|审一篇|审这篇|评审|reviewer|referee", re.I)


def extract_names(query: str) -> list[str]:
    """从查询里抽"看起来像人名"的片段。

    - 英文：2~4 个首字母大写的词
    - 中文：先剥掉模板词（介绍/研究/专家…），剩下的 2~4 个连续汉字当人名
      （否则"介绍一下钟南山"会被当成找专家，然后因为库里的论文是英文而拒答）
    """
    q = (query or "")
    # 中文紧贴英文时（"钟南山和Yoshua Bengio"）\b 不会在汉字与字母之间成立，
    # 所以按汉字把查询切成若干英文片段，再在**每段内部**找英文人名；
    # 否则 "对比 A 和 B" 会被当成一个 4 词的人名。
    cands: list[str] = []
    for seg in re.split(r"[\u4e00-\u9fa5]+", q):
        cands += re.findall(r"\b([A-Z][a-zA-Z.'\-]+(?:\s+[A-Z][a-zA-Z.'\-]+){1,3})\b", seg)
    stripped = re.sub(r"(找研究|帮我找|查一下|帮我|有哪些人|有哪些|是什么|是谁|介绍一下|一下|"
                      r"介绍|研究|专家|学者|哪些|什么|方向|领域|相关|合作者|合作|论文|文章|"
                      r"近五年|近十年|最近五年|最近|近|五年|十年|几年|的研究方向|研究方向|"
                      r"更适合|适合|谁更|对比|比较|和|与|的|请|查|看)", " ", q)
    cands += re.findall(r"[\u4e00-\u9fa5]{2,4}", stripped)
    seen, out = set(), []
    for c in cands:
        c = c.strip()
        if not c or c.lower() in seen:
            continue
        cjk = all("\u4e00" <= ch <= "\u9fa5" for ch in c)
        # 英文名至少 5 个字符；中文名 2~4 个汉字都算（钟南山 只有 3 字）
        if (cjk and 2 <= len(c) <= 4) or (not cjk and len(c) > 4):
            seen.add(c.lower())
            out.append(c)
    return out


def latin_query(query: str) -> str:
    """抽取查询里的英文内容词。

    语料是英文，而用户会用中文提问（"找研究 graph neural network 的专家"）。
    cross-encoder 对混着中文的查询打分会被稀释：同一句话
    "graph neural network" 单独喂 → 0.999，混在中文里 → 0.175，直接导致误拒。
    """
    toks = re.findall(r"[A-Za-z][A-Za-z0-9\-']+", query or "")
    return " ".join(toks) if toks else (query or "")


def plan_node(state: AgentState) -> dict:
    q = state["query"]
    q_en = latin_query(q)
    names = extract_names(q)
    if _CN_REVIEW.search(q):
        # 交给多步尽调任务（researcher/analyst/verifier 协作），而不是单轮比较
        plan = [{"tool": "task", "args": {"query": q}}]
    elif _CN_COMPARE.search(q) and len(names) >= 2:
        plan = [{"tool": "compare", "args": {"names": names[:3]}}]
    # "研究方向/专长"：走档案（dossier），这是 M17 之后才有的能力
    elif names and _CN_AREA.search(q) and not _CN_WORKS.search(q):
        plan = [{"tool": "research_area", "args": {"name": names[0]}}]
    elif len(names) >= 2 and _CN_REL.search(q):
        plan = [{"tool": "relation", "args": {"name_a": names[0], "name_b": names[1]}}]
    elif names and _CN_REL.search(q):
        plan = [{"tool": "collaborators", "args": {"name": names[0]}}]
    elif _CN_COLLAB.search(q) and names:
        plan = [{"tool": "collaborators", "args": {"name": names[0]}}]
    elif _CN_PROFILE.search(q) and names:
        # 只有抽到了"像人名"的片段才走画像；否则"介绍某个领域"应走找专家
        plan = [{"tool": "profile", "args": {"name": names[0]}}]
    elif _CN_WORKS.search(q):
        plan = [{"tool": "works", "args": {"query": q_en}}]
    else:
        # q_en 是"只留拉丁字母"的版本，会把"中国/呼吸/机构是"这些中文条件全删掉。
        # 带结构化条件的查询必须传原文，否则 facet 只看到英文残片（踩过：
        # "中国呼吸专家，机构是 Capital Medical University" 被解析成主题=Capital Medical）。
        from app import facets as facet_mod
        _f = facet_mod.parse(q)
        _structured = any([_f.get("country"), _f.get("inst_kind"),
                           _f.get("inst_name"), _f.get("cites_min")])
        plan = [{"tool": "find_expert", "args": {"query": q if _structured else q_en}}]
    return {"plan": plan, "query_en": q_en}


def _run_tool(step: dict) -> dict:
    name, args = step["tool"], dict(step.get("args") or {})
    if name == "find_expert":
        return tool_find_expert(**args)
    if name == "profile":
        return tool_profile(**args)
    if name == "collaborators":
        return tool_collaborators(**args)
    if name == "task":
        return tool_task(**args)
    if name == "relation":
        return tool_relation(**args)
    if name == "research_area":
        return tool_research_area(**args)
    if name == "works":
        return tool_works(**args)
    if name == "compare":
        return tool_compare(**args)
    return {"tool": name, "items": [], "count": 0}


def execute_node(state: AgentState) -> dict:
    plan = state.get("plan") or []
    results = []
    for step in plan:
        results.append(_run_tool(step))
    return {"evidence": results}


# ---------------- 作答 ----------------
def _cite(kind: str, ref_id: int, label: str, extra: dict | None = None) -> dict:
    return {"kind": kind, "id": ref_id, "label": label, **(extra or {})}


def compose_node(state: AgentState) -> dict:
    ev = state.get("evidence") or []
    first = ev[0] if ev else {"tool": "none", "items": [], "count": 0}
    tool, items = first.get("tool"), first.get("items") or []

    if not items:
        return {
            "refused": True,
            "reason": f"{tool} 没有返回结果",
            "answer": ("知识库里没有找到能支撑这个问题的资料。\n"
                       f"（我尝试的工具：{tool}；如果这是合作关系类问题，"
                       "请给出具体人名，例如“谁和 Yoshua Bengio 合作过”。）"),
            "citations": [],
        }

    if tool == "find_expert":
        # facet 模式：先回显"我理解成了什么条件"，再给结果（用户可纠正）
        if first.get("mode") == "facet":
            r0 = first
            f = r0.get("facets") or {}
            label = {"country": "国家", "inst_kind": "机构类型", "inst_name": "机构",
                     "cites_min": "被引下限", "sort": "排序", "topic": "主题"}
            conds = "、".join(f"{label.get(k, k)}={v}" for k, v in f.items() if v)
            lines = [f"我把它理解成了结构化条件：**{conds or '（无）'}**。",
                     f"命中 **{r0.get('total', r0.get('count', 0))}** 人"
                     + (f"（{r0.get('relaxed')}）" if r0.get("relaxed") else "") + "："]
            cites = []
            for i, it in enumerate(r0.get("items", [])[:5], 1):
                cc = f"{it.get('country')} · " if it.get("country") else ""
                # works_count 是"样本内"收录篇数，不是全部论文数；
                # 直接写"N 篇 / 被引 M"会让人以为 3 篇被引 4.9 万，必须标注清楚
                lines.append(f"{i}. **{it.get('name_zh') or it.get('name')}** — "
                             f"{cc}{it.get('inst') or '机构未知'}；"
                             f"被引 {it.get('cited_by_count', 0):,}"
                             f"（样本内 {it.get('works_count', 0)} 篇）")
                cites.append(_cite("person", it["pid"], it.get("name_zh") or it.get("name"),
                                   {"country": it.get("country"), "inst": it.get("inst")}))
            if r0.get("notes"):
                lines.append("")
                lines.append("> 识别依据：" + "；".join(r0["notes"]))
            answer = "\n".join(lines)
        else:
            lines = ["找到以下相关专家（按相关度）："]
            cites = []
            for i, it in enumerate(items[:5], 1):
                lines.append(f"{i}. **{it['name']}** — {it.get('inst') or '机构未知'}；"
                             f"样本内 {it['works']} 篇 / 被引 {it['cites']:,}")
                for p in (it.get("passages") or [])[:1]:
                    snippet = (p.get("text") or "")[:110].replace("\n", " ")
                    lines.append(f"   > 证据（{p.get('year')}《{(p.get('title') or '')[:40]}》）：{snippet}…")
                cites.append(_cite("person", it["pid"], it["name"],
                                   {"inst": it.get("inst"), "works": it["works"],
                                    "passages": len(it.get("passages") or [])}))
            answer = "\n".join(lines)

    elif tool == "task":
        it = items[0] if items else {}
        answer = it.get("report") or it.get("note") or "这次任务没有产出报告。"
        if it.get("cached"):
            answer = "（这是上次的结论，语料未变，直接复用）\n\n" + answer
        cites = [_cite("person", p["pid"], p.get("name_zh") or p.get("name"))
                 for p in (it.get("people") or [])[:5]]
    elif tool == "relation":
        it = items[0]
        an = it["a"].get("name_zh") or it["a"]["name"]
        bn = it["b"].get("name_zh") or it["b"]["name"]
        if it.get("direct"):
            answer = (f"**{an}** 与 **{bn}** 是直接合作者："
                      f"共同署名 **{it['shared']}** 篇论文。")
            cites = [_cite("person", it["a"]["pid"], an),
                     _cite("person", it["b"]["pid"], bn)]
        elif it.get("path"):
            names_zh = []
            conn = connect()
            try:
                for pid in it["path"]:
                    r = conn.execute("SELECT name,name_zh FROM people WHERE pid=?",
                                     (pid,)).fetchone()
                    names_zh.append((r["name_zh"] or r["name"]) if r else str(pid))
            finally:
                conn.close()
            answer = (f"**{an}** 与 **{bn}** 没有直接共著，"
                      f"但存在 **{it['hops']} 跳**合作链：\n" + " → ".join(names_zh))
            cites = [_cite("person", it["a"]["pid"], an), _cite("person", it["b"]["pid"], bn)]
        else:
            answer = (f"**{an}** 与 **{bn}** 在库内 4 跳内没有连通路径——"
                      "说明他们不在同一条合作链上（或库内论文覆盖不足）。")
            cites = [_cite("person", it["a"]["pid"], an), _cite("person", it["b"]["pid"], bn)]
    elif tool == "profile":
        p = items[0]
        works = p.get("works") or []
        lines = [f"**{p['name_zh'] or p['name']}**（{p.get('inst_zh') or p.get('last_inst') or '机构未知'}）",
                 f"- 样本内作品：{p.get('works_count')} 篇；被引 {p.get('cited_by_count', 0):,}",
                 f"- 研究主题：{'、'.join((p.get('topics_zh') or p.get('topics')) or [])[:80] or '未标注'}"]
        if works:
            lines.append("- 代表作：")
            for w in works[:3]:
                lines.append(f"  - {w.get('year')}《{w.get('title')}》被引 {w.get('cites')}")
        answer = "\n".join(lines)
        cites = [_cite("person", p["pid"], p["name"])] + [
            _cite("work", w["wid"], (w.get("title") or "")[:60]) for w in works[:3]]

    elif tool == "collaborators":
        anchor = first.get("anchor") or {}
        lines = [f"与 **{anchor.get('name_zh') or anchor.get('name')}** 合作最多的学者（按共同论文数）："]
        cites = [_cite("person", anchor.get("pid", 0), anchor.get("name", ""))]
        for i, n in enumerate(items[:8], 1):
            lines.append(f"{i}. {n.get('name_zh') or n.get('name')} — {n.get('last_inst') or '机构未知'}；"
                         f"共同论文 {n.get('weight')} 篇")
            cites.append(_cite("person", n["pid"], n.get("name", "")))
        answer = "\n".join(lines)

    elif tool == "research_area":
        it = items[0]
        lines = [f"**{it['name']}** 的研究方向（基于全量 {it['works']} 篇论文）",
                 f"- h-index {it['h_index']} / 被引合计 {it['cites']:,} / "
                 f"{it['first_year']}–{it['last_year']}"]
        if it.get("recent"):
            lines.append("- 近五年高频主题：" +
                         "、".join(f"{w}({n})" for w, n in it["recent"]))
        if it.get("early"):
            lines.append("- 早期高频主题：" +
                         "、".join(f"{w}({n})" for w, n in it["early"]))
        if it.get("top_venues"):
            lines.append("- 高频期刊：" +
                         "、".join(f"{v[0]}({v[1]})" for v in it["top_venues"]))
        lines.append("- 说明：主题由论文标题词频统计得出，用于描述研究重心，不代表水平高低。")
        answer = "\n".join(lines)
        cites = [_cite("person", it["pid"], it["name"])]
    elif tool == "works":
        lines = ["相关论文："]
        cites = []
        for i, w in enumerate(items[:5], 1):
            lines.append(f"{i}. {w.get('year')}《{w.get('title')}》"
                         f"（{w.get('venue') or '未知来源'}，被引 {w.get('cites')}）")
            cites.append(_cite("work", w["wid"], (w.get("title") or "")[:60]))
        answer = "\n".join(lines)

    elif tool == "compare":
        lines = ["对比（样本内数据）："]
        for it in items:
            topics = (it.get("topics_zh") or it.get("topics") or [])[:3]
            lines.append(f"- **{it.get('name_zh') or it['name']}**：{it['works_count']} 篇 / "
                         f"被引 {it['cited_by_count']:,} / 合作者 {it['collab']} 人 / "
                         f"主题 {'、'.join(topics) or '未标注'}")
        answer = "\n".join(lines)
        cites = [_cite("person", it["pid"], it["name"]) for it in items]
    else:
        answer, cites = "（未支持的工具）", []

    return {"answer": answer, "citations": cites, "refused": False, "reason": ""}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("planner", plan_node)
    g.add_node("execute", execute_node)
    g.add_node("compose", compose_node)
    g.add_edge(START, "planner")
    g.add_edge("planner", "execute")
    g.add_edge("execute", "compose")
    g.add_edge("compose", END)
    return g.compile()


_GRAPH = None


def get_agent():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def answer(query: str, allow_fallback: bool = True) -> dict:
    out = get_agent().invoke({"query": query, "evidence": []})
    r = {"query": query, "plan": out.get("plan", []),
         "answer": out.get("answer", ""), "citations": out.get("citations", []),
         "refused": bool(out.get("refused")), "reason": out.get("reason", ""),
         # 工具的结构化结果（叙述层要用它讲"这一步拿到了什么"）
         "evidence": out.get("evidence") or []}
    # 多轮收窄：把"下一步还能怎么筛"带给调用方（/chat 与 SSE 都透传）
    ev0 = (r["evidence"] or [{}])[0]
    r["refinements"] = ev0.get("refinements") or []
    # 兜底路由：库内没有证据时改走公开网络检索，结果明确标注来源
    # allow_fallback=False 时不做兜底——Supervisor 需要看到"纯库内结果"才能仲裁
    if r["refused"] and allow_fallback:
        try:
            from app import websearch
            if websearch.available():
                fb = websearch.answer(query)
                if fb.get("ok"):
                    r.update(answer=fb["text"], citations=fb["citations"],
                             refused=False, fallback="anysearch",
                             reason="知识库无直接证据，已转 AnySearch 公开网络检索")
        except Exception as exc:      # 兜底失败就保持原拒答
            r["fallback_error"] = f"{type(exc).__name__}: {exc}"
    return r
