"""ScholarTrust FastAPI 入口：搜索 / 画像 / 图谱 / 复核 / Agent。

安全边界（M5 审计后确定）：
- 读接口公开（这是演示站的卖点）；
- **写接口必须带 X-API-Key**：`/review/decide` 能把候选直接发布进知识库，
  无鉴权等于任何人都能投毒；
- /docs /openapi.json 默认关闭（公网部署时它们等于攻击面清单）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent import answer as agent_answer
from app.experts import hero as experts_hero, top_experts
from app.labels import label_inst, label_topic
from app.graph_analytics import (community_of, institution_graph, shortest_path)
from app.dossiers import dossier as get_dossier, dossiers_summary
from app import hooks, memory
from app import facets as facet_mod
from app import institutions as inst_mod
from app import venues as venue_mod
from app.onboard import welcome as chat_welcome
from app.zh_names import missing as zh_missing, set_name_zh
from app.skills import list_skills, load_skill
from app.harness import get_task, list_tasks, run as run_task, vfs_read
from app.health import trust_health
from app.supervisor import run as supervise
from app.import_orgs import person_orgs
from app.db import connect, stats
from app.review import decide, list_queue, queue_stats
from app.search import (graph_neighbors, person_profile, search_dense, search_hybrid,
                        search_people, search_works)

API_KEY = os.environ.get("ST_API_KEY", "")
_DOCS = os.environ.get("ST_DOCS_ENABLED", "0") == "1"

app = FastAPI(
    title="ScholarTrust", version="0.5.0",
    description="学术专家知识库 + 可信采集 + Agent（M1–M5）",
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if WEB_DIR.exists():
    # 单文件前端，无构建步骤：直接由 FastAPI 托管
    app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
def index():
    f = WEB_DIR / "index.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="前端未部署")
    # no-store：前端改版后用户硬刷新前不会用到旧缓存（曾经因此看到白屏）
    return FileResponse(str(f), headers={"Cache-Control": "no-store"})

def require_key(x_api_key: str | None = Header(default=None)) -> None:
    """写接口鉴权。没配置 ST_API_KEY 时**拒绝**（fail-closed），不是放行。"""
    if not API_KEY:
        raise HTTPException(status_code=503, detail="服务未配置 ST_API_KEY，写操作已禁用")
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="X-API-Key 缺失或错误")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "stage": "M1"}


@app.get("/stats")
def get_stats() -> dict:
    conn = connect()
    try:
        return stats(conn)
    finally:
        conn.close()


@app.get("/people")
def people(q: str = Query("", description="关键词，匹配姓名/主题/机构"),
           limit: int = 20,
           status: str | None = "published",
           min_works: int = 2) -> dict:
    conn = connect()
    try:
        # 多取一些再按 min_works 过滤：单篇作者占样本 80%，默认不当作"专家"
        hits = search_people(q, limit=max(limit * 4, 40), status=status, conn=conn)
        out = []
        for pid, score in hits:
            row = conn.execute(
                "SELECT pid,name,name_zh,last_inst,topics,works_count,cited_by_count,confidence,"
                "headline,source FROM people WHERE pid=?", (pid,)).fetchone()
            if not row or (row["works_count"] or 0) < min_works:
                continue
            out.append({**dict(row), "score": round(score, 4)})
            if len(out) >= limit:
                break
        return {"query": q, "count": len(out), "items": out}
    finally:
        conn.close()


@app.get("/people/{pid:int}")
def person(pid: int):
    data = person_profile(pid)
    if not data:
        raise HTTPException(status_code=404, detail="person not found")
    return data


@app.get("/graph/{pid:int}")
def graph(pid: int, depth: int = 1, max_collaborators: int = 60):
    if depth < 1 or depth > 2:
        raise HTTPException(status_code=400, detail="depth 只支持 1~2")
    data = graph_neighbors(pid, depth=depth)
    # 组织层（某学术图谱来源的"平台/机构"合作）：与前端的虚线节点对应
    data["orgs"] = person_orgs(pid)
    # 高产作者的合作者可以上百人（钟南山有 111 位），全画出来看不清。
    # 按共著权重取前 N 位，同时把总数告诉前端，避免"看起来只有这么点人"的误解。
    nodes, edges = data["nodes"], data["edges"]
    if len(nodes) > max_collaborators + 1:
        weight: dict[int, int] = {}
        for e in edges:
            weight[e["a"]] = weight.get(e["a"], 0) + (e.get("weight") or 1)
            weight[e["b"]] = weight.get(e["b"], 0) + (e.get("weight") or 1)
        center = [n for n in nodes if n.get("depth") == 0]
        rest = sorted([n for n in nodes if n.get("depth") != 0],
                      key=lambda n: -(weight.get(n["pid"], 0)))
        keep = {n["pid"] for n in center + rest[:max_collaborators]}
        data["nodes"] = [n for n in nodes if n["pid"] in keep]
        data["edges"] = [e for e in edges if e["a"] in keep and e["b"] in keep]
        data["truncated"] = {"shown": len(data["nodes"]), "total": len(nodes)}
    return data


@app.get("/work/{wid}/citations")
def work_citations(wid: int, limit: int = 20) -> dict:
    """这篇论文引用了谁（库内展开标题，库外只给 openalex id）。"""
    from app import citations as cit
    return cit.refs_of(wid, limit=min(max(limit, 1), 100))


@app.get("/work/{wid}/cited-by")
def work_cited_by(wid: int, limit: int = 20) -> dict:
    """库里有哪些论文引用了它。"""
    from app import citations as cit
    return cit.citing_of(wid, limit=min(max(limit, 1), 100))


@app.get("/citations/top")
def citations_top(limit: int = 10) -> dict:
    """库内被引最多的论文（按库内引用边计，不是 OpenAlex 的总被引）。"""
    from app import citations as cit
    return {"items": cit.top_cited(limit=min(max(limit, 1), 50))}


@app.get("/works")
def works(q: str = Query("", description="关键词，匹配标题/摘要"),
          limit: int = 20) -> dict:
    conn = connect()
    try:
        hits = search_works(q, limit=limit, conn=conn)
        out = []
        for wid, score in hits:
            row = conn.execute(
                "SELECT wid,title,year,cites,venue,doi FROM works WHERE wid=?",
                (wid,)).fetchone()
            if row:
                out.append({**dict(row), "score": round(score, 4)})
        return {"query": q, "count": len(out), "items": out}
    finally:
        conn.close()


@app.get("/search")
def search(q: str = Query(..., description="查询词"),
           kind: str = Query("people", pattern="^(people|works)$"),
           mode: str = Query("hybrid", pattern="^(bm25|dense|hybrid)$"),
           fusion: str = Query("rrf", pattern="^(rrf|dbsf)$"),
           rrf_k: int = 60,
           country: str = Query("", description="国家（ISO2），仅 kind=people"),
           inst_kind: str = Query("", description="机构类型：university/hospital/institute/company"),
           cites_min: int = Query(0, description="被引下限"),
           sort: str = Query("relevance", pattern="^(relevance|cites|works)$",
                             description="relevance=按检索分；cites/works=筛选后再按该字段重排"),
           limit: int = 10) -> dict:
    """统一检索入口：单路（bm25/dense）或融合（hybrid + rrf/dbsf）。

    M2 的 A/B 就靠这几个参数在同一批 gold 查询上扫。

    facet 过滤只作用于 kind=people：先按相关度取回一批候选，再按
    国家/机构类型/被引**保序过滤**——不重排，避免把"相关度"换成"被引"。
    """
    faceted = kind == "people" and bool(country or inst_kind or cites_min)
    # 有筛选时要多取一些候选，否则筛完可能不够 limit 条
    fetch_n = min(300, max(limit * 6, 60)) if faceted else limit

    if mode == "bm25":
        hits = (search_people(q, limit=fetch_n) if kind == "people"
                else search_works(q, limit=fetch_n))
    elif mode == "dense":
        hits = search_dense(q, kind=kind, limit=fetch_n)
    else:
        hits = search_hybrid(q, kind=kind, limit=fetch_n, fusion=fusion, rrf_k=rrf_k)

    if faceted and hits:
        conds, args = [], []
        if country:
            conds.append("p.country = ?")
            args.append(country)
        if inst_kind:
            conds.append("p.inst_id IN (SELECT iid FROM institutions WHERE kind = ?)")
            args.append(inst_kind)
        if cites_min:
            conds.append("p.cited_by_count >= ?")
            args.append(int(cites_min))
        conn_f = connect()
        try:
            allowed = {r[0] for r in conn_f.execute(
                f"SELECT p.pid FROM people p WHERE {' AND '.join(conds)}", args)}
        finally:
            conn_f.close()
        hits = [(pid, s) for pid, s in hits if pid in allowed]

    # 排序开关：默认保持检索相关度；显式要 cites/works 时才重排。
    # 为什么默认不重排——筛选一开就按被引排，等于把"相关度"偷换成"影响力"。
    if kind == "people" and sort != "relevance" and hits:
        col = "cited_by_count" if sort == "cites" else "works_count"
        ids = [h[0] for h in hits]
        ph = ",".join("?" * len(ids))
        conn_s = connect()
        try:
            vals = {r[0]: (r[1] or 0) for r in conn_s.execute(
                f"SELECT pid,{col} FROM people WHERE pid IN ({ph})", ids)}
        finally:
            conn_s.close()
        hits.sort(key=lambda kv: -vals.get(kv[0], 0))

    hits = hits[:limit]

    conn = connect()
    try:
        items = []
        for ref_id, score in hits:
            if kind == "people":
                row = conn.execute(
                    "SELECT pid AS id,name,name_zh,last_inst,works_count,cited_by_count,"
                    "confidence,headline,source,country,country_src,topics "
                    "FROM people WHERE pid=?", (ref_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT wid AS id,title,year,cites,venue FROM works WHERE wid=?",
                    (ref_id,)).fetchone()
            if row:
                d = dict(row)
                if kind == "people":
                    d["inst_zh"] = label_inst(d.get("last_inst"))
                    try:
                        d["topics_zh"] = [label_topic(t) for t in json.loads(d.get("topics") or "[]")][:5]
                    except Exception:
                        d["topics_zh"] = []
                items.append({**d, "score": round(float(score), 6)})
        return {"query": q, "kind": kind, "mode": mode,
                "fusion": fusion if mode == "hybrid" else None,
                "sort": sort,
                "facets": {"country": country, "inst_kind": inst_kind,
                           "cites_min": cites_min} if kind == "people" else None,
                "count": len(items), "items": items}
    finally:
        conn.close()


# ---------------- 图分析（社区 / 路径 / 机构网） ----------------
@app.get("/graph/path")
def graph_path(a: int, b: int, max_depth: int = 4) -> dict:
    """两位专家的最短合作链（双向 BFS，最多 4 跳）。"""
    r = shortest_path(a, b, max_depth=max_depth)
    conn = connect()
    try:
        names = {}
        for pid in r.get("path") or []:
            row = conn.execute("SELECT name,last_inst,country FROM people WHERE pid=?", (pid,)).fetchone()
            names[pid] = dict(row) if row else {"name": str(pid)}
        return {**r, "people": [{"pid": p, **names[p]} for p in r.get("path") or []]}
    finally:
        conn.close()


@app.get("/graph/institutions")
def graph_institutions(top: int = 36) -> dict:
    """机构级合作网：节点=机构，边=共同署名论文数。"""
    return institution_graph(top=min(max(top, 8), 60))


@app.get("/graph/community/{pid}")
def graph_community(pid: int) -> dict:
    """某人所属的合作社区（标签传播划分）。"""
    return community_of(pid)

# ---------------- 专家墙（浏览/排序/筛选） ----------------
@app.get("/experts")
def experts(sort: str = Query("cites", pattern="^(cites|works|collab|recent|network)$"),
            topic: str = "", inst: str = "", source: str = "", endorse: str = "",
            country: str = "", inst_kind: str = "", cites_min: int = 0,
            near: int = 0,
            limit: int = 24, offset: int = 0,
            order: str = Query("desc", pattern="^(asc|desc)$")) -> dict:
    """发布专家的浏览接口：专家墙用。

    facet 筛选（与 /chat 的自然语言条件一致）：
      country    ISO2，如 CN / US
      inst_kind  university / hospital / institute / company
      cites_min  被引下限
    """
    return top_experts(sort=sort, topic=topic, inst=inst, source=source,
                       endorse=endorse, country=country, inst_kind=inst_kind,
                       cites_min=cites_min, near=near,
                       limit=limit, offset=offset, order=order)


@app.post("/task")
def task_run(q: str = Query(..., description="自然语言目标，如：比较钟南山和李兰娟近五年的研究方向差异"),
             refresh: bool = Query(False, description="忽略记忆缓存，强制重算")) -> dict:
    """多步任务：规划 → 逐步执行 → 产物落虚拟文件系统 → 返回报告与 trace。"""
    try:
        out = run_task(q, refresh=refresh)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    data = get_task(out["tid"]) or {}
    return {**out, "plan": data.get("plan"), "steps": data.get("steps"),
            "report": vfs_read(out["report_path"]) or ""}


@app.get("/memory")
def memory_stats() -> dict:
    """记忆层的规模与语料版本（缓存是否有效看 version）。"""
    return {**memory.cache_stats(), "version": memory.corpus_version()}


@app.get("/dossier/{pid:int}/changes")
def dossier_changes(pid: int, limit: int = 5) -> dict:
    """这位专家的档案变更记录（来自记忆层的快照 diff）。"""
    return {"pid": pid, "changes": memory.changes(pid, limit=min(max(limit, 1), 50))}

@app.get("/zh-names/missing")
def zh_names_missing(limit: int = 20) -> dict:
    """还没有中文名的专家（按被引排序），供人工补录。"""
    items = zh_missing(min(max(limit, 1), 100))
    return {"count": len(items), "items": items}


@app.post("/people/{pid:int}/name-zh")
def set_zh_name(pid: int, name_zh: str = Query(..., min_length=1, max_length=20),
                _: None = Depends(require_key)) -> dict:
    """补录中文名（写操作，需要 X-API-Key）。会同步更新搜索索引。"""
    try:
        return set_name_zh(pid, name_zh, src="user")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/skills")
def skills_index() -> dict:
    """技能索引（渐进式披露第一层：只有名称/触发条件/参数键）。"""
    items = list_skills()
    return {"count": len(items), "items": items}


@app.get("/skills/{name}")
def skill_detail(name: str) -> dict:
    s = load_skill(name)
    if not s:
        raise HTTPException(status_code=404, detail="skill not found")
    return s

@app.get("/audit")
def audit(tid: int = 0, limit: int = 50) -> dict:
    """审计日志：每次工具调用的脱敏参数、状态、耗时、成本。"""
    rows = hooks.audit_rows(tid or None, limit=min(max(limit, 1), 500))
    return {"count": len(rows), "items": rows,
            "summary": hooks.summary(tid) if tid else None}

@app.get("/tasks")
def tasks(limit: int = 20) -> dict:
    items = list_tasks(limit=min(max(limit, 1), 100))
    return {"count": len(items), "items": items}


@app.get("/task/{tid:int}")
def task_detail(tid: int) -> dict:
    t = get_task(tid)
    if not t:
        raise HTTPException(status_code=404, detail="task not found")
    if t.get("report_path"):
        t["report"] = vfs_read(t["report_path"]) or ""
    return t


@app.get("/trace/{tid:int}")
def trace(tid: int) -> dict:
    """链路追踪：每步的工具、耗时、产物。"""
    t = get_task(tid)
    if not t:
        raise HTTPException(status_code=404, detail="task not found")
    return {"tid": tid, "query": t["query"], "status": t["status"],
            "steps": t["steps"], "spans": t["spans"]}


@app.get("/vfs")
def vfs(path: str) -> dict:
    """读取虚拟文件系统里的产物（如 /reports/2.md）。"""
    content = vfs_read(path)
    if content is None:
        raise HTTPException(status_code=404, detail="file not found")
    return {"path": path, "bytes": len(content), "content": content}

@app.get("/venues")
def venues_list(top: int = 20, kind: str = "journal") -> dict:
    """期刊实体（从 works.venue 归一而来；仓储/预印本单列）。"""
    items = venue_mod.top_venues(limit=min(max(top, 1), 100), kind=kind)
    return {"count": len(items), "items": items}


@app.get("/venue/{vid:int}")
def venue_ep(vid: int, papers: int = 10) -> dict:
    d = venue_mod.venue_detail(vid, limit=min(max(papers, 1), 50))
    if not d:
        raise HTTPException(status_code=404, detail="venue not found")
    return d


@app.get("/institutions")
def institutions_list(top: int = 20, kind: str = "") -> dict:
    """机构实体（从 people.last_inst 归一而来）。"""
    items = inst_mod.top(limit=min(max(top, 1), 100), kind=kind)
    return {"count": len(items), "items": items}


@app.get("/institutions/duplicates")
def institutions_duplicates(limit: int = 20) -> dict:
    """近似重复机构候选（**只报告**，供人工确认；同名词序陷阱会标 same_tokens_confirm）。"""
    items = inst_mod.near_duplicates(limit=min(max(limit, 1), 100))
    return {"count": len(items), "items": items,
            "note": "自动合并只发生在归一化后完全一致的机构名；这里是待人工确认的候选对。"}


@app.get("/people/{pid:int}/affiliation")
def person_affiliation(pid: int) -> dict:
    """某位专家归一化后的机构 + 常发期刊（审稿人匹配的两个关键信号）。"""
    return {"pid": pid, "institution": inst_mod.for_person(pid),
            "venues": venue_mod.person_venues(pid, limit=5)}

@app.get("/experts/facets")
def experts_facets() -> dict:
    """可选的筛选维度（国家 / 机构类型）及各自人数，供前端做筛选条。"""
    return {"countries": facet_mod.facet_counts("country", 12),
            "inst_kinds": facet_mod.facet_counts("inst_kind", 8)}


@app.get("/dossiers")
def dossiers(limit: int = 100) -> dict:
    """已建档的专家（全量成果 + h-index 等指标）。"""
    return dossiers_summary(limit=min(max(limit, 1), 200))


@app.get("/dossier/{pid:int}")
def dossier_ep(pid: int, papers: int = 20) -> dict:
    d = get_dossier(pid, limit_papers=min(max(papers, 1), 200))
    if not d:
        raise HTTPException(status_code=404, detail="该专家还没有深档案")
    return d

@app.get("/health/trust")
def health_trust() -> dict:
    """数据健康度：网络背书占比、无作者论文、悬空边、FTS 一致性等。"""
    return trust_health()

@app.get("/health/history")
def health_history(limit: int = 20) -> dict:
    """最近几次健康检查（由 app.health_watch 定时写入 logs/health.jsonl）。"""
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "logs" / "health.jsonl"
    if not p.exists():
        return {"count": 0, "items": [], "alert": None}
    lines = p.read_text(encoding="utf-8").strip().splitlines()[-max(1, min(limit, 200)):]
    items = []
    for ln in lines:
        try:
            items.append(json.loads(ln))
        except Exception:
            continue
    alert = p.parent / "health_ALERT.txt"
    return {"count": len(items), "items": items,
            "alert": alert.read_text(encoding="utf-8") if alert.exists() else None}

@app.get("/experts/hero")
def experts_hero_ep() -> dict:
    return experts_hero()

# ---------------- 复核队列（可信采集的人工闭环） ----------------
@app.get("/review/queue")
def review_queue(limit: int = 20, min_score: float = 0.0) -> dict:
    """待审候选：按置信度降序。人工复核是可复现的前提。"""
    items = list_queue(limit=limit, min_priority=min_score)
    return {"count": len(items), "items": items, **queue_stats()}


@app.post("/review/decide")
def review_decide(pid: int, decision: str,
                  by: str = "demo-reviewer", note: str = "",
                  _: None = Depends(require_key)) -> dict:
    """接受 / 拒绝某条候选，写留痕。**写接口，需要 X-API-Key。**"""
    try:
        return decide(pid, decision, by=by, note=note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------- Agent（LangGraph + 工具 + 引用 + 拒答） ----------------
def _timed_supervise(q: str, sid: str) -> dict:
    """统一入口 + 耗时/来源透出（对标 AMiner 的 source_api_chain：
    用户应该能知道这个答案走了哪条路、花了多久）。"""
    import time as _t

    t0 = _t.perf_counter()
    r = supervise(q, sid=sid)
    r["elapsed_ms"] = round((_t.perf_counter() - t0) * 1000, 1)
    src = []
    if r.get("answer") and not r.get("kb_refused"):
        src.append("kb")
    if r.get("fallback"):
        src.append("web")
    r["sources"] = src
    return r


@app.post("/chat")
def chat(q: str = Query(..., description="自然语言问题"),
         sid: str = Query("", description="会话 id：多轮指代（那这些人呢）靠它找回上一轮结果")) -> dict:
    # Supervisor 调度：kb / task / web / hybrid，并仲裁合并
    return _timed_supervise(q, sid)


@app.get("/chat")
def chat_get(q: str = Query(..., description="自然语言问题（GET 别名，便于 curl 调试）"),
             sid: str = Query("")) -> dict:
    return _timed_supervise(q, sid)


@app.get("/agent/tools")
def agent_tools() -> dict:
    from app.config import CE_REFUSE
    return {"tools": [
        {"name": "find_expert", "desc": "按研究方向找专家（论文优先 + CE 判相关）"},
        {"name": "profile", "desc": "专家画像：作品数/被引/主题/代表作"},
        {"name": "collaborators", "desc": "合作网络（图遍历；检索做不到的）"},
        {"name": "works", "desc": "检索论文"},
        {"name": "compare", "desc": "对比 2~3 位专家"},
    ], "refuse_threshold": CE_REFUSE}


@app.get("/chat/welcome")
def chat_welcome_ep() -> dict:
    """开场引导：能问什么 + 示例 + 能力边界（纯数据，无模型调用）。"""
    return chat_welcome()

@app.get("/chat/stream")
async def chat_stream(q: str = Query(...), sid: str = Query("")):
    """SSE：思考叙述 → 逐字回答 → 引用 → 结束。

    没有 LLM，但流式的**形状和行为**与模型一致：先讲思路，再逐步吐字。
    叙述由 app/narrate.py 模板生成，每条都绑定真实工具结果，不编造。
    """
    import asyncio
    import json as _json

    from fastapi.responses import StreamingResponse

    from app import narrate

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"

    async def gen():
        # 先把意图判断出来（快），让用户立刻看到"我开始干活了"
        from app.agent import plan_node
        intent = {"compare": "compare_experts", "collaborators": "collaborators",
                  "relation": "relation", "profile": "expert_dossier",
                  "research_area": "expert_dossier", "works": "works",
                  "find_expert": "search"}.get(plan_node({"query": q})["plan"][0]["tool"], "search")
        yield sse("think", {"t": narrate.opening(q, intent)})

        r = await asyncio.to_thread(supervise, q, sid=sid)
        if r.get("fallback"):
            yield sse("think", {"t": "知识库里没有直接证据，我改查公开网络（结果会标注来源，未经本库核验）。"})
        # supervisor 合并后的结果不一定带 plan（纯网络/澄清路径就没有），要安全取值
        for p in (r.get("plan") or []):
            yield sse("think", {"t": narrate.step_narration("执行", p["tool"])})
        if r["refused"]:
            yield sse("think", {"t": narrate.closing(True, r.get("reason"))})
            yield sse("refused", {"reason": r["reason"], "answer": r["answer"]})
            yield sse("done", {"citations": 0})
            return

        # 逐字吐字（每 6 个字符一批，约 25ms —— 肉眼可见的"在写"）
        text = r["answer"]
        for i in range(0, len(text), 6):
            yield sse("token", {"t": text[i:i + 6]})
            await asyncio.sleep(0.025)
        yield sse("think", {"t": narrate.caveat(intent)})
        yield sse("citations", {"items": r["citations"],
                                 "refinements": r.get("refinements") or []})
        yield sse("think", {"t": narrate.closing(False, citations=len(r["citations"]))})
        yield sse("done", {"citations": len(r["citations"])})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={
                                 # 反代/CDN 常见的三种缓冲开关，全关掉
                                 "Cache-Control": "no-cache, no-store, no-transform",
                                 "X-Accel-Buffering": "no",
                                 "Content-Encoding": "identity",
                                 "Connection": "keep-alive",
                             })
