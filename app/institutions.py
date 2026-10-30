"""机构归一：把 4,320 个 `people.last_inst` 字符串收敛成机构实体。

为什么需要（对标 AMiner 的 `organization/na` 消歧接口）：
- 现在机构只是自由文本，"First Affiliated Hospital of Guangzhou Medical University"
  和 "The First Affiliated Hospital, Guangzhou Medical University" 会算成两家；
- 机构是审稿人匹配里的重要信号（同机构要回避、跨机构才合适），没归一就用不了。

做法（**保守**）：
1. 归一化：展开常见缩写（Univ→University、Hosp→Hospital…）、去标点、压缩空格、小写；
2. 同 norm_key 合并，展示名取出现频次最高的原始写法；
3. 识别机构类型（医院/大学/研究所/企业），用于"这家机构是不是医院"这类判断；
4. 对剩余的近似名做 Jaccard 候选对，**只报告不自动合并**——合并错机构比不合并更糟。

自动合并只在 norm_key 完全相同的情况发生，这是安全边界。
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from app.db import connect, init_db

# 注意：`institutions` 表在早期里程碑里已存在（openalex_id / name / country / type），
# 被 app/db.py 的 upsert_institution 使用。这里**不新建表、不改语义**，
# 只补上归一化需要的列，避免抢表名造成隐性破坏。
NEW_COLS = {
    "norm_key": "TEXT",
    "kind": "TEXT DEFAULT 'unknown'",
    "people_count": "INTEGER DEFAULT 0",
    "variants_json": "TEXT",
}
IDX = [
    # norm_key 允许为 NULL（旧行）；SQLite 唯一索引允许多个 NULL
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_inst_norm ON institutions(norm_key)",
]


def _ensure_schema(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(institutions)")}
    if not cols:      # 全新库：建最小表
        conn.execute("""CREATE TABLE institutions (
            iid INTEGER PRIMARY KEY, openalex_id TEXT, name TEXT,
            country TEXT, type TEXT)""")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(institutions)")}
    for col, decl in NEW_COLS.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE institutions ADD COLUMN {col} {decl}")
    for ddl in IDX:
        conn.execute(ddl)
    conn.commit()

# 常见缩写展开（英文机构名里极高频，不展开会分出大量重复）
_ABBR = {
    "univ": "university", "hosp": "hospital", "med": "medical", "sch": "school",
    "inst": "institute", "tech": "technology", "natl": "national", "ctr": "center",
    "dept": "department", "affil": "affiliated", "coll": "college", "acad": "academy",
    "clin": "clinical", "res": "research", "sci": "science", "eng": "engineering",
    "biomed": "biomedical", "int": "international", "gen": "general",
    "lab": "laboratory", "labs": "laboratory", "univ.": "university",
}

_KIND_RULES = [
    ("hospital", re.compile(r"hospital|clinic|medical center|health center|医院|医学中心|临床|卫生院|疾控", re.I)),
    ("university", re.compile(r"university|college|school of|大学|学院", re.I)),
    ("institute", re.compile(r"institute|laborator|\blab\b|academy|research cent|研究所|研究院|实验室|重点实验室", re.I)),
    ("company", re.compile(r"\b(ltd|inc|corp|co\.|gmbh|company|pharma|biotech)\b|公司|集团|药业", re.I)),
]


def norm_key(name: str) -> str:
    """机构名归一化：展开缩写 + 去连接词 + 压缩空白。"""
    s = (name or "").strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\u4e00-\u9fa5 ]+", " ", s)
    toks = [_ABBR.get(t, t) for t in s.split()]
    # 去掉连接词与冠词：of/the/and/for 不承载机构语义，
    # 保留会让 "Hospital of Guangzhou Medical" 与 "Hospital, Guangzhou Medical" 分家
    toks = [t for t in toks if t not in {"of", "the", "and", "for"}]
    s = " ".join(toks)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def kind_of(name: str) -> str:
    for kind, rx in _KIND_RULES:
        if rx.search(name or ""):
            return kind
    return "unknown"


def build(log=print) -> dict:
    """建 institutions 表并把 people.inst_id 回填（幂等）。"""
    init_db()
    conn = connect()
    _ensure_schema(conn)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(people)")}
        if "inst_id" not in cols:
            conn.execute("ALTER TABLE people ADD COLUMN inst_id INTEGER")
        conn.commit()

        rows = conn.execute(
            """SELECT last_inst, COUNT(*) n FROM people
               WHERE last_inst IS NOT NULL AND TRIM(last_inst) != ''
               GROUP BY last_inst""").fetchall()
        groups: dict[str, dict] = {}
        for r in rows:
            key = norm_key(r["last_inst"])
            if not key:
                continue
            g = groups.setdefault(key, {"variants": Counter(), "n": 0})
            g["variants"][r["last_inst"].strip()] += r["n"]
            g["n"] += r["n"]

        for key, g in groups.items():
            name = g["variants"].most_common(1)[0][0]
            conn.execute(
                """INSERT INTO institutions(name,norm_key,kind,people_count,variants_json)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(norm_key) DO UPDATE SET
                     name=excluded.name, kind=excluded.kind,
                     people_count=excluded.people_count, variants_json=excluded.variants_json""",
                (name, key, kind_of(name), g["n"],
                 json.dumps(g["variants"].most_common(5), ensure_ascii=False)))
        conn.commit()

        # 回填 people.inst_id（Python 侧映射，稳）
        imap = {r["norm_key"]: r["iid"] for r in conn.execute("SELECT iid,norm_key FROM institutions")}
        upd = []
        for r in conn.execute("SELECT pid,last_inst FROM people WHERE last_inst IS NOT NULL"):
            iid = imap.get(norm_key(r["last_inst"]))
            if iid:
                upd.append((iid, r["pid"]))
        conn.executemany("UPDATE people SET inst_id=? WHERE pid=?", upd)
        conn.commit()

        by_kind = Counter(r[0] for r in conn.execute("SELECT kind FROM institutions"))
        stats = {"institutions": len(groups),
                 "people_linked": len(upd),
                 "kinds": dict(by_kind)}
        log(f"  机构实体 {stats['institutions']} 个，挂载专家 {stats['people_linked']} 人，"
            f"类型分布 {stats['kinds']}")
        return stats
    finally:
        conn.close()


def top(limit: int = 20, kind: str = "") -> list[dict]:
    conn = connect()
    try:
        sql = """SELECT iid,name,kind,people_count,variants_json FROM institutions
                 WHERE (? = '' OR kind = ?) ORDER BY people_count DESC LIMIT ?"""
        rows = [dict(r) for r in conn.execute(sql, (kind, kind, limit))]
        for r in rows:
            r["variants"] = json.loads(r.pop("variants_json") or "[]")
        return rows
    finally:
        conn.close()


def _tokens(key: str) -> set[str]:
    """比较用的词集合：保留**全部**词（含短词）。

    两次踩坑的记录：
    1. 一开始过滤掉 university/hospital 这类通用词 → "Guangzhou Medical University"
       与 "Guangzhou University" 都只剩 {guangzhou}，Jaccard 直接 1.0；
    2. 改为过滤长度 <3 的词 → "Wuxi No.2 People's Hospital" 里的 no/2 被丢掉，
       又和 "Wuxi People's Hospital" 变成同一家。
    结论：机构名的区分信息可能就在短词里，比较时一个词都不能少。
    """
    return set(key.split())


def near_duplicates(limit: int = 20, min_jaccard: float = 0.85) -> list[dict]:
    """近似重复机构：只报告候选对，供人工复核（不自动合并）。"""
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT iid,name,norm_key,people_count FROM institutions ORDER BY people_count DESC")]
    finally:
        conn.close()

    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        toks = _tokens(r["norm_key"])
        if not toks:
            continue
        # 用 token 建倒排，只比较至少共享一个实词的机构（避免 O(n²) 全比）
        for t in toks:
            buckets[t].append(r)

    seen, out = set(), []
    for _tok, group in buckets.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                pair = tuple(sorted((a["iid"], b["iid"])))
                if pair in seen:
                    continue
                seen.add(pair)
                ta, tb = _tokens(a["norm_key"]), _tokens(b["norm_key"])
                jac = len(ta & tb) / max(len(ta | tb), 1)
                if jac >= min_jaccard:
                    if ta == tb:
                        # 词集合相同但 norm_key 不同 → 多半是"同名不同机构"，必须人工确认
                        rel = "same_tokens_confirm"
                    elif ta <= tb or tb <= ta:
                        rel = "parent_child"
                    else:
                        rel = "similar"
                    out.append({"a": a["name"], "b": b["name"], "jaccard": round(jac, 3),
                                "relation_guess": rel,
                                "people_a": a["people_count"], "people_b": b["people_count"]})
    out.sort(key=lambda x: -x["jaccard"])
    return out[:limit]


def for_person(pid: int) -> dict | None:
    """某位专家所属机构（含机构类型与规模，供审稿人匹配判断同机构冲突）。"""
    conn = connect()
    try:
        r = conn.execute(
            """SELECT i.iid,i.name,i.kind,i.people_count FROM people p
               JOIN institutions i ON i.iid=p.inst_id WHERE p.pid=?""", (pid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


if __name__ == "__main__":
    print(build())
    print(top(5))
    print(json.dumps(near_duplicates(5), ensure_ascii=False, indent=1))
