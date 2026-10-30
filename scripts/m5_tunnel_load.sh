#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY=.venv/bin/python

echo "== 启动 cloudflared 隧道（8020） =="
if pgrep -f "cloudflared tunnel --url http://127.0.0.1:8020" >/dev/null; then
  echo "  已在运行"
else
  setsid nohup "$HOME/bin/cloudflared" tunnel --url http://127.0.0.1:8020 \
    --no-autoupdate --loglevel info > logs/tunnel.log 2>&1 < /dev/null &
  sleep 12
fi
URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' logs/tunnel.log | head -1)
echo "  公网地址: $URL"
[ -n "$URL" ] && echo "$URL" > logs/public_url.txt
if [ -n "$URL" ]; then
  echo -n "  公网 /health: "; curl -s -o /dev/null -w "%{http_code}\n" -m 25 "$URL/health"
fi

echo
echo "== 压测（本机直连，避开隧道噪声） =="
$PY scripts/loadtest.py 2>&1 | tail -10

echo
echo "== 资源 =="
ps -eo rss,cmd | grep "uvicorn app.api:app" | grep -v grep \
  | awk '{s+=$1} END {printf "  uvicorn RSS %.0f MB\n", s/1024}'
du -sh data/scholar.db | awk '{print "  DB ", $1}'
