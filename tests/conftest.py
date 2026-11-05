"""测试全局配置：把演示库**复制到临时文件**再用。

为什么：很多用例会写库（问答缓存 kv、反馈表、扩充日志），如果直接指向
`data/snapshot/demo.db`，每跑一次测试 `git status` 就会多出一个修改——
学习/提交时很烦，也容易把测试数据误提交。

优先级：
1. 用户/CI 显式设了 ST_DB → 尊重（CI 就是传 demo.db，那里只读不提交）；
2. 否则自动复制到系统临时目录并在本进程内指向它。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "snapshot" / "demo.db"


def _prepare_db() -> None:
    if os.environ.get("ST_DB"):
        return                      # 调用方自己指定了，不覆盖
    if not DEMO.exists():
        return
    tmp = Path(tempfile.gettempdir()) / "rs_test_demo.db"
    try:
        shutil.copyfile(DEMO, tmp)
        # WAL 残留可能干扰，先清掉同名的 -wal/-shm
        for suffix in ("-wal", "-shm"):
            p = Path(str(tmp) + suffix)
            if p.exists():
                p.unlink()
        os.environ["ST_DB"] = str(tmp)
    except Exception:
        os.environ["ST_DB"] = str(DEMO)


_prepare_db()
