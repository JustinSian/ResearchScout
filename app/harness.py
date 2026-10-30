"""Agent Harness 骨架：任务规划 + 虚拟文件系统 + 链路追踪。

为什么要有这一层：单个工具回答不了"比较两位专家近五年方向差异"——
需要解析实体 → 取档案 → 算指标 → 做差异 → 校验 → 出报告，至少 6 步。
把"多步"显式建成状态机，才能说清楚每一步做了什么、为什么拒答。

三个组件（都不引外部服务）：
  tasks   一条用户请求 = 一个 task，plan 落库，状态机 pending/running/done/failed
  steps   每一步：工具、参数、状态、耗时、产物路径
  vfs     虚拟文件系统：/dossiers/{pid}/works.jsonl、/reports/{task}.md
          大结果写文件，上下文只留摘要与路径（对应"上下文管理"）
  spans   链路追踪：每步起止时间与元数据，可用 /trace/{id} 回溯

规划器目前是**确定性规则**（不用 LLM）：意图可枚举、每一步可单测。
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import time
from collections import Counter
from pathlib import Path

from app.db import connect, init_db

VFS_ROOT = Path(__file__).resolve().parents[1] / "data" / "vfs"

DDL = [
    """CREATE TABLE IF NOT EXISTS tasks (
        tid INTEGER PRIMARY KEY,
        query TEXT NOT NULL,
        intent TEXT,
        plan_json TEXT,
        status TEXT DEFAULT 'pending',
        report_path TEXT,
        result_json TEXT,
        created_at REAL, updated_at REAL
    )""",
    """CREATE TABLE IF NOT EXISTS steps (
        sid INTEGER PRIMARY KEY,
        tid INTEGER NOT NULL,
        idx INTEGER NOT NULL,
        name TEXT, tool TEXT, args_json TEXT,
        status TEXT DEFAULT 'pending',
        summary TEXT, artifact TEXT,
        started_at REAL, ms INTEGER, error TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_steps ON steps(tid, idx)",
    """CREATE TABLE IF NOT EXISTS spans (
        span_id INTEGER PRIMARY KEY,
        tid INTEGER, sid INTEGER, kind TEXT, name TEXT,
        started_at REAL, ms INTEGER, meta_json TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_spans ON spans(tid)",
]

# 标题里不承载"研究方向"的词
_TITLE_STOP = set("""
a an the of in on for and or with without by to from at as is are was were be been
study studies analysis analyses trial trials randomized controlled review systematic
meta effect effects association associations impact impacts role roles results
patients patient clinical outcomes outcome risk factors factor among during after before
using based new novel approach approaches method methods model models data
""".split())


def init() -> None:
    init_db()
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    # 中间件的审计表也要建（否则 after_tool 会 no such table: audit）
    from app import hooks as _mw
    _mw.init()
    from app import memory as _mem
    _mem.init()
    VFS_ROOT.mkdir(parents=True, exist_ok=True)


# ---------------- 虚拟文件系统 ----------------
def vfs_write(tid: int, path: str, content: str) -> str:
    """写文件。path 形如 /reports/12.md；真实路径在 data/vfs 下。"""
    rel = path.lstrip("/")
    full = VFS_ROOT / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return path


def vfs_read(path: str) -> str | None:
    full = (VFS_ROOT / path.lstrip("/")).resolve()
    if not str(full).startswith(str(VFS_ROOT.resolve())) or not full.exists():
        return None
    return full.read_text(encoding="utf-8")


# ---------------- 实体与事实 ----------------
def _resolve(name: str) -> dict | None:
    from app.search import search_people
    hits = search_people(name, limit=1)
    if not hits:
        # 中文没有分词边界：抽取可能多吞一个字（"李兰娟近"），逐次去掉尾部重试
        for cut in (1, 2):
            if len(name) > cut + 1:
                hits = search_people(name[:-cut], limit=1)
                if hits:
                    break
        if not hits:
            return None
    pid = int(hits[0][0])
    conn = connect()
    try:
        r = conn.execute("SELECT pid,name,name_zh,last_inst,works_count,cited_by_count "
                         "FROM people WHERE pid=?", (pid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _dossier(pid: int) -> dict | None:
    from app.dossiers import dossier
    return dossier(pid, limit_papers=200)


def _term_profile(pid: int, since: int, until: int, top: int = 8) -> list[tuple[str, int]]:
    """用标题词频近似"研究方向"（没有逐篇概念标签时的替代方案）。"""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT title FROM dossier_work WHERE pid=? AND year BETWEEN ? AND ?",
            (pid, since, until)).fetchall()
    finally:
        conn.close()
    c: Counter[str] = Counter()
    for r in rows:
        for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", (r["title"] or "").lower()):
            if w in _TITLE_STOP or len(w) < 4:
                continue
            c[w] += 1
    return c.most_common(top)


# ---------------- 规划 ----------------
_CN_COMPARE = re.compile(r"(对比|比较|对比一下|差异|区别|vs\.?|versus)", re.I)
_CN_REVIEWER = re.compile(r"(审稿|审稿人|评审|审一篇|审这篇|reviewer|referee|编辑)", re.I)


def plan(query: str) -> dict:
    """确定性规划器：意图 → 步骤清单。每步都是 (name, tool, args)。"""
    q = query or ""
    names = _extract_names(q)
    if _CN_COMPARE.search(q) and len(names) >= 2:
        return {
            "intent": "compare_experts",
            "steps": [
                {"name": "解析专家 A", "tool": "resolve", "args": {"name": names[0]}},
                {"name": "解析专家 B", "tool": "resolve", "args": {"name": names[1]}},
                {"name": "载入档案 A", "tool": "dossier", "args": {"of": 0}},
                {"name": "载入档案 B", "tool": "dossier", "args": {"of": 1}},
                {"name": "近五年方向对比", "tool": "contrast", "args": {}},
                {"name": "证据校验", "tool": "verify", "args": {}},
                {"name": "生成报告", "tool": "report", "args": {}},
            ],
        }
    if _CN_REVIEWER.search(q):
        return {
            "intent": "reviewer_match",
            "steps": [
                {"name": "抽取稿件主题", "tool": "extract_topic", "args": {"query": q}},
                # 下面四步各自是一个子 Agent：只回结构化结论，大结果落 VFS
                {"name": "researcher 召回候选", "tool": "researcher", "args": {}},
                {"name": "analyst 算指标", "tool": "analyst", "args": {"top": 5}},
                {"name": "打分与利益冲突", "tool": "score", "args": {}},
                {"name": "verifier 证据校验", "tool": "verifier", "args": {}},
                {"name": "生成报告", "tool": "report", "args": {}},
            ],
        }
    if names:
        return {
            "intent": "expert_dossier",
            "steps": [
                {"name": "解析专家", "tool": "resolve", "args": {"name": names[0]}},
                {"name": "载入档案", "tool": "dossier", "args": {"of": 0}},
                {"name": "方向画像", "tool": "profile_terms", "args": {}},
                {"name": "生成报告", "tool": "report", "args": {}},
            ],
        }
    return {"intent": "search", "steps": [
        {"name": "检索", "tool": "search", "args": {"query": q}},
        {"name": "生成报告", "tool": "report", "args": {}},
    ]}


def _extract_names(q: str) -> list[str]:
    from app.agent import extract_names
    return extract_names(q)


# ---------------- 执行 ----------------
def _span(conn, tid, sid, kind, name, started, meta=None) -> None:
    conn.execute("INSERT INTO spans(tid,sid,kind,name,started_at,ms,meta_json) "
                 "VALUES(?,?,?,?,?,?,?)",
                 (tid, sid, kind, name, started, int((time.time() - started) * 1000),
                  json.dumps(meta or {}, ensure_ascii=False)))


def run(query: str, log=print, refresh: bool = False) -> dict:
    init()
    conn = connect()
    plan_ = plan(query)

    # ---- 记忆：同一问题（同语料版本）直接复用上次的产物 ----
    from app import memory
    mkey = memory.task_key(plan_["intent"], query)
    if not refresh:
        hit = memory.cache_get(mkey)
        if hit and vfs_read(hit.get("report_path") or ""):
            cur = conn.execute(
                "INSERT INTO tasks(query,intent,plan_json,status,report_path,result_json,"
                "created_at,updated_at) VALUES(?,?,?,'done',?,?,?,?)",
                (query, plan_["intent"], json.dumps(plan_["steps"], ensure_ascii=False),
                 hit["report_path"], json.dumps({"cached": True}, ensure_ascii=False),
                 time.time(), time.time()))
            tid_hit = int(cur.lastrowid)
            conn.execute("INSERT INTO steps(tid,idx,name,tool,args_json,status,summary,ms) "
                         "VALUES(?,?,?,?,?,'done',?,0)",
                         (tid_hit, 1, "记忆命中", "memory", "{}",
                          f"复用任务 #{hit.get('tid')} 的报告"))
            # 缓存命中时把原任务涉及的人也带出来，否则调用方拿不到引用
            src_people = []
            try:
                src = conn.execute("SELECT result_json FROM tasks WHERE tid=?",
                                   (hit.get("tid"),)).fetchone()
                if src:
                    src_people = (json.loads(src["result_json"] or "{}").get("people") or [])
            except Exception:
                src_people = []
            conn.commit()
            conn.close()
            log(f"    记忆命中：复用 #{hit.get('tid')} 的报告（语料未变）")
            return {"tid": tid_hit, "intent": plan_["intent"], "status": "done",
                    "report_path": hit["report_path"],
                    "artifacts": hit.get("artifacts") or [], "cached": True,
                    "source_tid": hit.get("tid"), "people": src_people}

    cur = conn.execute("INSERT INTO tasks(query,intent,plan_json,status,created_at,updated_at) "
                       "VALUES(?,?,?,'running',?,?)",
                       (query, plan_["intent"], json.dumps(plan_["steps"], ensure_ascii=False),
                        time.time(), time.time()))
    tid = int(cur.lastrowid)
    conn.commit()

    ctx: dict = {"people": [], "dossiers": [], "facts": [], "artifacts": []}

    def step_row(idx, name, tool, args):
        c = conn.execute("INSERT INTO steps(tid,idx,name,tool,args_json,status,started_at) "
                         "VALUES(?,?,?,?,?,'running',?)",
                         (tid, idx, name, tool, json.dumps(args, ensure_ascii=False), time.time()))
        conn.commit()
        return int(c.lastrowid)

    def finish(sid, status, summary, artifact=None, error=None, tool="", targs=None):
        ms = int((time.time() - (conn.execute(
            'SELECT started_at FROM steps WHERE sid=?', (sid,)).fetchone()[0])) * 1000)
        conn.execute("UPDATE steps SET status=?, summary=?, artifact=?, ms=?, error=? WHERE sid=?",
                     (status, summary, artifact, ms, error, sid))
        conn.commit()
        # 中间件：审计 + 成本核算（脱敏后落库）；默认取当前步骤的工具与参数
        try:
            from app import hooks as mw
            mw.after_tool(tid, sid, tool or ctx.get("_tool", "unknown"),
                          targs if targs is not None else (ctx.get("_args") or {}),
                          status, ms, artifact, summary, error, log=log)
        except Exception as exc:      # 审计失败不能影响主流程
            log(f"      [audit] 写入失败: {type(exc).__name__}: {exc}")

    try:
        for i, st in enumerate(plan_["steps"], 1):
            tool = st["tool"]
            targs = st.get("args") or {}
            ctx["_tool"], ctx["_args"] = tool, targs
            # 中间件：限流（在真正执行前拦截）
            try:
                from app import hooks as mw
                mw.before_tool(tool, targs)
            except Exception as exc:
                sid = step_row(i, st["name"], tool, targs)
                finish(sid, "failed", "被限流拦截", error=str(exc), tool=tool, targs=targs)
                raise RuntimeError(str(exc)) from exc
            sid = step_row(i, st["name"], tool, targs)
            t0 = time.time()
            summary, artifact = "", None

            if tool == "resolve":
                p = _resolve(st["args"]["name"])
                if not p:
                    finish(sid, "failed", f"未找到: {st['args']['name']}")
                    raise RuntimeError(f"未找到专家：{st['args']['name']}")
                ctx["people"].append(p)
                summary = f"{p['name_zh'] or p['name']}（pid={p['pid']}）"

            elif tool == "dossier":
                idx = st["args"].get("of", 0)
                p = ctx["people"][idx]
                d = _dossier(p["pid"])
                if not d:
                    finish(sid, "failed", "无深档案")
                    raise RuntimeError(f"{p['name']} 没有深档案，请先建档")
                ctx["dossiers"].append(d)
                # 大结果落盘，上下文只留摘要
                path = f"/dossiers/{p['pid']}/works.jsonl"
                vfs_write(tid, path, "\n".join(json.dumps(w, ensure_ascii=False)
                                               for w in d["papers"]))
                artifact = path
                ctx["artifacts"].append(path)
                summary = f"{d['works_loaded']} 篇 → {path}"

            elif tool == "extract_topic":
                from app.subagents import _keywords
                kws = _keywords(st["args"].get("query") or query, limit=6)
                ctx["topic"] = kws
                summary = "、".join(kws) or "（未抽出关键词）"

            elif tool == "researcher":
                from app.subagents import researcher
                out = researcher(" ".join(ctx.get("topic") or []) or query)
                out["tid"] = tid
                path = f"/agents/researcher/{tid}.json"
                vfs_write(tid, path, json.dumps(out, ensure_ascii=False))
                ctx["candidates"] = out["candidates"]
                artifact = path
                ctx["artifacts"].append(path)
                summary = f"{out['count']} 个候选 → {path}"

            elif tool == "analyst":
                from app.subagents import analyst
                top = int(st["args"].get("top") or 5)
                pids = [c["pid"] for c in (ctx.get("candidates") or [])[:top]]
                out = analyst(pids)
                path = f"/agents/analyst/{tid}.json"
                vfs_write(tid, path, json.dumps(out, ensure_ascii=False))
                ctx["analysis"] = out["authors"]
                artifact = path
                ctx["artifacts"].append(path)
                summary = f"{out['count']} 人指标 → {path}"

            elif tool == "score":
                # 打分口径：从 skills/reviewer-matching.md 读，改权重不用改代码
                topic = [t.lower() for t in (ctx.get("topic") or [])]
                from app.skills import param
                W = param("reviewer-matching", "weights",
                          {"topic": 0.5, "h": 0.3, "recent": 0.2})
                scored = []
                for a in ctx.get("analysis") or []:
                    if not a.get("has_dossier"):
                        continue
                    # 主题词是多词的（"lung cancer"），词频是单词的，必须按词拆开比对
                    words = set(w.lower() for w, _n in (a.get("recent_terms") or []))
                    words |= set(w.lower() for w, _n in (a.get("early_terms") or []))
                    topic_words = [x for t in topic for x in t.split() if len(x) > 2]
                    hit = sum(1 for w in set(topic_words) if w in words)
                    import math
                    s = (W["topic"] * (hit / max(len(topic), 1))
                         + W["h"] * min(1.0, math.log10(1 + (a.get("h_index") or 0)) / 2)
                         + W["recent"] * float(a.get("recent_5y_share") or 0))
                    scored.append({**a, "topic_hits": hit, "score": round(s, 3)})
                scored.sort(key=lambda x: -x["score"])
                ctx["scored"] = scored
                if not scored:
                    finish(sid, "failed", "没有可打分的候选人")
                    raise RuntimeError("没有可打分的候选人（候选人都没有深档案）")
                summary = " / ".join(f"{s['name']}({s['score']})" for s in scored[:3])

            elif tool == "verifier":
                from app.subagents import verifier
                claims = []
                for a in (ctx.get("scored") or [])[:3]:
                    claims.append({"kind": "metric", "pid": a["pid"],
                                   "field": "h_index", "value": a["h_index"]})
                    claims.append({"kind": "metric", "pid": a["pid"],
                                   "field": "works", "value": a["works"]})
                    if a.get("recent_terms"):
                        claims.append({"kind": "term", "pid": a["pid"],
                                       "term": a["recent_terms"][0][0]})
                    # 领域一致性：候选人的主题词必须与稿件主题有交集
                    claims.append({"kind": "domain", "pid": a["pid"], "topic": topic,
                                   "min_hits": param("reviewer-matching",
                                                     "require_domain_hits", 2)})
                out = verifier(claims)
                path = f"/agents/verifier/{tid}.json"
                vfs_write(tid, path, json.dumps(out, ensure_ascii=False))
                ctx["verification"] = out
                artifact = path
                ctx["artifacts"].append(path)
                summary = (f"{len(out['verified'])}/{out['total']} 条通过"
                           f"（{out['pass_rate']:.0%}）→ {path}")
                if out["rejected"]:
                    for r in out["rejected"][:3]:
                        log(f"      驳回：{r}")
                # 被 verifier 判定"领域不符"的人不进最终榜单
                ctx["rejected_pids"] = {r.get("pid") for r in out["rejected"]
                                        if r.get("kind") == "domain"}

            elif tool == "contrast":
                a, b = ctx["dossiers"][0], ctx["dossiers"][1]
                facts = []
                for d in (a, b):
                    m = d["metrics"]
                    last = m["last_year"] or 2026
                    recent = _term_profile(d["pid"], last - 4, last + 1, top=6)
                    early = _term_profile(d["pid"], (m["first_year"] or last) - 0, last - 5, top=6)
                    facts.append({"pid": d["pid"], "name": d["name_zh"] or d["name"],
                                  "works": m["works"], "h": m["h_index"],
                                  "cites": m["cites_sum"], "recent": recent, "early": early,
                                  "first_year": m["first_year"], "last_year": last,
                                  "recent_5y_share": m["recent_5y_share"]})
                ctx["facts"].append(("contrast", facts))
                summary = f"A 近五年词 {facts[0]['recent'][:3]} / B {facts[1]['recent'][:3]}"

            elif tool == "verify":
                # 校验：每条事实都必须能指回 pid 与具体数字
                ok = all(f.get("pid") and f.get("works") is not None
                         for _k, v in ctx["facts"] for f in v)
                ctx["facts"].append(("verify", {"passed": ok}))
                summary = "全部事实可回溯到 pid" if ok else "存在无证据事实"
                if not ok:
                    finish(sid, "failed", summary)
                    raise RuntimeError("证据校验未通过")

            elif tool == "report":
                path = f"/reports/{tid}.md"
                ctx["artifacts"].append(path)          # 先登记，报告里才能列全产物
                md = _render_report(query, plan_["intent"], ctx)
                vfs_write(tid, path, md)
                artifact = path
                conn.execute("UPDATE tasks SET report_path=? WHERE tid=?", (path, tid))
                summary = f"报告 {len(md)} 字符 → {path}"

            elif tool == "search":
                from app.search import search_people
                hits = search_people(st["args"]["query"], limit=5)
                conn2 = connect()
                try:
                    names = [dict(conn2.execute(
                        "SELECT pid,name,name_zh FROM people WHERE pid=?", (h[0],)).fetchone())
                        for h in hits]
                finally:
                    conn2.close()
                ctx["facts"].append(("search", names))
                summary = f"{len(names)} 个候选"

            else:
                summary = f"（未实现的工具 {tool}）"

            finish(sid, "done", summary, artifact)
            _span(conn, tid, sid, "tool", tool, t0, {"summary": summary})
            log(f"    [{i}/{len(plan_['steps'])}] {st['name']}: {summary}")

        conn.execute("UPDATE tasks SET status='done', result_json=?, updated_at=? WHERE tid=?",
                     (json.dumps({"artifacts": ctx["artifacts"],
                                  "people": [{"pid": pp["pid"], "name": pp.get("name"),
                                              "name_zh": pp.get("name_zh")}
                                             for pp in ctx.get("people") or []]},
                                 ensure_ascii=False),
                      time.time(), tid))
        conn.commit()
        # 记忆：把这次结果按"意图+问题"缓存，下次同问题直接复用
        try:
            memory.cache_put(mkey, {"tid": tid, "report_path": f"/reports/{tid}.md",
                                    "artifacts": ctx["artifacts"]})
        except Exception as exc:
            log(f"    [memory] 写缓存失败: {type(exc).__name__}: {exc}")
    except Exception as exc:
        conn.execute("UPDATE tasks SET status='failed', result_json=?, updated_at=? WHERE tid=?",
                     (json.dumps({"error": str(exc)}, ensure_ascii=False), time.time(), tid))
        conn.commit()
        conn.close()
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {"tid": tid, "intent": plan_["intent"], "status": "done",
            "report_path": f"/reports/{tid}.md", "artifacts": ctx["artifacts"],
            # 供前端联动：这次任务涉及的人（可直接跳到图谱/画像）
            "people": [{"pid": pp["pid"], "name": pp.get("name"),
                        "name_zh": pp.get("name_zh")} for pp in ctx.get("people") or []]}


def _render_report(query: str, intent: str, ctx: dict) -> str:
    lines = [f"# 尽调报告：{query}", "",
             f"- 意图：`{intent}`", f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M')}", ""]
    if ctx.get("topic"):
        lines += ["**稿件主题关键词**：" + "、".join(ctx["topic"]), ""]
    if ctx.get("scored"):
        ctx["facts"].append(("scored", ctx["scored"]))
    if ctx.get("verification"):
        ctx["facts"].append(("verification", ctx["verification"]))
    for kind, payload in ctx["facts"]:
        if kind == "contrast":
            a, b = payload
            lines += ["## 两位专家对照", "",
                      "| 指标 | " + a["name"] + " | " + b["name"] + " |",
                      "|---|---|---|",
                      f"| 全量论文 | {a['works']} | {b['works']} |",
                      f"| h-index | {a['h']} | {b['h']} |",
                      f"| 被引合计 | {a['cites']:,} | {b['cites']:,} |",
                      f"| 年份跨度 | {a['first_year']}–{a['last_year']} | "
                      f"{b['first_year']}–{b['last_year']} |",
                      f"| 近五年产出占比 | {a['recent_5y_share']:.0%} | {b['recent_5y_share']:.0%} |",
                      "",
                      f"**{a['name']} 近五年高频词**：" +
                      "、".join(f"{w}({n})" for w, n in a["recent"][:6]),
                      "",
                      f"**{b['name']} 近五年高频词**：" +
                      "、".join(f"{w}({n})" for w, n in b["recent"][:6]),
                      "",
                      f"**{a['name']} 早期高频词**：" +
                      "、".join(f"{w}({n})" for w, n in a["early"][:6]),
                      "",
                      f"**{b['name']} 早期高频词**：" +
                      "、".join(f"{w}({n})" for w, n in b["early"][:6]),
                      ""]
        elif kind == "scored":
            bad = ctx.get("rejected_pids") or set()
            ranked = [s for s in payload if s["pid"] not in bad]
            dropped = [s for s in payload if s["pid"] in bad]
            lines += ["## 候选人打分（主题命中 0.5 / h-index 0.3 / 近五年活跃 0.2）", "",
                      "| 排名 | 专家 | h-index | 全量论文 | 主题命中 | 得分 |",
                      "|---|---|---|---|---|---|"]
            for i, s in enumerate(ranked, 1):
                inst = (s.get("inst") or "—")[:18]
                lines.append(f"| {i} | {s['name']}（{inst}） | {s['h_index']} | "
                             f"{s['works']} | {s['topic_hits']} | {s['score']} |")
            lines.append("")
            if dropped:
                lines.append("**被 verifier 驳回（领域不符，不计入榜单）**：" +
                             "、".join(f"{s['name']}（{s['topic_hits']} 词）" for s in dropped))
                lines.append("")
            for s in ranked[:3]:
                terms = "、".join(f"{w}({n})" for w, n in (s.get("recent_terms") or [])[:6])
                lines.append(f"**{s['name']}** 近五年高频主题：{terms}")
            lines.append("")
        elif kind == "verification":
            v = payload
            lines += ["## 证据校验（verifier 子 Agent）", "",
                      f"- 提交 {v['total']} 条，通过 {len(v['verified'])} 条，"
                      f"通过率 {v['pass_rate']:.0%}"]
            for r in v["rejected"][:5]:
                lines.append(f"- 驳回：{r.get('kind')} pid={r.get('pid')} → {r.get('why')}")
            lines.append("")
        elif kind == "verify":
            lines += ["## 证据校验", "",
                      "全部数字均可回溯到 pid 与档案条目。" if payload["passed"]
                      else "存在无法回溯的事实。", ""]
        elif kind == "search":
            lines += ["## 候选专家", ""]
            for n in payload:
                lines.append(f"- {n.get('name_zh') or n.get('name')}（pid={n['pid']}）")
            lines.append("")
    lines += ["## 产物", ""]
    for a in ctx["artifacts"]:
        lines.append(f"- `{a}`")
    lines += ["", "> 词频是「研究方向」的近似：没有逐篇概念标签时，用标题词频做替代，"
                  "只用于描述性对比，不用于判定水平高低。"]
    return "\n".join(lines)


def get_task(tid: int) -> dict | None:
    conn = connect()
    try:
        t = conn.execute("SELECT * FROM tasks WHERE tid=?", (tid,)).fetchone()
        if not t:
            return None
        steps = [dict(r) for r in conn.execute(
            "SELECT idx,name,tool,status,summary,artifact,ms,error FROM steps WHERE tid=? ORDER BY idx",
            (tid,))]
        spans = [dict(r) for r in conn.execute(
            "SELECT sid,kind,name,ms FROM spans WHERE tid=? ORDER BY span_id", (tid,))]
        out = dict(t)
        out["steps"], out["spans"] = steps, spans
        if out.get("plan_json"):
            out["plan"] = json.loads(out["plan_json"])
        try:
            res = json.loads(out.get("result_json") or "{}")
            out["people"] = res.get("people") or []
        except Exception:
            out["people"] = []
        return out
    finally:
        conn.close()


def list_tasks(limit: int = 20) -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT tid,query,intent,status,report_path,created_at FROM tasks "
            "ORDER BY tid DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "比较钟南山和李兰娟近五年的研究方向差异"
    print(f"Q: {q}")
    print(run(q))
