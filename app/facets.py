"""Facet 解析与筛选检索：把自然语言变成结构化条件。

对标 AMiner 智能对话助手的思路：用户说"中国的呼吸科专家"，
真正的做法不是把这个句子丢进向量检索，而是拆成
  {topic: 呼吸, country: CN, sort: cites}
再走"过滤 + 排序"，并把识别到的条件**回显给用户**（可纠正）。

支持的维度：
  topic       研究主题（中英映射后走 FTS）
  country     国家/地区（ISO2，来自 OpenAlex country_code）
  inst_kind   机构类型（university / hospital / institute / company）
  inst_name   机构名片段（如"清华"→ Tsinghua）
  cites_min   被引下限（"高被引"/"被引超过 5000"）
  sort        排序（cites / works / recent）
  limit       条数（"前 10"）

设计原则：**能解析才解析，解析不到就当普通检索词**，绝不硬猜。
"""
from __future__ import annotations

import json
import re

from app.db import connect

# 国家/地区：中文与英文 → ISO2（只列我们数据里可能出现的）
COUNTRY = {
    "中国": "CN", "国内": "CN", "china": "CN", "chinese": "CN",
    "美国": "US", "usa": "US", "united states": "US", "american": "US",
    "英国": "GB", "uk": "GB", "united kingdom": "GB", "england": "GB", "britain": "GB",
    "德国": "DE", "germany": "DE", "german": "DE",
    "法国": "FR", "france": "FR", "french": "FR",
    "日本": "JP", "japan": "JP", "japanese": "JP",
    "加拿大": "CA", "canada": "CA",
    "澳大利亚": "AU", "澳洲": "AU", "australia": "AU",
    "荷兰": "NL", "netherlands": "NL",
    "瑞士": "CH", "switzerland": "CH",
    "新加坡": "SG", "singapore": "SG",
    "韩国": "KR", "korea": "KR",
    "印度": "IN", "india": "IN",
    "意大利": "IT", "italy": "IT",
    "西班牙": "ES", "spain": "ES",
    "瑞典": "SE", "sweden": "SE",
    "巴西": "BR", "brazil": "BR",
    "香港": "HK", "hong kong": "HK",
    "台湾": "TW", "taiwan": "TW",
}

# 机构类型
INST_KIND = [
    ("hospital", re.compile(r"医院|附院|医学中心|临床|hospital|clinic|medical cent", re.I)),
    ("university", re.compile(r"大学|高校|学院|学校|university|college|school", re.I)),
    ("institute", re.compile(r"研究所|研究院|实验室|研究中心|institute|laborator|academy", re.I)),
    ("company", re.compile(r"企业|公司|工业界|业界|大厂|company|labs?\b|inc\b|corp\b|industry", re.I)),
]

# 机构名片段（中文 → 英文关键词）
INST_NAME = {
    "清华": "Tsinghua", "北大": "Peking University", "浙江大学": "Zhejiang University",
    "复旦": "Fudan", "上海交大": "Shanghai Jiao Tong", "中山大学": "Sun Yat-sen",
    "广医": "Guangzhou Medical", "广州医科大学": "Guangzhou Medical",
    "协和": "Peking Union Medical", "中科院": "Chinese Academy of Sciences",
    "哈佛": "Harvard", "麻省理工": "MIT", "斯坦福": "Stanford", "牛津": "Oxford",
    "剑桥": "Cambridge", "东京大学": "University of Tokyo", "新加坡国立": "National University of Singapore",
    "google": "Google", "微软": "Microsoft", "deepmind": "DeepMind", "openai": "OpenAI",
}

SORT_WORDS = [
    ("recent", re.compile(r"最近活跃|近年活跃|近期|最新|recent", re.I)),
    ("works", re.compile(r"论文最多|作品最多|产出最多|发表最多|most works|most papers", re.I)),
    ("cites", re.compile(r"引用最多|引用最高|被引最多|被引最高|影响力最大|影响力|权威|大牛|top|leading", re.I)),
]

HIGH_CITES = re.compile(r"高被引|高引|大牛|顶尖|顶流|影响力大|famous|highly cited", re.I)

_CN_D = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
         "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_U = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def to_int(tok: str) -> int | None:
    """'500' / '五百' / '一千五百' → int；解析不出返回 None。

    用户口语里经常用中文数字（"被引五百以上"），只认阿拉伯数字会把这类句子整句漏掉。
    """
    tok = (tok or "").strip()
    if not tok:
        return None
    if tok.isdigit():
        return int(tok)
    total = section = number = 0
    for ch in tok:
        if ch in _CN_D:
            number = _CN_D[ch]
        elif ch in _CN_U:
            unit = _CN_U[ch]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = number = 0
            else:
                if number == 0:
                    number = 1
                section += number * unit
                number = 0
        else:
            return None
    return total + section + number or None


_NUM = r"(?:\d{2,7}|[一二两三四五六七八九十百千万]{1,6})"
# 同一个意图有多种说法，都要认：“被引超过 500” / “被引 500 以上” / “被引 500+” /
# “被引至少 500” / “引用不低于 500” / “被引超 500” / “被引五百以上”。
# 只支持一种写法 = 线上换个说法就失效。
CITES_NUM = re.compile(
    r"(?:被引|引用)(?:数|量|次数)?\s*(?:"
    r"(?:超过|超|大于|不低于|不少于|至少|>=|≥|>)\s*(" + _NUM + r")"
    r"|(" + _NUM + r")\s*(?:篇)?\s*(?:以上|及以上|或以上|\+|起)"
    r")")
TOP_N = re.compile(r"(?:前|top)\s*(\d{1,3}|[一二两三四五六七八九十百]{1,3})", re.I)

# 解析时要剥掉的功能词，避免它们污染主题
STOP_PHRASES = re.compile(
    r"帮我找|帮我|找一下|找|查一下|查|列出|推荐|介绍|有哪些|有哪些人|哪些|谁|什么|"
    r"专家|学者|教授|研究员|医生|老师|人才|的人|人员|"
    r"中国|国内|美国|英国|德国|法国|日本|加拿大|澳大利亚|澳洲|荷兰|瑞士|新加坡|韩国|印度|意大利|西班牙|瑞典|巴西|香港|台湾|"
    r"大学|高校|学院|医院|研究所|研究院|实验室|企业|公司|工业界|业界|"
    r"高被引|顶尖|大牛|影响力|"
    r"引用最多|论文最多|最近活跃|近年活跃|"
    r"一下|的|在|做|从事|研究|学科|科|系|专业|领域|方向|里|中|条|大陆|大夫|几个|几位|几名|一些|就行|即可|也可以|就可以|都可以|都行|也行|都成|左右|个|位|名|人", re.I)


def parse(query: str) -> dict:
    """自然语言 → 结构化条件 + 回显说明。解析不到的条件保持 None。"""
    q = (query or "").strip()
    facets: dict = {"topic": "", "country": None, "inst_kind": None,
                    "inst_name": None, "cites_min": None, "sort": "cites", "limit": 10}
    notes: list[str] = []

    for zh, iso in COUNTRY.items():
        if zh in q.lower():
            facets["country"] = iso
            notes.append(f"国家={iso}")
            break

    for kind, rx in INST_KIND:
        if rx.search(q):
            facets["inst_kind"] = kind
            notes.append(f"机构类型={kind}")
            break

    # 显式语法："机构是 Fudan University"——refinement 按钮生成的就是这种句子，
    # 必须能重新解析回来，否则"继续筛选"点一次就没法再点了
    m_inst = re.search(r"机构(?:是|为)\s*([^，,。;；]+)", q)
    if m_inst:
        facets["inst_name"] = m_inst.group(1).strip()
        notes.append("机构=" + facets["inst_name"])
    else:
        for zh, en in INST_NAME.items():
            if zh in q.lower():
                facets["inst_name"] = en
                notes.append("机构≈" + en)
                break

    if HIGH_CITES.search(q):
        facets["cites_min"] = 1000
        notes.append("被引≥1000")
    m = CITES_NUM.search(q)
    if m:
        # 两个分支：前置比较词（被引超过 500）/ 后置范围词（被引 500 以上）
        _v = to_int(m.group(1) or m.group(2))
        if _v:
            facets["cites_min"] = _v
        notes.append(f"被引≥{m.group(1)}")

    for name, rx in SORT_WORDS:
        if rx.search(q):
            facets["sort"] = name
            notes.append(f"排序={name}")
            break

    m = TOP_N.search(q)
    if m:
        _n = to_int(m.group(1))
        if _n:
            facets["limit"] = max(1, min(_n, 50))
            notes.append(f"取前{_n}")

    # 把已识别的条件短语从主题里剥掉，避免"呼吸科 ，被引超过 1000，前 5"这种垃圾主题
    rest = q
    rest = CITES_NUM.sub(" ", rest)
    rest = re.sub(r"被引(?:数|量)?\s*(?:超过|大于|>|>=)?\s*\d{2,7}", " ", rest)
    rest = TOP_N.sub(" ", rest)
    rest = HIGH_CITES.sub(" ", rest)
    for zh in COUNTRY:
        rest = re.sub(re.escape(zh), " ", rest, flags=re.I)
    for _kind, rx in INST_KIND:
        rest = rx.sub(" ", rest)
    for zh in INST_NAME:
        rest = rest.replace(zh, " ")
    rest = re.sub(r"机构(?:是|为)\s*[^，,。;；]+", " ", rest)
    for _name, rx in SORT_WORDS:
        rest = rx.sub(" ", rest)
    topic = STOP_PHRASES.sub(" ", rest)
    topic = re.sub(r"[，,。;；:：!?！？、]+", " ", topic)
    topic = re.sub(r"\s+", " ", topic).strip()
    facets["topic"] = topic
    facets["topic_en"] = topic_en(topic)
    if topic:
        notes.append(f"主题≈{topic}")
    facets["notes"] = notes
    return facets


def topic_en(topic: str, max_terms: int = 3) -> list[str]:
    """中文主题 → 英文检索词（语料是英文，不翻译基本搜不到）。"""
    if not topic:
        return []
    try:
        from app.subagents import _keywords
        terms = [t for t in _keywords(topic, limit=max_terms) if t]
    except Exception:
        terms = []
    # 缩写单独处理：词表按整词匹配，"AI"/"ML" 这类两字母词既进不了词表也进不了英文分词
    low = (topic or "").lower()
    if re.search(r"\bai\b", low) and "artificial intelligence" not in terms:
        terms.insert(0, "artificial intelligence")
    if re.search(r"\bml\b", low) and "machine learning" not in terms:
        terms.insert(0, "machine learning")
    return terms[:max_terms]


def search(facets: dict, limit: int = 0) -> dict:
    """按 facets 过滤 + 排序，返回命中列表与条件回显。"""
    from app.search import _fts_query

    limit = limit or facets.get("limit") or 10
    where = ["p.status = 'published'"]
    args: list = []

    topic = (facets.get("topic") or "").strip()
    terms = facets.get("topic_en") or topic_en(topic)
    # RSI 策略注入：当前策略里的查询扩展词（M53）。没有策略时是空列表。
    try:
        from app import rsi as _rsi
        _extra = _rsi.synonyms_for(topic)
        if _extra:
            _base = list(terms or [])
            terms = _base + [x for x in _extra if x not in _base]
    except Exception:
        pass
    # 同义词之间必须是 OR：respiratory 与 pulmonary 同时出现在同一个人身上概率很低，
    # 用 AND 会把「中国的呼吸科专家」查成 0 人（踩过）。
    match_expr = " OR ".join(f"({_fts_query(t)})" for t in terms if _fts_query(t)) if terms else ""
    if match_expr:
        where.append("p.pid IN (SELECT rowid FROM people_fts WHERE people_fts MATCH ?)")
        args.append(match_expr)

    if facets.get("country"):
        where.append("p.country = ?")
        args.append(facets["country"])

    if facets.get("inst_kind"):
        where.append("i.kind = ?")
        args.append(facets["inst_kind"])

    if facets.get("inst_name"):
        where.append("i.name LIKE ?")
        args.append(f"%{facets['inst_name']}%")

    if facets.get("cites_min"):
        where.append("p.cited_by_count >= ?")
        args.append(int(facets["cites_min"]))

    # 上下文继承：只在上一轮命中的那批人里筛/排（"那这些人里被引最高的是谁"）
    pids = facets.get("pids") or []
    if pids:
        marks = ",".join("?" * len(pids[:200]))
        where.append(f"p.pid IN ({marks})")
        args += [int(x) for x in pids[:200]]

    order = {"cites": "p.cited_by_count DESC",
             "works": "p.works_count DESC",
             "recent": "p.updated_at DESC"}.get(facets.get("sort") or "cites",
                                                "p.cited_by_count DESC")

    sql = f"""
        SELECT p.pid, p.name, p.name_zh, p.country, p.works_count, p.cited_by_count,
               p.updated_at, p.topics, p.source,
               i.name AS inst, i.kind AS inst_kind
        FROM people p LEFT JOIN institutions i ON i.iid = p.inst_id
        WHERE {' AND '.join(where)}
        ORDER BY {order}
        LIMIT ?
    """
    conn = connect()
    try:
        # 多取一些候选，留给"主题命中数"重排（AMiner 的三层权重简化版：
        # high=主题词必含（已在 FTS/过滤里）、middle=国家/机构类型硬条件、
        # low=同义词命中数用于加分），否则按被引排会把擦边的人排前面。
        fetch_n = limit if not terms else min(200, max(limit * 4, 40))
        rows = [dict(r) for r in conn.execute(sql, (*args, fetch_n))]
        total = conn.execute(
            f"SELECT COUNT(*) FROM people p LEFT JOIN institutions i ON i.iid = p.inst_id "
            f"WHERE {' AND '.join(where)}", args).fetchone()[0]
    finally:
        conn.close()

    # 主题命中重排：这就是 AMiner "topic_high/middle/low" 在本数据上的等价物——
    # 必含条件由 SQL 过滤承担（high），机构/国家是硬条件（middle），
    # 这里算的是"主题覆盖度"（low）：概念 + 论文标题各命中多少。
    # 只看 topics 不够：OpenAlex 的概念很粗（"Medicine"），同义词基本不共现，
    # 实测所有人都是 1 分，等于没排序。加上论文标题命中才分得开。
    if terms and rows:
        def _topic_hits(r: dict) -> int:
            t = (r.get("topics") or "").lower()
            return sum(1 for x in terms if x and str(x).lower() in t)

        pids = [r["pid"] for r in rows]
        ph = ",".join("?" * len(pids))
        like = " OR ".join(["w.title LIKE ?"] * len(terms))
        wargs = [f"%{x}%" for x in terms]
        work_hits: dict[int, int] = {}
        try:
            conn_w = connect()
            try:
                for row in conn_w.execute(
                        f"""SELECT wa.pid, COUNT(*) AS n
                            FROM work_authors wa JOIN works w ON w.wid = wa.wid
                            WHERE wa.pid IN ({ph}) AND ({like})
                            GROUP BY wa.pid""", (*pids, *wargs)):
                    work_hits[int(row[0])] = int(row[1])
            finally:
                conn_w.close()
        except Exception:
            work_hits = {}

        secondary = {"cites": "cited_by_count", "works": "works_count",
                     "recent": "updated_at"}.get(facets.get("sort") or "cites",
                                                 "cited_by_count")
        for r in rows:
            r["topic_hits"] = _topic_hits(r)
            r["work_hits"] = work_hits.get(int(r["pid"]), 0)

        def _key(r: dict):
            # 稳定排序：同分时保留 SQL 里的原顺序（recent 模式就靠它）
            if secondary == "updated_at":
                return (-r["topic_hits"], -r["work_hits"])
            return (-r["topic_hits"], -r["work_hits"], -(r.get(secondary) or 0))

        rows.sort(key=_key)
        rows = rows[:limit]
        for r in rows:
            r.pop("topics", None)      # 只用于算命中数，别塞进响应里

    # FTS 没命中但确实有主题词 → 用 LIKE 在 topics 上兜底（英文概念就存在这里）
    if not rows and terms:
        try:
            conn2 = connect()
            like = " OR ".join(["p.topics LIKE ?"] * len(terms))
            like_args = [f"%{t}%" for t in terms]
            # 复用除 FTS 之外的过滤条件（where[0] 恒为 status，其后才是各 facet）
            extra = where[1:] if match_expr else where[:]
            if match_expr:
                extra = where[1:]
                # where 里 FTS 条件在第 1 位，其余条件与 args 的对应关系需同步裁剪
                extra_args = [a for a in args[1:]]
            else:
                extra_args = list(args)
            conds = extra + [f"({like})"]
            sql2 = (f"SELECT p.pid, p.name, p.name_zh, p.country, p.works_count, "
                    f"p.cited_by_count, p.source, i.name AS inst, i.kind AS inst_kind "
                    f"FROM people p LEFT JOIN institutions i ON i.iid = p.inst_id "
                    f"WHERE {' AND '.join(conds)} ORDER BY {order} LIMIT ?")
            rows = [dict(r) for r in conn2.execute(sql2, (*extra_args, *like_args, limit))]
            total = max(total, len(rows))
            conn2.close()
        except Exception:
            pass

    # 条件过严导致 0 结果 → 放宽被引门槛再试一次，并把"为什么放宽"告诉用户
    relaxed = ""
    if not rows and facets.get("cites_min"):
        relaxed = f"没有同时满足「被引≥{facets['cites_min']}」的人，已自动放宽该条件"
        retry = dict(facets)
        retry["cites_min"] = None
        retry["topic_en"] = terms
        sub = search(retry, limit=limit)
        rows, total = sub["items"], sub["total"]
        facets = sub["facets"]
        facets["notes"] = [n for n in facets.get("notes", []) if "被引" not in n]

    return {"facets": facets, "count": len(rows), "total": total, "items": rows,
            "order": order.split()[0], "terms": terms, "relaxed": relaxed}


def facet_counts(kind: str = "country", limit: int = 12) -> list[dict]:
    """给前端/助手的"当前可选条件"：国家、机构类型、主题。"""
    conn = connect()
    try:
        if kind == "country":
            rows = conn.execute(
                """SELECT country AS value, COUNT(*) n FROM people
                   WHERE status='published' AND country IS NOT NULL AND country != ''
                   GROUP BY country ORDER BY n DESC LIMIT ?""", (limit,)).fetchall()
        elif kind == "inst_kind":
            rows = conn.execute(
                """SELECT i.kind AS value, COUNT(*) n FROM people p
                   JOIN institutions i ON i.iid = p.inst_id
                   WHERE p.status='published' GROUP BY i.kind ORDER BY n DESC LIMIT ?""",
                (limit,)).fetchall()
        else:
            rows = []
        return [dict(r) for r in rows]
    finally:
        conn.close()


# 反向映射：把结构化条件写回自然语言（供"继续筛选"按钮使用）
COUNTRY_ZH = {"CN": "中国", "US": "美国", "GB": "英国", "DE": "德国", "FR": "法国",
              "JP": "日本", "CA": "加拿大", "AU": "澳大利亚", "NL": "荷兰",
              "CH": "瑞士", "SG": "新加坡", "KR": "韩国", "IN": "印度",
              "IT": "意大利", "ES": "西班牙", "SE": "瑞典", "BR": "巴西",
              "HK": "中国香港", "TW": "中国台湾"}
KIND_ZH = {"university": "高校", "hospital": "医院", "institute": "研究所",
           "company": "企业"}


def to_query(facets: dict, **override) -> str:
    """把 facets 写回一句自然语言，用于"继续筛选"。

    例：{country: CN, topic: 呼吸} → "中国的呼吸专家"
    这样按钮点下去仍然走同一条"自然语言 → facet"链路，不需要额外协议。
    """
    f = {**facets, **override}
    parts: list[str] = []
    cc = f.get("country")
    if cc:
        parts.append(COUNTRY_ZH.get(cc, cc))
    kind = f.get("inst_kind")
    if kind:
        parts.append(KIND_ZH.get(kind, kind))
    topic = (f.get("topic") or "").strip()
    if topic:
        parts.append(topic)
    parts.append("专家")
    q = "".join(parts)
    if f.get("inst_name"):
        q += f"，机构是{f['inst_name']}"
    if f.get("cites_min"):
        q += f"，被引超过 {f['cites_min']}"
    # 条数必须显式写进句子，否则"扩大条数"生成的查询和原句一模一样，点了没反应
    if override.get("limit"):
        q += f"，前 {override['limit']} 条"
    return q


def suggest(facets: dict, items: list[dict], total: int, max_items: int = 4) -> list[dict]:
    """根据当前结果给出"下一步可以怎么筛"。

    只提**结果里真实存在**的选项：机构、国家、被引档位、扩大条数。
    没有可选的就返回空列表，不硬凑。
    """
    from collections import Counter

    out: list[dict] = []
    seen: set[str] = set()

    def push(label: str, query: str) -> None:
        if query in seen or len(out) >= max_items:
            return
        seen.add(query)
        out.append({"label": label, "query": query})

    insts = Counter((it.get("inst") or "").strip() for it in items if it.get("inst"))
    for name, n in insts.most_common(2):
        # 已经按这个机构筛过就别再推荐自己，否则点下去毫无变化
        if n >= 2 and name != (facets.get("inst_name") or ""):
            push(f"只看 {name}（{n} 人）", to_query(facets, inst_name=name))

    countries = Counter(it.get("country") for it in items if it.get("country"))
    if len(countries) > 1 and not facets.get("country"):
        cc, n = countries.most_common(1)[0]
        push(f"只看 {COUNTRY_ZH.get(cc, cc)}（{n} 人）", to_query(facets, country=cc))

    if not facets.get("cites_min") and items:
        top = max(int(it.get("cited_by_count") or 0) for it in items)
        if top >= 5000:
            push("只看被引 ≥ 5,000", to_query(facets, cites_min=5000))

    if total > len(items):
        nxt = min(20, max(10, len(items) * 2))
        push(f"扩大到前 {nxt} 条", to_query(facets, limit=nxt))
    return out


if __name__ == "__main__":
    for q in ("中国的呼吸科专家，被引超过 1000，前 5",
              "美国做机器学习的大学教授",
              "企业里的 AI 专家",
              "清华的高被引学者"):
        f = parse(q)
        print(f"\nQ: {q}")
        print("  facets:", {k: v for k, v in f.items() if k != 'notes' and v})
        r = search(f, limit=3)
        print(f"  共 {r['total']} 人，示例:", [(x['name_zh'] or x['name'], x['country']) for x in r['items']])
