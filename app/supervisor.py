"""Supervisor：把问题分派给三个专用 Agent，并仲裁合并结果。

为什么需要它（M28 的路由审计）：单轮规划器 17 题里错 8 题——
"今天北京天气"被塞给 find_expert、"钟南山最近有什么新闻"只答了库内画像（混合问题答不了）。

架构（确定性调度，不用 LLM）：
    query → probe(能力探测) → decide(路由) → 执行 1~2 个 agent → arbiter(合并)

三个 agent：
    kb_agent   : 库内工具 + 模板（app.agent.answer，关闭内部兜底）
    task_agent : 多步尽调（app.harness.run，researcher/analyst/verifier 协作）
    web_agent  : AnySearch 公开网络（结果标注未核验）

仲裁原则：
    1. 库内能答就答，网络只作补充，**永远分节标注**，不混成一段；
    2. 库内拒答而网络有结果 → 只给网络，并说明为什么没走库；
    3. 两边都没有 → 明确拒答，不编。
"""
from __future__ import annotations

import re

from app import websearch

# 明显属于"库外"的领域词：出现即不必查库
_EXTERNAL = re.compile(
    r"天气|气温|股票|股价|汇率|比分|球赛|赛程|新闻|热搜|票房|航班|汇率|油价|"
    r"诺奖|诺贝尔|大选|选举|总统|选举|上市|融资|发布会|价格|多少钱|"
    r"足球|篮球|男足|女足|联赛|世界杯|奥运|比分|赛程|转会|mv|专辑|综艺|"
    r"weather|stock|price|score|news|election|release|match|league", re.I)

# 需要"库内学术信息 + 外部动态"的混合信号
_RECENCY = re.compile(r"最近|最新|近期|近期|今年|202[5-9]\s*年|动态|进展|新闻|newest|latest|recent", re.I)

_ARTICLE_WORDS = re.compile(r"这篇|稿件|投稿|审稿|评审|reviewer", re.I)


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fa5]", s or ""))


def probe(query: str) -> dict:
    """能力探测：能不能在库里定位实体？是不是明显库外？要不要最新动态？"""
    from app.agent import extract_names, _resolve_person

    names = extract_names(query)
    resolved = []
    for n in names[:3]:
        p = _resolve_person(None, n)
        if p:
            resolved.append({"pid": p["pid"], "name": p["name"],
                             "name_zh": p.get("name_zh")})
    return {
        "names": names,
        "resolved": resolved,
        "external": bool(_EXTERNAL.search(query or "")),
        "recency": bool(_RECENCY.search(query or "")),
        "article": bool(_ARTICLE_WORDS.search(query or "")),
    }


def decide(query: str) -> dict:
    """路由决策：返回 {route, agents, reason}。纯规则，可单测。"""
    from app.agent import plan_node

    p = probe(query)
    tool = (plan_node({"query": query})["plan"] or [{}])[0].get("tool", "find_expert")

    # 1) 审稿：必须走多步（researcher/analyst/verifier）
    if p["article"] or tool == "task":
        return {"route": "task", "agents": ["task"],
                "reason": "审稿类问题需要召回+算指标+领域校验"}

    # 2) 两个人名 + 比较：走多步出报告更完整
    if tool == "compare" and len(p["resolved"]) >= 2:
        return {"route": "task", "agents": ["task"],
                "reason": "两人对比交给多步任务，产出对照报告"}

    # 3) 有人名 + 要最新动态：混合（库内学术侧 + 网络动态）
    if p["resolved"] and p["recency"]:
        return {"route": "hybrid", "agents": ["kb", "web"],
                "reason": "既要库内成果，又要外部最新动态"}

    # 4) 有人名 + 画像/方向/关系：库内
    if p["resolved"]:
        return {"route": "kb", "agents": ["kb"],
                "reason": "库里能定位到人，走库内工具"}

    # 5) 要"最新/最近"但没有具体人物 → 静态语料答不了，直接网络
    if p["recency"] and not p["resolved"]:
        return {"route": "web", "agents": ["web"],
                "reason": "问的是最新动态，静态语料无法回答"}

    # 6) 明显库外（天气/股价/赛事…）：直接网络
    if p["external"]:
        return {"route": "web", "agents": ["web"],
                "reason": "问题属于公开信息，库内不会有"}

    # 7) 其余：先库内，答不出再由 run() 自动兜底（不单独成路由标签）
    return {"route": "kb", "agents": ["kb"],
            "reason": "主题类问题，先查库，不行再联网"}


def _merge(query: str, kb: dict | None, web: dict | None) -> dict:
    """仲裁合并：分节标注，绝不把两类证据混成一段。"""
    parts, cites = [], []
    if kb and not kb.get("refused") and kb.get("answer"):
        parts.append(kb["answer"])
        cites += (kb.get("citations") or [])
    if web and web.get("ok") and web.get("text"):
        if parts:
            parts.append("\n---\n**以下是公开网络补充（未经本库核验）**\n")
        parts.append(web["text"])
        cites += (web.get("citations") or [])

    if parts:
        return {"answer": "\n".join(parts), "citations": cites, "refused": False,
                # 多轮收窄按钮（来自 kb agent 的 facet 结果；纯网络结果没有）
                "refinements": (kb or {}).get("refinements") or []}
    reason = (kb or {}).get("reason") or "知识库与公开网络都没有找到可回答的内容。"
    return {"answer": f"资料不足，我不猜。{reason}", "citations": [],
            "refused": True, "reason": reason, "refinements": []}


def run(query: str, force_route: str | None = None, log=print, sid: str = "") -> dict:
    """执行一次调度：探测 → 决策 → 跑 agent → 仲裁。"""
    from app import context as ctx_mod

    ctx = ctx_mod.resolve(query, sid)
    if ctx.get("inherit"):
        # "那这些人呢"必须留在库内、并且只在那批人里排；交给路由判断会跑到网络
        d = {"route": "kb", "agents": ["kb"], "reason": "指代上一轮结果，沿用库内结果集"}
    elif ctx_mod.has_anaphora(query):
        return {"answer": ("我没法确定“这些人”指的是哪一批——上一个问题可能已过期，"
                           "或者换了会话。请把条件再说一次，例如“中国的呼吸科专家”。"),
                "citations": [], "refused": True, "reason": "指代无上下文",
                "refinements": []}
    else:
        d = decide(query)
    route = force_route or d["route"]
    log(f"    [supervisor] route={route}（{d['reason']}）")

    kb = web = None
    task_error = ""
    if route == "task":
        # task_agent：多步尽调（researcher/analyst/verifier），产出的是报告
        from app.harness import run as run_task, vfs_read
        try:
            t = run_task(query)
            report = vfs_read(t.get("report_path") or "") or ""
            kb = {"refused": not report, "answer": report,
                  "citations": [{"kind": "person", "id": p_["pid"],
                                 "label": p_.get("name_zh") or p_.get("name")}
                                for p_ in (t.get("people") or [])],
                  "plan": [{"tool": "task"}],
                  "reason": "" if report else "任务没有产出报告"}
        except Exception as exc:
            # 多步任务失败（常见原因：主题不在 102 人档案覆盖范围内）
            # → 降级到 kb→web 链路，不能把内部异常变成用户可见的错误
            task_error = f"{type(exc).__name__}: {exc}"
            kb = None
            route = "kb"
    if route in ("kb", "hybrid"):
        from app.agent import answer as kb_answer
        # 混合问题里库内这一半改用"规范化子问句"：
        # 直接拿"钟南山最近有什么新闻"查库搜不到（库里没有"新闻"这类内容），
        # 换成"介绍一下钟南山"才能命中画像与成果。
        kb_query = query
        if route == "hybrid":
            p_ = probe(query)
            who = ""
            if p_["resolved"]:
                r0 = p_["resolved"][0]
                who = r0.get("name_zh") or r0.get("name") or ""
            elif p_["names"]:
                who = p_["names"][0]
            if who:
                kb_query = "介绍一下" + who
        kb = kb_answer(kb_query, allow_fallback=False, sid=sid)
        if route == "kb" and kb.get("refused") and websearch.available():
            route = "web"
    if route in ("web", "hybrid"):
        if websearch.available():
            web = websearch.answer(query)

    merged = _merge(query, kb, web)
    merged.update({
        "query": query, "route": route, "agents": d["agents"],
        "route_reason": d["reason"],
        "kb_refused": bool((kb or {}).get("refused")),
        "web_used": bool(web and web.get("ok")),
        "plan": (kb or {}).get("plan") or [{"tool": "web"}],
        "task_error": task_error,
    })
    return merged


if __name__ == "__main__":
    print(run("钟南山最近有什么新闻"))
