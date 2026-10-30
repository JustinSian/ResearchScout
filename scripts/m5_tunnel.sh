#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
mkdir -p logs
echo "== 启动隧道（新进程） =="
setsid nohup "$HOME/bin/cloudflared" tunnel --url http://127.0.0.1:8020 \
  --no-autoupdate --loglevel info > logs/tunnel.log 2>&1 < /dev/null &
sleep 15
URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' logs/tunnel.log | head -1)
echo "  地址: $URL"
if [ -n "$URL" ]; then
  echo "$URL" > logs/public_url.txt
  echo -n "  /health: "; curl -s -m 30 -o /dev/null -w "%{http_code}\n" "$URL/health"
  echo -n "  /stats : "; curl -s -m 30 "$URL/stats" | head -c 160; echo
  echo -n "  /search: "; curl -s -m 30 --get --data-urlencode "q=graph neural network" \
    "$URL/search?kind=people&limit=3" | .venv/bin/python -c "
import json,sys;d=json.load(sys.stdin);print([i['name'] for i in d['items']])"
  echo -n "  /chat  : "; curl -s -m 60 --get --data-urlencode "q=谁和 Yoshua Bengio 合作过" \
    "$URL/chat" | .venv/bin/python -c "
import json,sys;d=json.load(sys.stdin);print('refused=',d['refused'],'cites=',len(d['citations']))"
  echo -n "  写接口无 key: "; curl -s -o /dev/null -w "%{http_code}\n" -m 30 -X POST "$URL/review/decide?pid=1&decision=accept"
fi
