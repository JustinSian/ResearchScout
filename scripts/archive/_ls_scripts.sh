cd "$HOME/scholar-trust" || exit 1
echo "脚本总数: $(ls scripts/ | wc -l)"
echo "== 按前缀分组 =="
ls scripts/ | sed 's/_.*//; s/[0-9].*//' | sort | uniq -c | sort -rn | head -12
echo "== 非 diag/_ 的脚本 =="
ls scripts/ | grep -v '^diag' | grep -v '^_' | head -30