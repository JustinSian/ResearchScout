#!/usr/bin/env bash
set -e
cd "$HOME/scholar-trust" || exit 1
D=data/models/bge-small-en
mkdir -p "$D"
BASE=https://hf-mirror.com/BAAI/bge-small-en-v1.5/resolve/main
for f in config.json model.safetensors tokenizer.json tokenizer_config.json vocab.txt special_tokens_map.json; do
  if [ ! -s "$D/$f" ]; then
    echo "  下载 $f"
    curl -sL --retry 4 --retry-delay 2 -m 300 -o "$D/$f" "$BASE/$f" || echo "  失败: $f"
  else
    echo "  已存在 $f"
  fi
done
echo "== 文件大小 =="
ls -la "$D" | awk '{print "  ", $5, $9}'
echo "== 能否加载 =="
export PYTHONPATH="$PWD"
.venv/bin/python - <<'PY'
import time
import torch
from transformers import AutoModel, AutoTokenizer

d = "data/models/bge-small-en"
tok = AutoTokenizer.from_pretrained(d)
model = AutoModel.from_pretrained(d).eval()
texts = ["graph neural networks for molecules"] * 32
t0 = time.time()
with torch.no_grad():
    enc = tok(texts, padding=True, truncation=True, max_length=256, return_tensors="pt")
    out = model(**enc)
print("  hidden:", out.last_hidden_state.shape, " 32 条耗时 %.2fs" % (time.time()-t0))
print("  参数名样例:", [n for n, _ in model.named_parameters()][:2])
PY
