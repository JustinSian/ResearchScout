"""可信判定器（独立模块，便于单测与 M3 评测）。

设计要点（这是与某学术图谱最大的差异）：
某学术图谱把"可信门槛"写成目标（≥98%/99%），但没有可复现的打分与实测。
这里每个判定都输出 score + reasons + evidence，M3 直接用它算精确率/召回与置信区间。

特征选择来自第一次试跑的教训：**只用 ORCID 会把 Geoffrey Hinton / Kahneman 判成 reject**
（他们没有 ORCID）。所以现在同时用"作品数/被引/机构/姓名形态"这些可观测信号。
"""
from __future__ import annotations

import re
from typing import Any

from app.config import GATE_ACCEPT, GATE_REVIEW

NON_PERSON = re.compile(
    r"\b(universit|institut|college|school|department|faculty|academy|society|"
    r"association|conference|proceedings|symposium|workshop|journal|series|"
    r"ministry|agency|council|center|centre|laborator|hospital|foundation|"
    r"committee|federation|network|consortium|group|team|"
    # 2026-10-29 补：专家墙把 "Authors Task Force Members" 排到了第 1 名，
    # 说明"论文作者栏里的集体署名"也能通过可信门槛（9 篇/5 万被引），必须挡掉。
    r"task force|authors?|members?|investigators?|panel|writing|collaborat|"
    r"trial|initiative|consensus)\b",
    re.I,
)
# 说明：用 \b 边界 + 子串匹配（universit 覆盖 university/université），
# 所以像 "Member" 这样的正常姓氏也可能被误伤——但这类姓名在样本里没有出现过，
# 且拒收代价远小于把集体署名当专家展示。
NAME_SHAPE = re.compile(r"^[A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*){1,3}$")
# 中文人名：2~4 个汉字（钟南山 / 李兰娟 / 张文宏），5 字以上更可能是机构或会议
NAME_ZH = re.compile(r"^[\u4e00-\u9fa5]{2,4}$")
# 中文机构/组织词：命中就不当人
NON_PERSON_ZH = re.compile(r"(大学|学院|医院|研究所|研究院|中心|实验室|集团|公司|委员会|学会|协会|基金会|论坛|峰会|编辑部|课题组)")



def judge(features: dict[str, Any]) -> dict:
    """features: name, orcid, sample_works, sample_cites, has_inst, topics"""
    name = (features.get("name") or "").strip()
    score = 0.0
    reasons: list[dict[str, Any]] = []

    def add(rule: str, delta: float, detail: Any = "") -> None:
        nonlocal score
        score += delta
        reasons.append({"rule": rule, "delta": delta, "detail": detail})

    # 四组信号：身份 / 产出 / 影响 / 归属。
    # 教训：最初"有 ORCID 才算人"会把 Geoffrey Hinton（13 篇、24 万被引、无 ORCID）
    # 判成 pending，所以身份只占 0.35，剩下靠可观测的产出与影响。
    if features.get("orcid"):
        add("has_orcid", 0.35, features["orcid"])
    if NAME_SHAPE.match(name):
        add("name_looks_like_person", 0.20, name)
    if NAME_ZH.match(name):
        add("chinese_person_name", 0.20, name)
    # 外部专家库（如某学术图谱）已人工核验：作为一条独立来源证据。
    # 权重 0.55 低于"主库可测产出"，避免外部数据压过本库信号。
    if features.get("external_verified"):
        add("external_verified_source", 0.55, features.get("source") or "external")
    if features.get("headline"):
        add("has_headline", 0.10, str(features["headline"])[:40])
    # 网络背书：与"已核验专家"有共著关系，且本身有 10 篇以上作品。
    # 门槛设在 10 篇，是因为一篇几十人的大协作论文不能算"同行认可"；
    # 这条证据权重 0.25，单独不足以通过门槛，必须配合机构/作品/主题。
    if features.get("coauthor_of_verified"):
        add("coauthor_of_verified", 0.25, str(features.get("works") or ""))

    works = int(features.get("sample_works") or 0)
    cites = int(features.get("sample_cites") or 0)
    if works >= 5:
        add("works_5plus", 0.25, works)
    elif works >= 3:
        add("works_3plus", 0.15, works)
    elif works >= 2:
        add("two_works", 0.08, works)
    else:
        add("single_work", -0.05, works)      # 只出现一次的作者默认不算"专家"

    # 被引不能用绝对阈值：样本本身就是"最高被引 8000 篇"，
    # 几乎人人上千被引（第一次 accept 率被抬到 65%）。改用样本内分位数。
    p50 = float(features.get("cite_p50") or 0)
    p90 = float(features.get("cite_p90") or 0)
    if p90 and cites >= p90:
        add("cites_top10pct", 0.20, cites)
    elif p50 and cites >= p50:
        add("cites_above_median", 0.10, cites)
    if features.get("has_inst"):
        add("has_institution", 0.10, features.get("has_inst"))
    if features.get("topics"):
        add("has_topics", 0.05, features.get("topics"))

    m = NON_PERSON.search(name)
    if m:
        add("non_person_word", -0.55, m.group(0))
    # 中文机构名（"广州市第一人民医院"）不能被当成中文人名放过
    mz = NON_PERSON_ZH.search(name)
    if mz:
        add("non_person_word_zh", -0.55, mz.group(0))
    if name.isupper() and len(name) > 6:
        add("all_caps", -0.25, name)
    if len(name) < 3:
        add("name_too_short", -0.40, name)
    if any(ch.isdigit() for ch in name):
        add("name_has_digits", -0.20, name)

    score = round(max(0.0, min(1.0, score)), 3)
    verdict = ("accept" if score >= GATE_ACCEPT
               else "review" if score >= GATE_REVIEW else "reject")
    return {"score": score, "verdict": verdict, "reasons": reasons}
