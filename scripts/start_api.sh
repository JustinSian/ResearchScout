#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY="$HOME/scholar-trust/.venv/bin/python"
PORT="${PORT:-8020}"

[ -f logs/api.pid ] && { kill -- -"$(cat logs/api.pid)" 2>/dev/null || true; sleep 2; }
setsid nohup "$PY" -m uvicorn app.api:app --host 0.0.0.0 --port "$PORT" --workers 1 \
  > logs/api.log 2>&1 < /dev/null &
echo $! > logs/api.pid
for i in $(seq 1 60); do
  curl -fsS -m 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && {
    echo "就绪 ${i}s → http://127.0.0.1:${PORT}"; break; }
  sleep 1
done

echo
echo "== /stats =="
curl -s -m 10 "http://127.0.0.1:${PORT}/stats"; echo
echo "== /people?q=machine learning =="
curl -s -m 10 "http://127.0.0.1:${PORT}/people?q=machine%20learning&limit=5" \
  | "$PY" -c "import json,sys;d=json.load(sys.stdin);print('count',d['count']);[print('  ',i['name'],'|',i['last_inst'],'| works',i['works_count'],'| cites',i['cited_by_count']) for i in d['items']]"
echo "== /people?q=Yoshua Bengio =="
curl -s -m 10 "http://127.0.0.1:${PORT}/people?q=Yoshua%20Bengio&limit=3" \
  | "$PY" -c "import json,sys;d=json.load(sys.stdin);[print('  ',i['pid'],i['name'],'| works',i['works_count'],'| cites',i['cited_by_count'],'| conf',i['confidence']) for i in d['items']]"
echo "== /works?q=transformer =="
curl -s -m 10 "http://127.0.0.1:${PORT}/works?q=transformer&limit=3" \
  | "$PY" -c "import json,sys;d=json.load(sys.stdin);[print('  ',i['year'],i['title'][:60],'| cites',i['cites']) for i in d['items']]"
