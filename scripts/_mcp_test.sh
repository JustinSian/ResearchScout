cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
echo "== MCP 握手 + 工具清单 =="
printf '%s\n%s\n%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 '{"jsonrpc":"2.0","id":3,"method":"ping"}' \
 | .venv/bin/python -m app.mcp_server 2>/dev/null \
 | .venv/bin/python -c "
import json,sys
for line in sys.stdin:
    d=json.loads(line)
    if d.get('id')==1: print('  initialize:', d['result']['serverInfo'], d['result']['protocolVersion'])
    if d.get('id')==2: print('  tools:', [t['name'] for t in d['result']['tools']])
    if d.get('id')==3: print('  ping:', d['result'])"
echo
echo "== 调用工具 =="
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_profile","arguments":{"pid":527}}}' \
 '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"health","arguments":{}}}' \
 '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"nope","arguments":{}}}' \
 | .venv/bin/python -m app.mcp_server 2>/dev/null \
 | .venv/bin/python -c "
import json,sys
for line in sys.stdin:
    d=json.loads(line)
    if d.get('id')==2:
        t=d['result']['content'][0]['text']; print('  get_profile:', t[:150].replace(chr(10),' '))
    if d.get('id')==3:
        t=json.loads(d['result']['content'][0]['text']); print('  health:', t['ok'], t['stats'])
    if d.get('id')==4: print('  未知工具:', d.get('error',{}).get('message'))"