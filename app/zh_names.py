"""中文名：人工整理的映射 + 补录入口。

现状：OpenAlex 只有拉丁转写（"Kefang Lai"），没有汉字。
47,569 人里绝大多数没有中文名，所以界面上大面积是英文——这不是显示 bug，
是**数据本身缺**。

这里做三件事：
1. 收录一批**有把握**的中文名（只收我能确认对应关系的，宁缺毋滥）；
2. 提供 apply() 把名字写进 people.name_zh + FTS（这样中文搜索也能命中）；
3. 留一个补录函数，前端可以让用户自己填（填错由用户负责，来源标记为 user）。

注意：拼音 → 汉字不是唯一映射（"Jun Liu" 可能是刘军/刘俊/刘珺），
所以**不做自动音译**，只做人工映射 + 用户补录。
"""
from __future__ import annotations

import json

from app.db import connect, init_db

# 英文名（OpenAlex display_name）→ 汉字名。仅收能确认的。
MANUAL: dict[str, str] = {
    "Nanshan Zhong": "钟南山",
    "Lanjuan Li": "李兰娟",
    "Jianxing He": "何建行",
    "Weijie Guan": "关伟杰",
    "Wei‐jie Guan": "关伟杰",
    "Kefang Lai": "赖克方",
    "Pixin Ran": "冉丕鑫",
    "Zifeng Yang": "杨子峰",
    "Jinping Zheng": "郑劲平",
    "Wenhua Liang": "梁文华",
    "Yimin Li": "黎毅敏",
    "Wenju Lu": "卢文菊",
    "Yumin Zhou": "周玉民",
    "Rongchang Chen": "陈荣昌",
    "Chunliang Lei": "雷春亮",
    "Zhijian Zheng": "郑志坚",
}

_DDL = [
    "CREATE TABLE IF NOT EXISTS name_zh_log (pid INTEGER, old TEXT, new TEXT, src TEXT, ts REAL)",
]


def _norm(s: str) -> str:
    """名字规范化：统一连字符（U+2010/2011/–/—）与空白，忽略大小写。

    踩过的坑：库里是 "Wei‐jie Guan"（U+2010 连字符），映射表写的是 "-"，
    精确匹配就漏掉了。
    """
    import re
    s = (s or "").strip().lower()
    s = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\-]+", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _fts_update(conn, pid: int) -> None:
    r = conn.execute("SELECT name,name_zh,topics,last_inst FROM people WHERE pid=?", (pid,)).fetchone()
    if not r:
        return
    conn.execute("DELETE FROM people_fts WHERE rowid=?", (pid,))
    conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                 (pid, f'{r["name"]} {r["name_zh"] or ""}'.strip(),
                  " ".join(json.loads(r["topics"] or "[]")), r["last_inst"] or ""))


def apply(log=print) -> dict:
    """把 MANUAL 里的中文名写进库（幂等）。"""
    init_db()
    conn = connect()
    for ddl in _DDL:
        conn.execute(ddl)
    # 全库一次性读入，按规范化名匹配（避免逐条精确匹配漏掉连字符变体）
    index: dict[str, tuple] = {}
    for r in conn.execute("SELECT pid,name,name_zh FROM people"):
        index.setdefault(_norm(r["name"]), (r["pid"], r["name"], r["name_zh"]))
    added = skipped = 0
    for en, zh in MANUAL.items():
        hit = index.get(_norm(en))
        row = ({"pid": hit[0], "name": hit[1], "name_zh": hit[2]} if hit else None)
        if not row:
            skipped += 1
            continue
        if row["name_zh"] == zh:
            continue
        conn.execute("UPDATE people SET name_zh=? WHERE pid=?", (zh, row["pid"]))
        conn.execute("INSERT INTO name_zh_log(pid,old,new,src,ts) VALUES(?,?,?,?,?)",
                     (row["pid"], row["name_zh"], zh, "manual", __import__("time").time()))
        _fts_update(conn, int(row["pid"]))
        added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM people WHERE name_zh IS NOT NULL").fetchone()[0]
    conn.close()
    log(f"  写入中文名 {added} 人（跳过 {skipped} 个库里没有的），全库已有中文名 {total} 人")
    return {"added": added, "skipped": skipped, "total_zh": total}


def propagate(log=print) -> dict:
    """同名传播：同一英文名下只要有一条记录有中文名，其余也补上。

    背景：同一位专家在库里常有两条记录（某学术图谱导入 + OpenAlex 各一条），
    中文名只写在其中一条，另一条仍显示英文。
    同名同人是有风险的假设（可能重名），但**只用于显示**，
    来源可追溯（name_zh_log 里 src=name_propagation）。
    """
    init_db()
    conn = connect()
    rows = conn.execute(
        """SELECT p.pid, p.name,
                  (SELECT p2.name_zh FROM people p2
                    WHERE p2.name = p.name AND p2.name_zh IS NOT NULL LIMIT 1) AS zh
           FROM people p WHERE p.name_zh IS NULL""").fetchall()
    n = 0
    for r in rows:
        if not r["zh"]:
            continue
        conn.execute("UPDATE people SET name_zh=? WHERE pid=?", (r["zh"], r["pid"]))
        _fts_update(conn, int(r["pid"]))
        n += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM people WHERE name_zh IS NOT NULL").fetchone()[0]
    conn.close()
    log(f"  同名传播补上 {n} 条记录，全库有中文名 {total} 人")
    return {"propagated": n, "total_zh": total}


def set_name_zh(pid: int, zh: str, src: str = "user", log=print) -> dict:
    """补录单个中文名（前端用）。会同步更新 FTS。"""
    zh = (zh or "").strip()
    if not zh or len(zh) > 20:
        raise ValueError("中文名不能为空且不超过 20 字")
    init_db()
    conn = connect()
    try:
        for ddl in _DDL:
            conn.execute(ddl)
        row = conn.execute("SELECT name,name_zh FROM people WHERE pid=?", (pid,)).fetchone()
        if not row:
            raise ValueError(f"没有 pid={pid} 的人")
        conn.execute("UPDATE people SET name_zh=? WHERE pid=?", (zh, pid))
        conn.execute("INSERT INTO name_zh_log(pid,old,new,src,ts) VALUES(?,?,?,?,?)",
                     (pid, row["name_zh"], zh, src, __import__("time").time()))
        _fts_update(conn, pid)
        conn.commit()
        log(f"  pid={pid} 中文名：{row['name_zh'] or '（空）'} → {zh}")
        return {"pid": pid, "name": row["name"], "name_zh": zh}
    finally:
        conn.close()


def missing(limit: int = 20) -> list[dict]:
    """还没有中文名的专家（按被引排序），给前端做补录列表。"""
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            """SELECT pid,name,cited_by_count FROM people
               WHERE status='published' AND name_zh IS NULL
               ORDER BY cited_by_count DESC LIMIT ?""", (limit,))]
    finally:
        conn.close()


if __name__ == "__main__":
    print(apply())
    print(missing(5))
