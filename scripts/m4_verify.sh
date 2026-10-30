#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
bash scripts/start_api.sh >/dev/null 2>&1; sleep 1
PY=.venv/bin/python

echo "== /agent/tools =="
curl -s -m 10 http://127.0.0.1:8020/agent/tools | $PY -c "
import json,sys;d=json.load(sys.stdin)
print('  tools:', [t['name'] for t in d['tools']], 'refuse_threshold', d['refuse_threshold'])"

echo
echo "== /chat（工具类 + 拒答类） =="
for q in "谁和 Yoshua Bengio 合作过" "找研究 graph neural network 的专家" "找研究 quantum gravity phenology 的专家"; do
  curl -s -m 60 --get --data-urlencode "q=$q" http://127.0.0.1:8020/chat \
    | $PY -c "
import json,sys
d=json.load(sys.stdin)
print('  Q:', d['query'][:30])
print('     tool:', [p['tool'] for p in d['plan']], 'refused:', d['refused'], 'cites:', len(d['citations']))
print('     A:', d['answer'][:90].replace(chr(10),' | '))"
done

echo
echo "== SSE =="
curl -s -N -m 60 --get --data-urlencode "q=介绍一下 Yoshua Bengio" \
  http://127.0.0.1:8020/chat/stream | head -8
echo
echo "== 单测 + /stats =="
$PY -m pytest tests -q 2>&1 | tail -2
curl -s -m 8 http://127.0.0.1:8020/stats; echo
