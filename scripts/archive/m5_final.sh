#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY=.venv/bin/python

echo "== 全量单测 =="
$PY -m pytest tests -q 2>&1 | tail -2

echo
echo "== 最终验收（公网） =="
URL=$(cat logs/public_url.txt)
echo "  URL: $URL"
for p in /health /stats; do
  echo -n "  $p: "; curl -s -o /dev/null -w "%{http_code}\n" -m 25 "$URL$p"
done
echo -n "  /search: "; curl -s -m 30 --get --data-urlencode "q=graph neural network" \
  "$URL/search?kind=people&limit=2" | $PY -c "import json,sys;d=json.load(sys.stdin);print([i['name'] for i in d['items']])"
echo -n "  /chat 拒答: "; curl -s -m 60 --get --data-urlencode "q=找研究 quantum gravity phenology 的专家" \
  "$URL/chat" | $PY -c "import json,sys;d=json.load(sys.stdin);print('refused=',d['refused'])"
echo -n "  SSE: "; curl -s -N -m 40 --get --data-urlencode "q=介绍一下 Yoshua Bengio" \
  "$URL/chat/stream" | grep -c '^event:' | xargs -I{} echo "{} 个事件"
echo -n "  写接口无 key: "; curl -s -o /dev/null -w "%{http_code}\n" -m 25 -X POST \
  "$URL/review/decide?pid=1&decision=accept"

echo
echo "== 项目规模 =="
echo "  app 代码行: $(cat app/*.py | wc -l)"
echo "  py 文件: $(ls app/*.py | wc -l)  测试: $(ls tests/*.py 2>/dev/null | wc -l)"
echo "  报告: $(ls docs/*.md | wc -l) 份"
echo "  DB: $(du -sh data/scholar.db | cut -f1)"
