#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
echo "== works 表字段 =="
.venv/bin/python -c "
from app.db import connect
c=connect()
print([r[1] for r in c.execute('PRAGMA table_info(works)')])
print('示例:', dict(c.execute('SELECT wid,doi,title,primary_location FROM works LIMIT 1').fetchone() or {}))"
echo
echo "== PDF 解析库 =="
.venv/bin/python -c "
import importlib
for m in ('pypdf','fitz','pdfminer'):
    try:
        mod=importlib.import_module(m); print('  ',m, getattr(mod,'__version__','ok'))
    except Exception: print('  ',m,'缺失')"
echo
echo "== 取一条 best_oa_location 看有没有 pdf_url =="
curl -s -m 30 "https://api.openalex.org/works?filter=doi:10.1038/nature14539&select=id,doi,best_oa_location,open_access&mailto=scholartrust@example.com" \
  | .venv/bin/python -c "
import json,sys
d=json.load(sys.stdin)['results'][0]
print('  open_access:', d.get('open_access'))
print('  best_oa:', d.get('best_oa_location'))"
echo
echo "== PDF 下载可达性（arxiv 与常见 OA） =="
for u in "https://arxiv.org/pdf/1706.03762" "https://www.nature.com/articles/nature14539.pdf"; do
  echo -n "  $u → "; curl -s -o /dev/null -m 30 -w "%{http_code} %{content_type} %{size_download}B\n" -L "$u"
done
