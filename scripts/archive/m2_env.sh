#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
echo "== 现有模型 =="
ls -la "$HOME/med-compliance-agent/data/models/"*/ 2>/dev/null | head -10
echo
echo "== 网络：HF 各入口 =="
for u in https://huggingface.co https://hf-mirror.com https://hf-mirror.com/BAAI/bge-small-en-v1.5/resolve/main/config.json; do
  code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$u"); echo "  [$code] $u"
done
echo
echo "== 本地是否有 sklearn（可做 TF-IDF+SVD 兜底） =="
.venv/bin/python -c "import sklearn;print('  sklearn', sklearn.__version__)" 2>&1 | tail -1
echo "== 是否已有 sentence-transformers / transformers =="
.venv/bin/python -c "
import importlib
for m in ('sentence_transformers','transformers','torch'):
    try:
        mod=importlib.import_module(m); print('  ',m,getattr(mod,'__version__','?'))
    except Exception: print('  ',m,'缺失')" 2>&1 | tail -4
