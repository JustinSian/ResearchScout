"""生成盲审样本：金标 20 条 + 扩充 21 条，输出 CSV + 一个离线标注页。

盲审原则：
  - 只给"问题 / 人名 / 机构 / 被引 / 标签 / 3 篇代表作"；
  - **不给**这个人是怎么被选进来的（金标 / 扩充）、也不给系统对他的评分；
  - 标注者只回答：这个人是不是"该问题领域的专家"（是 / 否 / 边界）。

用法：
  python -m app.build_audit --gold 20 --exp 21 --out eval/audit
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "expert_gold.jsonl"


def _titles(conn, pid: int, limit: int = 3) -> list[str]:
    rows = conn.execute(
        """SELECT w.title FROM work_authors wa JOIN works w ON w.wid=wa.wid
           WHERE wa.pid=? ORDER BY w.cites DESC LIMIT ?""", (int(pid), limit)).fetchall()
    return [r["title"] or "" for r in rows]


def build_gold(n: int, seed: int = 13) -> list[dict]:
    from app.db import connect
    gold = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    rnd = random.Random(seed)
    rnd.shuffle(gold)
    conn = connect()
    out = []
    try:
        for g in gold:
            pids = [int(x) for x in g.get("gold_pids") or []]
            if not pids:
                continue
            pid = rnd.choice(pids)
            p = conn.execute(
                """SELECT p.pid, p.name, p.last_inst, p.cited_by_count, p.topics
                   FROM people p WHERE p.pid=?""", (pid,)).fetchone()
            if not p:
                continue
            try:
                tags = json.loads(p["topics"] or "[]")[:6]
            except Exception:
                tags = []
            out.append({
                "row_id": f"G{g['id']}-{pid}",
                "query": g["query"],
                "name": p["name"],
                "institution": p["last_inst"] or "",
                "cited_by": p["cited_by_count"],
                "topics": " / ".join(tags),
                "top3_titles": " || ".join(t[:110] for t in _titles(conn, pid)),
                "label_is_expert": "",
                "note": "",
            })
            if len(out) >= n:
                break
    finally:
        conn.close()
    return out


def build_expansion(n: int) -> list[dict]:
    src = ROOT / "eval" / "audit_sample.csv"
    if not src.exists():
        return []
    rows = []
    with src.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "row_id": "X" + r["row_id"],
                "query": r["query_topic"],
                "name": r["name"],
                "institution": r.get("institution") or "",
                "cited_by": r.get("cited_by") or "",
                "topics": "",
                "top3_titles": r.get("top3_titles") or "",
                "label_is_expert": "",
                "note": "",
            })
    return rows[:n]


HTML = """<!doctype html><meta charset="utf-8">
<title>专家盲审 · ResearchScout</title>
<style>
body{font-family:-apple-system,"Segoe UI",system-ui,sans-serif;max-width:980px;margin:32px auto;padding:0 16px;color:#1a1a1a;background:#fafafa}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#666;font-size:13px;margin-bottom:16px}
.card{background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.q{font-size:13px;color:#555;margin-bottom:10px}
.name{font-size:22px;font-weight:600}
.meta{color:#666;font-size:13px;margin:6px 0 14px}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.tag{background:#f0f4ff;color:#345;border-radius:12px;padding:2px 10px;font-size:12px}
.titles{font-size:13px;line-height:1.7;color:#333;border-left:3px solid #eee;padding-left:12px;margin:14px 0}
.btns{display:flex;gap:10px;margin-top:18px}
button{font-size:15px;padding:10px 20px;border-radius:8px;border:1px solid #d0d0d0;background:#fff;cursor:pointer}
button.yes{background:#e8f5ec;border-color:#9fd0ac}
button.no{background:#fdecec;border-color:#e3a9a9}
button.mid{background:#fff7e6;border-color:#e6cf9a}
button:disabled{opacity:.4;cursor:default}
.bar{height:6px;background:#eee;border-radius:4px;overflow:hidden;margin:10px 0 20px}
.bar>i{display:block;height:100%;background:#6c8cff;width:0}
.foot{display:flex;gap:12px;align-items:center;margin-top:18px;font-size:13px;color:#666}
a{color:#3b6cf0}
kbd{border:1px solid #ccc;border-bottom-width:2px;border-radius:4px;padding:0 5px;font-size:12px}
</style>
<h1>专家盲审</h1>
<div class="sub">判断：这个人是不是「该问题」领域的专家。不看来源、不看系统评分。<br>
快捷键：<kbd>1</kbd> 是 · <kbd>2</kbd> 否 · <kbd>3</kbd> 边界 · <kbd>←</kbd> 上一条</div>
<div class="bar"><i id="bar"></i></div>
<div class="card" id="card"></div>
<div class="foot">
  <span id="prog"></span>
  <button onclick="exportCsv()">导出 CSV</button>
  <button onclick="resetAll()">清空重来</button>
  <span>进度存在浏览器本地，刷新不丢</span>
</div>
<script id="audit-data" type="application/json">__DATA__</script>
<script>
const ROWS = JSON.parse(document.getElementById("audit-data").textContent);
const KEY = "rs_audit_v1";
const mem = {};                       // file:// 下 localStorage 可能不可用，兜底
function lsGet(k){ try { return localStorage.getItem(k); } catch(e) { return mem[k] || null; } }
function lsSet(k,v){ try { localStorage.setItem(k,v); } catch(e) { mem[k]=v; } }
let state = {};
try { state = JSON.parse(lsGet(KEY) || "{}"); } catch(e) { state = {}; }
let idx = 0;
try { idx = parseInt(lsGet(KEY+"_i") || "0", 10) || 0; } catch(e) { idx = 0; }
function save(){ lsSet(KEY, JSON.stringify(state)); lsSet(KEY+"_i", idx); }
function render(){
  const r = ROWS[idx];
  document.getElementById("card").innerHTML = `
    <div class="q">问题：${esc(r.query)}</div>
    <div class="name">${esc(r.name)}</div>
    <div class="meta">${esc(r.institution || "机构未知")} · 被引 ${esc(String(r.cited_by))}</div>
    ${r.topics ? `<div class="tags">${r.topics.split(" / ").map(t=>`<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
    <div class="titles">${esc(r.top3_titles).split(" || ").map(t=>`<div>· ${t}</div>`).join("")}</div>
    <div class="btns">
      <button class="yes" onclick="mark('是')">是（1）</button>
      <button class="no" onclick="mark('否')">否（2）</button>
      <button class="mid" onclick="mark('边界')">边界（3）</button>
    </div>`;
  const done = Object.keys(state).length;
  document.getElementById("prog").textContent = `${idx+1} / ${ROWS.length} · 已标 ${done}`;
  document.getElementById("bar").style.width = (100*done/ROWS.length) + "%";
}
function fail(e){
  document.getElementById("card").innerHTML =
    '<div style="color:#b00">页面脚本出错：' + esc(String(e && e.message || e)) + '</div>';
}
function mark(v){ state[ROWS[idx].row_id] = v; save(); if(idx < ROWS.length-1){ idx++; save(); render(); } else { render(); alert("已到最后一条，点导出 CSV"); } }
function esc(s){ return (s||"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function exportCsv(){
  const head = ["row_id","query","name","institution","cited_by","label_is_expert"];
  const lines = [head.join(",")];
  for(const r of ROWS){
    const cells = [r.row_id,r.query,r.name,r.institution,r.cited_by,state[r.row_id]||""];
    lines.push(cells.map(c=>`"${String(c==null?"":c).replace(/"/g,'""')}"`).join(","));
  }
  const blob = new Blob(["\\ufeff"+lines.join("\\n")], {type:"text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "audit_labeled.csv"; a.click();
}
function resetAll(){ if(confirm("清空全部标注？")){ state={}; idx=0; save(); render(); } }
document.addEventListener("keydown", e=>{
  if(e.key==="1") mark("是"); else if(e.key==="2") mark("否"); else if(e.key==="3") mark("边界");
  else if(e.key==="ArrowLeft" && idx>0){ idx--; save(); render(); }
});
try { render(); } catch(e) { fail(e); }
window.addEventListener("error", ev => fail(ev.error || ev.message));
</script>
"""


def anonymize(rows: list[dict], seed: int = 5) -> list[dict]:
    """把 row_id 换成随机不透明 ID——G/X 前缀本身就是来源泄漏。"""
    rnd = random.Random(seed)
    mapping = {}
    for r in rows:
        tok = "R%08x" % rnd.getrandbits(32)
        mapping[tok] = r.pop("_source_ref", r.get("row_id"))
        r["row_id"] = tok
    return rows, mapping


def write_page(rows: list[dict], out_dir: Path) -> Path:
    payload = json.dumps([
        {k: r[k] for k in ("row_id", "query", "name", "institution",
                           "cited_by", "topics", "top3_titles")} for r in rows
    ], ensure_ascii=False)
    page = out_dir / "review.html"
    page.write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
    return page


def write_text(rows: list[dict], path: Path) -> None:
    """纯文本后备：浏览器打不开时，直接照这个列表回复标注即可。"""
    lines = ["专家盲审（共 %d 条）" % len(rows),
             "判定：是 / 否 / 边界；回复格式：编号+判定，如 1是 2否 3边界", ""]
    for i, r in enumerate(rows, 1):
        titles = " | ".join(t[:70] for t in (r.get("top3_titles") or "").split(" || ")[:3])
        lines.append(f"{i:>2}. [{r['row_id']}] 问题：{r['query']}")
        lines.append(f"    {r['name']} · {r.get('institution') or '机构未知'} · 被引 {r.get('cited_by')}")
        if r.get("topics"):
            lines.append(f"    标签：{r['topics']}")
        lines.append(f"    代表作：{titles or '（库内无论文）'}")
        lines.append("")
    path.write_text(chr(10).join(lines), encoding="utf-8")


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=int, default=20)
    ap.add_argument("--exp", type=int, default=21)
    ap.add_argument("--out", default="eval/audit")
    a = ap.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    gold = build_gold(a.gold)
    exp = build_expansion(a.exp)
    write_csv(gold, out / "gold_audit.csv")
    write_csv(exp, out / "expansion_audit.csv")
    rows = [dict(r) for r in gold + exp]
    for r in rows:
        r["_source_ref"] = r["row_id"]
    # 先匿名化（不透明 ID），再打乱顺序：避免 ID 前缀和排序泄漏来源
    rows, mapping = anonymize(rows)
    random.Random(99).shuffle(rows)
    page = write_page(rows, out)
    write_text(rows, out / "review.txt")
    import json as _json
    (out / "mapping.json").write_text(
        _json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  金标 {len(gold)} 条 · 扩充 {len(exp)} 条 → {page}")
    print(f"  私有映射（含来源）→ {out / 'mapping.json'}")


if __name__ == "__main__":
    main()
