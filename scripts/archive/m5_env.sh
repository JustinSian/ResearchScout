#!/usr/bin/env bash
echo "== cloudflared 是否可用（复用医药项目那套） =="
ls -la "$HOME/med-compliance-agent/bin" 2>/dev/null | head -5
command -v cloudflared || ls "$HOME"/cloudflared "$HOME"/bin/cloudflared 2>/dev/null
echo "== 现有隧道进程 =="
pgrep -fa cloudflared | head -3 || echo "  无"
echo "== 端口 8020 监听 =="
ss -ltn 2>/dev/null | grep 8020 || echo "  未监听"
