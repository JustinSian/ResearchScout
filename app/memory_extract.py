"""从一句话里抽取"值得长期记住的事实"。

默认**纯规则**（零成本、可单测、可解释）；`ST_MEM_LLM=1` 时才额外走模型抽取，
且任何失败都退回规则——抽取永远不能阻塞对话。

输出统一为 [{op, key, value}]：
  op="set"    写入/覆盖（同 key 旧值会被 expire）
  op="expire" 只让旧值失效（"我不做 X 了"但没说转做什么）
"""
from __future__ import annotations

import json
import re

# key 的取值是有限的，方便前端分组与召回打分
KEYS = ("topic", "field", "constraint", "preference", "role", "org", "country")

_TOPIC_STOP = "了。，,；;！!？?、 \n\t"

# ---- 规则表：每条 (key, 正则, 组名) ----

# 转向：先 expire 旧方向，再 set 新方向
_REDIRECT = re.compile(
    r"(?:我)?(?:现在|目前)?(?:不再|不)(?:做|研究|搞|关注)\s*(?P<old>[^，。；,;\s]{2,20}?)"
    r"\s*(?:了)?\s*[，,;；]?\s*(?:改|转)(?:为|做|向|成)?\s*(?P<new>[^，。；,;\s]{2,20})")

_ONLY = re.compile(r"(?:我)?(?:现在|目前)?(?:只|仅)(?:做|研究|搞|关注)\s*(?P<v>[^，。；,;\s]{2,20})")

_TOPIC = re.compile(
    r"(?:我)?(?:现在|目前)?(?:主要)?(?:在做|在做的是|做|研究|搞|专注(?:于)?)\s*"
    r"(?P<v>[^，。；,;\s]{2,20})")

_FIELD = re.compile(r"(?:我)?(?:的)?(?:研究)?(?:方向|领域|专业)(?:是|为)\s*(?P<v>[^，。；,;\s]{2,20})")

# 国家约束："我只找中国的专家" → country=中国
_COUNTRY_NAMES = ("中国", "美国", "英国", "日本", "德国", "法国", "加拿大", "澳大利亚",
                  "荷兰", "瑞士", "新加坡", "韩国", "印度", "意大利", "西班牙", "瑞典",
                  "丹麦", "比利时", "奥地利", "以色列", "巴西")
_COUNTRY_ONLY = re.compile(
    r"(?:我)?(?:现在|目前)?(?:只|仅|只要|只找|仅找|只推荐|只考虑)"
    r"(?:找|要|推荐|考虑|看)?\s*(?P<v>" + "|".join(_COUNTRY_NAMES) + r")")

_CONSTRAINT = re.compile(
    r"(?:我)?(?:只要|只想要|不要太|不要|别给|不要推荐|不想看)\s*(?P<v>[^，。；,;]{2,20})")

_PREFERENCE = re.compile(r"(?:我)?(?:更)?(?:喜欢|偏好|倾向于|prefer)\s*(?P<v>[^，。；,;]{2,20})")

_ORG = re.compile(r"(?:我)?(?:在|就职于|来自)\s*(?P<v>[^，。；,;]{2,24}?)\s*(?:工作|读博|读研|任教)")

# 英文（论文库以英文为主，用户也可能用英文说偏好）
_EN_FIELD = re.compile(r"(?:my (?:field|area|research) is|i work on|i study)\s+(?P<v>[A-Za-z][A-Za-z \-]{2,40})", re.I)
_EN_REDIRECT = re.compile(r"(?:i (?:no longer|don't) (?:work on|study)|i stopped working on)\s+(?P<old>[A-Za-z][A-Za-z \-]{2,40}?)"
                          r"(?:,| and| ;)?\s*(?:i (?:now )?(?:work on|study|focus on))\s+(?P<new>[A-Za-z][A-Za-z \-]{2,40})", re.I)


def _clean(v: str) -> str:
    return (v or "").strip().strip(_TOPIC_STOP).strip()


def extract(query: str) -> list[dict]:
    """纯规则抽取。返回按"先 expire 后 set"排序的动作列表。"""
    q = (query or "").strip()
    if not q:
        return []
    # 只抽"用户自述"：没有第一人称就不是在说自己的情况
    if not re.search(r"我|(?<![A-Za-z])[Ii](?![A-Za-z])|my", q):
        return []
    out: list[dict] = []

    m = _REDIRECT.search(q) or _EN_REDIRECT.search(q)
    if m:
        old, new = _clean(m.group("old")), _clean(m.group("new"))
        if old:
            out.append({"op": "expire", "key": "topic", "value": old})
        if new:
            out.append({"op": "set", "key": "topic", "value": new})
        return out

    for key, rx in (("field", _FIELD), ("topic", _ONLY), ("topic", _TOPIC),
                    ("preference", _PREFERENCE), ("org", _ORG)):
        m = rx.search(q)
        if m:
            v = _clean(m.group("v"))
            if v:
                out.append({"op": "set", "key": key, "value": v})
            break

    m = _COUNTRY_ONLY.search(q)
    if m:
        out.append({"op": "set", "key": "country", "value": m.group("v")})

    m = _CONSTRAINT.search(q)
    if m:
        v = _clean(m.group("v"))
        if v:
            out.append({"op": "set", "key": "constraint", "value": v})

    if not out:
        m = _EN_FIELD.search(q)
        if m:
            v = _clean(m.group("v"))
            if v:
                out.append({"op": "set", "key": "field", "value": v})
    return out


SYSTEM = (
    "从用户这句话里抽取**值得长期记住**的个人事实。输出 JSON："
    '{"facts":[{"op":"set|expire","key":"topic|field|constraint|preference|role|org","value":"..."}]}'
    "。只抽用户自己的情况（研究方向、偏好、限制、单位），不要抽问题本身；没有就返回空数组。"
)


def extract_llm(query: str) -> list[dict]:
    """可选：模型抽取。未配置/失败返回 []，由调用方退回规则。"""
    from app import llm
    if not llm.configured():
        return []
    res = llm.chat_json(SYSTEM, f"用户：{query}")
    if not res.get("ok"):
        return []
    data = res.get("data") or {}
    facts = data.get("facts")
    if not isinstance(facts, list):
        return []
    out = []
    for f in facts[:5]:
        if not isinstance(f, dict):
            continue
        op = str(f.get("op") or "set").lower()
        key = str(f.get("key") or "").lower()
        val = _clean(str(f.get("value") or ""))
        if op not in ("set", "expire") or key not in KEYS or not val:
            continue
        out.append({"op": op, "key": key, "value": val})
    return out


def apply(uid: str, query: str, sid: str = "", *, use_llm: bool | None = None) -> dict:
    """抽取并写入记忆。返回 {added, expired, source}。

    规则先跑：命中就不调模型（省钱）；没命中且开关打开才调 LLM。
    """
    import os

    from app import user_memory

    if not (uid or "").strip(): # 没有用户ID就不写 记忆必须知道属于哪个用户。
        return {"added": 0, "expired": 0, "source": "no_uid", "facts": []}
    facts = extract(query) # 规则先运行
    source = "rule"
    if not facts and (use_llm if use_llm is not None   # 根据开关决定是否调用模型
                      else os.environ.get("ST_MEM_LLM") == "1"):
        facts = extract_llm(query)
        source = "llm" if facts else "rule"
    if not facts:
        return {"added": 0, "expired": 0, "source": source, "facts": []}

    added = expired = 0
    written = []
    for f in facts:
        try:
            if f["op"] == "expire":
                n = user_memory.expire(uid, f["key"])
                expired += 1 if n else 0
                written.append({**f, "applied": bool(n)})
            else:
                r = user_memory.add_fact(uid, f["key"], f["value"], source=source)
                added += 1
                expired += 1 if r["expired_previous"] else 0
                written.append({**f, "id": r["id"], "applied": True})
        except Exception:
            continue
    return {"added": added, "expired": expired, "source": source, "facts": written}
