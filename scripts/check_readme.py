#!/usr/bin/env python
"""README 数字一致性校验：改代码忘了改文档时，这里会红。

校验项（只校验 README 里**真的写了**的数字，不强制全写）：
  - 测试徽章 tests-<N>%20passed 与正文 "<N> 项测试"
  - "<N> 个端点"
  - "<N> 视图"
  - 必备章节是否存在
  - LICENSE / .env.example / DEPLOY.md 是否存在

用法：
    python scripts/check_readme.py          # 打印对照表，不一致则退出码 1
    python scripts/check_readme.py --quiet  # 只在不一致时输出
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SECTIONS = ("快速开始", "架构", "测试与评测", "Roadmap", "License")
REQUIRED_FILES = ("LICENSE", ".env.example", "DEPLOY.md", "README.md")


def _count_test_functions() -> int:
    n = 0
    for p in sorted((ROOT / "tests").glob("test_*.py")):
        n += len(re.findall(r"^def test_", p.read_text(encoding="utf-8"), re.M))
    return n


def _count_endpoints() -> int:
    text = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
    return len(re.findall(r"^@app\.(?:get|post)", text, re.M))


def _count_views() -> int:
    text = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    return len(set(re.findall(r'id="v-([a-z]+)"', text)))


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    readme_path = ROOT / "README.md"
    if not readme_path.exists():
        print("README.md 不存在")
        return 1
    readme = readme_path.read_text(encoding="utf-8")

    actual = {
        "tests": _count_test_functions(),
        "endpoints": _count_endpoints(),
        "views": _count_views(),
    }

    checks: list[tuple[str, int, int]] = []   # (描述, README 写的, 实际)

    m = re.search(r"tests-(\d+)%20passed", readme)
    if m:
        checks.append(("徽章 tests", int(m.group(1)), actual["tests"]))
    m = re.search(r"(\d+)\s*项测试", readme)
    if m:
        checks.append(("正文 项测试", int(m.group(1)), actual["tests"]))
    for m in re.finditer(r"(\d+)\s*个端点", readme):
        checks.append(("端点", int(m.group(1)), actual["endpoints"]))
    for m in re.finditer(r"(\d+)\s*视图", readme):
        checks.append(("视图", int(m.group(1)), actual["views"]))

    problems: list[str] = []
    for label, claimed, real in checks:
        if claimed != real:
            problems.append(f"{label}: README 写 {claimed}，实际 {real}")

    for sec in REQUIRED_SECTIONS:
        if sec not in readme:
            problems.append(f"缺少章节：{sec}")
    for f in REQUIRED_FILES:
        if not (ROOT / f).exists():
            problems.append(f"缺少文件：{f}")

    if not quiet:
        print("README 数字对照：")
        for label, claimed, real in checks:
            flag = "ok " if claimed == real else "DIFF"
            print(f"  [{flag}] {label}: README={claimed} 实际={real}")
        print(f"  （实际统计：测试 {actual['tests']} / 端点 {actual['endpoints']} / 视图 {actual['views']}）")

    if problems:
        print("\nREADME 与代码不一致：")
        for p in problems:
            print("  - " + p)
        print("\n修正后再提交；规范见 docs/readme-standard.md")
        return 1

    if not quiet:
        print("\nREADME 与代码一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
