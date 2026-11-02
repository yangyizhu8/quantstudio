# -*- coding: utf-8 -*-
"""崩溃注入 runner（SC-014）：真子进程 + env 双开关 ⇒ os._exit 复现"锁残留 + journal 半写"。

用法（由测试调用，不手工执行）：
    python tests/fixtures/delivery/crash_runner.py <data_root> <config_dir> <import_dir>

环境变量：
    QUANTSTUDIO_DATA_ROOT          数据根（与 data_root 参数一致，供 snapshot_lock 定位）
    QS_DELIVERY_TEST_HOOKS=1       启用注入
    QS_DELIVERY_TEST_KILL_AFTER_REPLACES=N  替换 N 项后 os._exit(9)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def main() -> int:
    data_root, config_dir, import_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    os.environ.setdefault("QUANTSTUDIO_DATA_ROOT", data_root)
    # snapshot_lock 刻意不跟随 QUANTSTUDIO_DATA_ROOT（只认本变量）⇒ 崩溃注入必须把锁目录
    # 重定向到 fixture，否则 os._exit 会把陈锁写进**真实** data/snapshots（W3 首跑已实证污染）
    lock_dir = Path(data_root) / "_lock_dir"
    lock_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QS_WRITE_LOCK_DIR", str(lock_dir))
    from quantstudio.delivery import import_kernel as I

    result = I.import_bundle(
        import_dir=Path(import_dir),
        config_dir=Path(config_dir),
        data_root=Path(data_root),
    )
    print(f"OK={result.ok} exit={result.exit_code} mode={result.mode} reasons={result.reasons}")
    return 0 if result.ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
