"""Agent 评测：24 题，覆盖 5 个工具 + 拒答。

判定项（都不依赖主观打分）：
  tool_ok     —— 规划出的工具是否等于期望工具
  cited       —— 非拒答回答是否带引用
  refused_ok  —— 该拒的拒了、不该拒的没拒
  numbers     —— 回答里是否出现结构化数字（作品数/被引/共同论文数）
"""
from __future__ import annotations

import json
import re

from app.agent import answer

CASES = [
    # (问题, 期望工具, 是否应拒答)
    ("找研究 graph neural network 的专家", "find_expert", False),
    ("有哪些做 reinforcement learning 的学者", "find_expert", False),
    ("研究 dementia 的专家有哪些", "find_expert", False),
    ("帮我找 cancer immunotherapy 方向的专家", "find_expert", False),
    ("找 protein structure 领域的专家", "find_expert", False),
    ("有哪些人研究 image segmentation", "find_expert", False),
    ("找 drug discovery 方向的研究者", "find_expert", False),
    ("speech recognition 有哪些专家", "find_expert", False),
    ("介绍一下 Yoshua Bengio", "profile", False),
    ("Geoffrey Hinton 是谁", "profile", False),
    ("介绍一下 Yann LeCun 的背景", "profile", False),
    ("Fei-Fei Li 的画像", "profile", False),
    ("谁和 Yoshua Bengio 合作过", "collaborators", False),
    ("Yann LeCun 的合作者有哪些", "collaborators", False),
    ("谁和 Geoffrey Hinton 一起写论文", "collaborators", False),
    ("Ian Goodfellow 和谁合作", "collaborators", False),
    ("找 transformer 相关的论文", "works", False),
    ("有没有关于 attention 的文章", "works", False),
    ("对比 Yoshua Bengio 和 Geoffrey Hinton", "compare", False),
    ("比较 Yann LeCun 和 Ian Goodfellow", "compare", False),
    # 拒答组（库里没有对应方向）
    ("找研究 quantum gravity phenology 的专家", "find_expert", True),
    ("谁研究 medieval icelandic poetry", "find_expert", True),
    ("找 underwater basket weaving 的专家", "find_expert", True),
    ("介绍 baroque architecture 领域的带头人", "find_expert", True),
]


def main() -> None:
    rows = []
    for q, expect_tool, should_refuse in CASES:
        r = answer(q, allow_fallback=False)
        tools = [p["tool"] for p in r["plan"]]
        tool_ok = bool(tools) and tools[0] == expect_tool
        refused = bool(r["refused"])
        cited = len(r["citations"]) > 0
        has_num = bool(re.search(r"\d", r["answer"]))
        refused_ok = refused == should_refuse
        rows.append({"q": q, "expect_tool": expect_tool, "tool": tools,
                     "tool_ok": tool_ok, "refused": refused, "refused_ok": refused_ok,
                     "cited": cited, "has_num": has_num})

    n = len(rows)
    tool_acc = sum(r["tool_ok"] for r in rows) / n
    refuse_acc = sum(r["refused_ok"] for r in rows) / n
    cite_rate = sum(r["cited"] for r in rows if not r["refused"]) / max(
        1, sum(1 for r in rows if not r["refused"]))
    num_rate = sum(r["has_num"] for r in rows if not r["refused"]) / max(
        1, sum(1 for r in rows if not r["refused"]))
    print(f"题数 {n}")
    print(f"  工具选择准确率 : {tool_acc:.1%}")
    print(f"  拒答正确率     : {refuse_acc:.1%}")
    print(f"  非拒答带引用率 : {cite_rate:.1%}")
    print(f"  非拒答含数字率 : {num_rate:.1%}")

    print("\n失败样例：")
    for r in rows:
        if not (r["tool_ok"] and r["refused_ok"]):
            print(f"  {r['q'][:36]:<38} 期望 {r['expect_tool']}/"
                  f"{'拒' if r['refused'] else '答'}  实际 {r['tool']}/"
                  f"{'拒' if r['refused'] else '答'}")

    with open("eval/m4_agent.json", "w", encoding="utf-8") as fh:
        json.dump({"tool_acc": tool_acc, "refuse_acc": refuse_acc,
                   "cite_rate": cite_rate, "num_rate": num_rate, "rows": rows},
                  fh, ensure_ascii=False, indent=1)
    print("\n写出 eval/m4_agent.json")


if __name__ == "__main__":
    main()
