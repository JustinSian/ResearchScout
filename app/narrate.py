"""把确定性答案"讲成人话"：叙述层。

没有 LLM，但"像 Agent"不等于"必须有大模型"——
关键是**说清楚现在在做什么、依据是什么、为什么这样排**。

这里只做模板化叙述，每条都绑定真实数据（不编造），
并显式标注"无大模型"，避免让人误以为有生成式推理。
"""
from __future__ import annotations

_TOOL_ZH = {
    "resolve": "在库里定位专家",
    "dossier": "读取他的全量成果档案",
    "research_area": "统计近五年与早期的研究方向",
    "relation": "检查他们之间的合作边",
    "collaborators": "按共同论文数排出合作者",
    "profile": "拉出画像与代表作",
    "compare": "并排比较两位专家",
    "find_expert": "按主题检索相关专家",
    "works": "检索论文",
    "extract_topic": "把稿件主题翻译成检索词",
    "researcher": "多通道召回候选审稿人",
    "analyst": "算候选人的指标与主题分布",
    "score": "按主题匹配与活跃度打分",
    "verifier": "逐条核对结论能否回溯",
    "contrast": "对比两位的近五年主题",
    "verify": "校验事实可回溯性",
    "report": "写成带证据的尽调报告",
    "search": "检索候选",
    "memory": "命中上次的结论",
}


def opening(query: str, intent: str) -> str:
    return {
        "compare_experts": f"收到。你问的是「{query}」，我按对比来做：先分别定位两位专家，"
                            f"再把各自的全量成果拆成近五年与早期两组主题，最后并排给结论。",
        "reviewer_match": f"收到。你问的是「{query}」，这是个审稿人匹配问题："
                          f"我先抽取稿件主题，再召回候选、算指标、做领域校验，最后给排名。",
        "expert_dossier": f"收到。你问的是「{query}」，我会读这位专家的完整档案，"
                          f"而不是只搜关键词。",
        "search": f"收到。你问的是「{query}」，我先做检索再判断。",
    }.get(intent, f"收到「{query}」，我开始拆解这个问题。")


def step_narration(name: str, tool: str) -> str:
    act = _TOOL_ZH.get(tool, tool)
    return f"{name}：{act}…"


def after_step(summary: str) -> str:
    """把工具的结构化摘要讲成一句人话。"""
    s = (summary or "").strip()
    if not s:
        return "这一步没有返回内容。"
    return f"这一步得到：{s}"


def closing(refused: bool, reason: str = "", citations: int = 0) -> str:
    if refused:
        return "资料不足，我不猜——这是拒答，不是失败。"
    if citations == 0:
        return "结论在上面，但没有挂到具体证据，可信度有限。"
    return f"以上每条结论都能点回证据（{citations} 条引用）。需要我把这些人拉到图谱里看关系吗？"


def caveat(intent: str) -> str:
    if intent == "reviewer_match":
        return "提醒：打分只反映「主题匹配 + 活跃度」，不是学术水平评价；同名不同人已由校验步骤剔除。"
    if intent == "compare_experts":
        return "提醒：主题来自论文标题词频，是研究重心的近似描述，不代表水平高低。"
    return "提醒：本系统没有大模型，回答由确定性规则 + 数据库事实拼装，不会编造。"
