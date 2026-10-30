"""健康检查守护进程：定期跑 /health/trust 并落盘，异常时写告警文件。

为什么不用 cron：这台机器没有 sudo，crontab 是否可用不确定；
用 `setsid nohup python -m app.health_watch` 自管循环最简单，且能看到日志。

用法：
  python -m app.health_watch --once                # 跑一次（给 CI/脚本用）
  python -m app.health_watch --interval 21600      # 每 6 小时一次（默认）
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.health import trust_health

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "health.jsonl"
ALERT = ROOT / "logs" / "health_ALERT.txt"


def check_once(log=print) -> dict:
    d = trust_health()
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": d["ok"],
           "warnings": d["warnings"], "network_ratio": d["network_ratio"],
           "checks": [{"name": c["name"], "status": c["status"], "value": c["value"]}
                      for c in d["checks"]]}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if d["ok"]:
        if ALERT.exists():
            ALERT.unlink()
        log(f"  {rec['ts']} ok（背书占比 {d['network_ratio']:.2%}）")
    else:
        bad = [c for c in d["checks"] if c["status"] != "ok"]
        msg = "\n".join(f"- {c['label']}: {c['value']}（限制 {c['limit']}）{c['hint']}"
                        for c in bad)
        ALERT.write_text(f"{rec['ts']} 健康检查未通过\n{msg}\n", encoding="utf-8")
        log(f"  {rec['ts']} ⚠ {d['warnings']} 项异常，已写 {ALERT.name}")
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=6 * 3600)
    a = ap.parse_args()
    if a.once:
        check_once()
        return
    print(f"守护进程启动，间隔 {a.interval}s，日志 {LOG}", flush=True)
    while True:
        try:
            check_once(log=lambda s: print(s, flush=True))
        except Exception as exc:          # 单次失败不能让守护进程退出
            print(f"  检查失败: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
