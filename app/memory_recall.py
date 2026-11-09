"""记忆召回：从用户的事实里挑出与当前问题相关的几条，并转成"提示"。

三个约束：
1. **显式条件永远优先**：用户这句话里说了"中国的"，记忆里的"美国"不许覆盖；
2. **空记忆零开销**：没有 uid 或没有事实时，不查库、不改任何行为；
3. **有界**：最多 k 条、提示文本 ≤ 200 字，避免把记忆塞满上下文。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app import user_memory

KEY_WEIGHT = {"topic": 1.3, "field": 1.3, "constraint": 1.1,
              "preference": 0.9, "role": 0.7, "org": 0.6}
HALF_LIFE_DAYS = 180.0


# 中文领域 → 英文检索词的兜底表（facets.topic_en 的词表不全，
# 例如"帕金森""阿尔茨海默"会返回空，导致记忆补全后仍然搜不到）。
_TOPIC_FALLBACK = {
    "帕金森": ["parkinson"],
    "阿尔茨海默病": ["alzheimer"],
    "阿尔茨海默": ["alzheimer"],
    "单细胞测序": ["single-cell"],
    "肿瘤免疫微环境": ["tumor microenvironment", "immunotherapy"],
    "医学影像": ["medical imaging"],
    "蛋白质结构": ["protein structure"],
    "蛋白质设计": ["protein design"],
    "药物重定位": ["drug repurposing"],
    "空间转录组": ["spatial transcriptomics"],
    "皮肤菌群": ["skin microbiome"],
    "乳腺癌": ["breast cancer"],
    "扩散模型": ["diffusion model"],
    "模仿学习": ["imitation learning"],
    "多模态": ["multimodal"],
    "病理图像": ["pathology"],
}

# 把记忆主题变成英文检索词
def topic_terms(topic: str) -> list[str]:
    """主题英文检索词：先走 facets.topic_en，为空再用兜底表。"""
    from app.facets import topic_en
    terms = topic_en(topic)
    if terms:
        return terms
    for k, v in _TOPIC_FALLBACK.items():
        if k in (topic or ""):
            return list(v)
    return []

# 文本拆成可比较的集合
def _tokens(text: str) -> set[str]:
    t = (text or "").lower()
    words = set(re.findall(r"[a-z][a-z\-]{1,}", t))
    cjk = re.sub(r"[^\u4e00-\u9fa5]", " ", t)
    for seg in cjk.split():
        for i in range(len(seg) - 1):
            words.add(seg[i:i + 2])
    return words

# 计算记忆已经存在多少天
def _age_days(valid_from: str) -> float:
    try:
        dt = datetime.fromisoformat(valid_from)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except Exception:
        return HALF_LIFE_DAYS

# 记忆排序的核心公式
def _score(query_tokens: set[str], fact: dict) -> float:
    value_tokens = _tokens(fact.get("value") or "")
    overlap = len(query_tokens & value_tokens)
    recency = 0.5 ** (_age_days(fact.get("valid_from") or "") / HALF_LIFE_DAYS)
    return overlap * 2.0 + KEY_WEIGHT.get(fact.get("key") or "", 0.5) * 0.5 + recency * 0.5


def recall(uid: str, query: str, k: int = 5) -> list[dict]:
    """按"词重叠 + key 权重 + 时间衰减"排序，取前 k 条。"""
    if not (uid or "").strip():
        return []
    try:
        rows = user_memory.facts(uid, only_active=True, limit=100)
    except Exception:
        return []
    if not rows:
        return []
    qt = _tokens(query)
    scored = [(_score(qt, r), r) for r in rows]
    scored.sort(key=lambda x: (-x[0], x[1].get("key") or ""))
    top = [r for s, r in scored[:k] if s > 0] or [r for _s, r in scored[:k]]
    return top


def build_hint(uid: str, query: str) -> dict:
    """把召回结果压成可注入的提示。空记忆返回 {}。"""
    facts = recall(uid, query)
    if not facts:
        return {}
    def first(key: str) -> str:
        for f in facts:
            if f.get("key") == key:
                return (f.get("value") or "").strip()
        return ""
    hint = {
        "topic": first("topic") or first("field"),
        "constraint": first("constraint"),
        "preference": first("preference"),
        "org": first("org"),
        "country": first("country"),
        "facts": [{k: f.get(k) for k in ("key", "value", "valid_from", "source")}
                  for f in facts],
    }
    hint = {k: v for k, v in hint.items() if v}
    if hint.get("facts"):
        hint["note"] = note(hint)
    return hint


def note(hint: dict) -> str:
    """一句话说明（附在答案前，让用户知道"系统记得"）。"""
    parts = []
    if hint.get("topic"):
        parts.append(f"方向={hint['topic']}")
    if hint.get("constraint"):
        parts.append(f"限制={hint['constraint']}")
    if hint.get("preference"):
        parts.append(f"偏好={hint['preference']}")
    if hint.get("org"):
        parts.append(f"单位={hint['org']}")
    if not parts:
        return ""
    when = ""
    facts = hint.get("facts") or []
    if facts:
        when = (facts[0].get("valid_from") or "")[:10]
    tail = f"，{when} 更新" if when else ""
    return "（已按你的长期记忆：" + "、".join(parts) + tail + "）"


def apply_to_parsed(parsed: dict, hint: dict) -> dict:
    """用记忆补全 facet 条件：**只填空，不覆盖**。

    返回的 parsed 里多两个字段：
      memory_fills    这次由记忆补上的字段
      explicit_keeps  用户显式给了、记忆没覆盖的字段
    """
    if not hint:
        return parsed
    out = dict(parsed or {})
    fills, keeps = [], []
    # "推荐几个专家" 会被 facets.parse 当成主题"推荐几个"；这类短且无英文映射的
    # 片段是噪声，应该让记忆补位，而不是占着 topic 把记忆挡掉。
    _t = (out.get("topic") or "").strip()
    if _t and len(_t) <= 4 and not topic_terms(_t):
        out["topic"] = ""
        out["topic_en"] = []

    if not out.get("topic") and hint.get("topic"):
        out["topic"] = hint["topic"]
        out["topic_en"] = topic_terms(hint["topic"])
        fills.append("topic")
    elif out.get("topic") and hint.get("topic"):
        keeps.append("topic")

    if not out.get("country") and hint.get("country"):
        out["country"] = hint["country"]
        fills.append("country")
    elif out.get("country") and hint.get("country"):
        keeps.append("country")

    if not out.get("sort") and hint.get("sort"):
        out["sort"] = hint["sort"]
        fills.append("sort")
    elif out.get("sort") and hint.get("sort"):
        keeps.append("sort")

    if fills:
        out["memory_fills"] = fills
    if keeps:
        out["explicit_keeps"] = keeps
    return out


def log_event(uid: str, sid: str, query: str, result: dict) -> None:
    """把一次问答写进情景记忆（失败不影响主流程）。"""
    if not (uid or "").strip():
        return
    try:
        user_memory.add_event(
            uid, sid, query,
            action=result.get("route") or (result.get("plan") or [{}])[0].get("tool") or "",
            outcome="refused" if result.get("refused") else "answered",
            meta={"citations": len(result.get("citations") or []),
                  "route": result.get("route")})
    except Exception:
        pass
