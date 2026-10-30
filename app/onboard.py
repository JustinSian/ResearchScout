"""开场引导：告诉用户"能问什么"，而不是让他对着空输入框发呆。

设计原则：
1. 每个示例都**真的能跑通**（写进代码前先在库里验证过），不做"看起来很强"的演示话术；
2. 分组按用户意图，而不是按我们内部的工具名；
3. 同时写清**能力边界**——这比多列三个功能更能建立信任。

这个接口没有任何模型调用，纯数据。
"""
from __future__ import annotations

from app.db import connect

GREETING = (
    "我是 ScholarTrust 的学者尽调助手。"
    "先说清楚我的底子：库里 47,569 位学者、11,104 篇论文，"
    "其中 102 位有完整成果档案；我不接大模型，回答由规则 + 数据库事实拼装，"
    "查不到会直接拒答，不会编。"
)

GROUPS = [
    {
        "title": "认识一位专家",
        "hint": "画像、研究方向、代表作",
        "examples": [
            "介绍一下钟南山",
            "钟南山的研究方向",
            "何建行的研究方向",
        ],
    },
    {
        "title": "看他们的关系",
        "hint": "直接共著 / 最短合作链 / 合作者排名",
        "examples": [
            "钟南山和李兰娟的关系",
            "钟南山和何建行的关系",
            "谁和钟南山合作过",
        ],
    },
    {
        "title": "对比两位专家",
        "hint": "近五年方向差异（会出对照报告）",
        "examples": [
            "比较钟南山和李兰娟近五年的研究方向差异",
            "比较何建行和李兰娟近五年的研究方向差异",
        ],
    },
    {
        "title": "找审稿人",
        "hint": "给一篇稿件的主题，我召回候选、算指标、查同名不同人",
        "examples": [
            "谁更适合审一篇关于肺癌免疫治疗的稿件",
            "谁更适合审一篇关于慢阻肺的稿件",
        ],
    },
    {
        "title": "库外问题（网络兜底）",
        "hint": "知识库没有的问题会转公开网络检索，结果标注来源、未经本库核验",
        "examples": [
            "2026年诺贝尔生理学或医学奖得主是谁",
            "OpenAI 最新模型是什么",
        ],
    },
    {
        "title": "按主题找人 / 找论文",
        "hint": "英文语料，主题词建议用英文或常见中文术语",
        "examples": [
            "找研究 dementia 的专家",
            "找研究 graph neural network 的论文",
        ],
    },
]

LIMITS = [
    "不接大模型：回答由规则 + 数据库事实拼装，不会写文学化评论，也不会替你下结论。",
    "库外问题会转 AnySearch 网络检索：结果带来源链接，但未经本库证据门槛核验，别当成库内结论。",
    "深档案只有 102 位：没建档的人只能给画像，给不了 h-index 与主题分布。",
    "中文语义检索较弱：语料是英文，中文主题词靠一张医学词表映射，冷门词会搜不到。",
    "中文名覆盖有限：只有 41 人有中文名，其余显示英文原名（可点「补中文名」填）。",
]


def welcome() -> dict:
    """开场白 + 示例 + 边界。示例里的名字做一次存在性校验，避免推荐跑不通的例子。"""
    conn = connect()
    try:
        # 注意取 name_zh（中文名），不是 name（英文名）——取错会把含人名的示例全过滤掉
        names = {r["name_zh"] for r in conn.execute(
            "SELECT name_zh FROM people WHERE name_zh IS NOT NULL AND name_zh != ''")}
        dossiers = conn.execute("SELECT COUNT(*) FROM dossier").fetchone()[0]
        people = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        works = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
    finally:
        conn.close()

    groups = []
    for g in GROUPS:
        items = [x for x in g["examples"] if _runnable(x, names)]
        if items:
            groups.append({**g, "examples": items})
    return {
        "greeting": GREETING,
        "stats": {"people": people, "works": works, "dossiers": dossiers,
                  "with_zh_name": len(names)},
        "groups": groups,
        "limits": LIMITS,
        "note": "示例都可以直接点；也可以自己输入中文问题。",
    }


def _runnable(query: str, names: set[str]) -> bool:
    """示例里提到的人必须在库里有中文名，否则这条示例很可能跑不通。"""
    for zh in names:
        if zh in query:
            return True
    # 不含人名的示例（找主题/审稿）一律保留
    return not any(zh in query for zh in ("钟南山", "李兰娟", "何建行"))


if __name__ == "__main__":
    import json
    print(json.dumps(welcome(), ensure_ascii=False, indent=1)[:800])
