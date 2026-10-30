#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY=.venv/bin/python

echo "== 生成并写入 ST_API_KEY =="
KEY="st_$(.venv/bin/python -c 'import secrets;print(secrets.token_urlsafe(24))')"
if grep -qE '^ST_API_KEY=' .env 2>/dev/null; then
  sed -i "s|^ST_API_KEY=.*|ST_API_KEY=$KEY|" .env
else
  echo "ST_API_KEY=$KEY" >> .env
fi
echo "  已写入 .env（长度 ${#KEY}）"

echo "== 重启（带 key） =="
bash scripts/start_api.sh >/dev/null 2>&1; sleep 2

echo "== 1. 未配置/错误 key 的写操作应被拒 =="
PID=$(curl -s -m 10 "http://127.0.0.1:8020/review/queue?limit=1" | $PY -c "import json,sys;print(json.load(sys.stdin)['items'][0]['pid'])")
echo -n "  无 key: "; curl -s -o /dev/null -w "%{http_code}\n" -m 10 -X POST "http://127.0.0.1:8020/review/decide?pid=$PID&decision=accept"
echo -n "  错 key: "; curl -s -o /dev/null -w "%{http_code}\n" -m 10 -X POST -H "X-API-Key: wrong" "http://127.0.0.1:8020/review/decide?pid=$PID&decision=accept"
echo -n "  对 key: "; curl -s -o /dev/null -w "%{http_code}\n" -m 10 -X POST -H "X-API-Key: $(grep '^ST_API_KEY=' .env | cut -d= -f2)" "http://127.0.0.1:8020/review/decide?pid=$PID&decision=accept&note=M5-auth"

echo "== 2. 文档应关闭 =="
for p in /docs /openapi.json /redoc; do
  echo -n "  $p: "; curl -s -o /dev/null -w "%{http_code}\n" -m 8 "http://127.0.0.1:8020$p"
done

echo "== 3. 异常输入不泄漏堆栈 =="
curl -s -m 20 --get --data-urlencode "q=<script>alert(1)</script>" "http://127.0.0.1:8020/search" \
  | grep -cE "Traceback|sqlite|SELECT" || echo "  无堆栈"
echo -n "  超长输入: "; $PY -c "print('a'*9000)" | curl -s -o /dev/null -w "%{http_code}\n" -m 20 --get --data-urlencode "q@-" "http://127.0.0.1:8020/people"
