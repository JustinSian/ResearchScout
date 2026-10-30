#!/usr/bin/env bash
EMAIL="your-email@example.com"
echo "== 1. 申请 OpenAlex API key（POST /api-keys） =="
curl -s -m 40 -X POST "https://api.openalex.org/api-keys" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\"}" -o /tmp/key.json -w "HTTP %{http_code}\n"
echo "--- 响应 ---"
head -c 600 /tmp/key.json; echo
echo
echo "== 2. 如果返回了 key，写入 .env 并验证额度 =="
python3 - <<'PY'
import json, os, re
try:
    d = json.load(open('/tmp/key.json'))
except Exception as e:
    print("  响应不是 JSON:", e); raise SystemExit
key = d.get("api_key") or d.get("key")
if not key:
    print("  没有直接返回 key，字段:", list(d)[:8])
    raise SystemExit
print("  key 前 8 位:", key[:8] + "…", " 长度", len(key))
env = os.path.expanduser("~/scholar-trust/.env")
lines = [l for l in open(env).read().splitlines() if not l.startswith("ST_OPENALEX_KEY=")] if os.path.exists(env) else []
lines.append(f"ST_OPENALEX_KEY={key}")
open(env, "w").write("\n".join(lines) + "\n")
print("  已写入 .env")
PY
echo
echo "== 3. 用 key 请求一次，看额度 =="
KEY=$(grep '^ST_OPENALEX_KEY=' "$HOME/scholar-trust/.env" 2>/dev/null | cut -d= -f2)
if [ -n "$KEY" ]; then
  curl -s -D /tmp/oa_k.txt -o /dev/null -m 30 \
    "https://api.openalex.org/works?filter=doi:10.1038/nature14539&select=id&api_key=$KEY"
  grep -iE "x-ratelimit-(limit|remaining|limit-usd|remaining-usd)|HTTP/" /tmp/oa_k.txt | sed 's/^/  /'
else
  echo "  没有 key，跳过"
fi
