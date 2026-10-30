"""中文别名表：让"介绍一下李兰娟"这种提问能命中 OpenAlex 的英文记录。

背景：OpenAlex 只有英文名（Lanjuan Li），中文名 name_zh 目前只有从某学术图谱导入的钟南山有。
所以中文提问查不到人 —— 这不是小问题，用户就是中文用户。

做法：
- 表 `person_alias(pid, alias)`，唯一索引；
- 别名同时写进 FTS（people_fts.name 列），这样 search_people 不用改；
- 别名**只收有把握的**：宁可查不到，也不能把张三的别名挂到李四身上。

注意：这里的中文名是人工整理，不是 OpenAlex 数据。以后从某学术图谱批量导入时，
`metadata.name_en` + `name` 可以自动产生别名，不需要这份表。
"""
from __future__ import annotations

import sqlite3

from app.db import connect, init_db

DDL = [
    """CREATE TABLE IF NOT EXISTS person_alias (
        pid INTEGER NOT NULL,
        alias TEXT NOT NULL,
        source TEXT,
        PRIMARY KEY (pid, alias)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_alias ON person_alias(alias)",
]

# 英文名（OpenAlex display_name）→ 中文名。只收本库里能对上的、且我有把握的。
ALIASES: dict[str, str] = {
    "Lanjuan Li": "李兰娟",
    "Jianxing He": "何建行",
    "Wei‐jie Guan": "关伟杰",
    "Wei-jie Guan": "关伟杰",
    "Kefang Lai": "赖克方",
    "Pixin Ran": "冉丕鑫",
    "Zifeng Yang": "杨子峰",
    "Jinping Zheng": "郑劲平",
    "Wenhua Liang": "梁文华",
    "Ruchong Chen": "陈荣昌",
    "Yimin Li": "黎毅敏",
    "Nanshan Zhong": "钟南山",
}


def apply_aliases(log=print) -> dict:
    init_db()
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    added = missing = 0
    for en, zh in ALIASES.items():
        row = conn.execute("SELECT pid, name FROM people WHERE name=? LIMIT 1", (en,)).fetchone()
        if not row:
            missing += 1
            log(f"  跳过（库里没有）: {en}")
            continue
        pid = int(row["pid"])
        conn.execute("INSERT OR REPLACE INTO person_alias(pid,alias,source) VALUES(?,?,?)",
                     (pid, zh, "manual"))
        # 同时补 name_zh（画像/卡片显示中文名），已有值不覆盖
        conn.execute("UPDATE people SET name_zh=COALESCE(name_zh, ?) WHERE pid=?", (zh, pid))
        # FTS 里补上中文别名，中文提问即可命中
        conn.execute("DELETE FROM people_fts WHERE rowid=?", (pid,))
        r = conn.execute("SELECT name,name_zh,topics,last_inst FROM people WHERE pid=?",
                         (pid,)).fetchone()
        conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                     (pid, f'{r["name"]} {r["name_zh"] or ""} {zh}'.strip(),
                      " ".join(__import__("json").loads(r["topics"] or "[]")),
                      r["last_inst"] or ""))
        added += 1
    conn.commit()
    conn.close()
    log(f"  别名写入 {added} 条，跳过 {missing} 条")
    return {"added": added, "missing": missing}


if __name__ == "__main__":
    print(apply_aliases())
