"""Skills：把"口径"从代码里挪到 Markdown，运行时加载。

对标简历里的"Skills 技能系统：将规则模板、SOP、Prompt 打包为可复用 Skill，
采取渐进式披露（progressive disclosure）"。

渐进式披露在本项目的含义：
  1. 先只读 front-matter（name/when/权重）→ 决定是否加载；
  2. 命中后再读正文（SOP）→ 注入到对应步骤；
  3. 正文不进主上下文，只在报告里引用名称与版本。

这样改口径不需要改代码：把 `weights` 里的 0.5 改成 0.6，下一次任务生效。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills"


def _parse_front(text: str) -> tuple[dict, str]:
    """极简 front-matter 解析（避免为 3 个字段引入 PyYAML）。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("{") and v.endswith("}"):
            try:
                meta[k] = json.loads(v)
                continue
            except Exception:
                pass
        try:
            meta[k] = float(v) if re.fullmatch(r"-?\d+(\.\d+)?", v) else int(v)
        except Exception:
            meta[k] = v
    return meta, m.group(2)


def list_skills() -> list[dict]:
    """只返回元信息（渐进式披露第一层）。"""
    out = []
    if not SKILL_DIR.exists():
        return out
    for p in sorted(SKILL_DIR.glob("*.md")):
        meta, _body = _parse_front(p.read_text(encoding="utf-8"))
        out.append({"name": meta.get("name") or p.stem, "version": meta.get("version"),
                    "when": meta.get("when"), "file": p.name,
                    "keys": [k for k in meta if k not in ("name", "version", "when")]})
    return out


def load_skill(name: str) -> dict | None:
    """加载正文（第二层）。"""
    if not SKILL_DIR.exists():
        return None
    for p in SKILL_DIR.glob("*.md"):
        text = p.read_text(encoding="utf-8")
        meta, body = _parse_front(text)
        if (meta.get("name") or p.stem) == name:
            return {"name": name, "meta": meta, "body": body}
    return None


def param(name: str, key: str, default=None):
    """取 skill 的某个参数（带默认值，缺失时不影响主流程）。"""
    s = load_skill(name)
    if not s:
        return default
    return s["meta"].get(key, default)


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(list_skills(), ensure_ascii=False, indent=1))
    print("weights:", param("reviewer-matching", "weights"))
