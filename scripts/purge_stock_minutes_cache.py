# -*- coding: utf-8 -*-
"""清空 stock_minutes 的 export 缓存（manifest 条目 + shard 目录），
让下次 fetch 重新 export 最新云端数据。（TD-QFQ-FRESH-CACHE 处置）
"""
import json
import shutil
from pathlib import Path

root = Path(r"D:\miniQMT策略实盘\QuantStudio\data\mcp_landing")
mp = root / "_export_cache_manifest.json"
m = json.loads(mp.read_text(encoding="utf-8"))

table = "stock_minutes"
entries = m.pop(table, {})
print(f"移除 manifest 网格: {len(entries)}")
# 删除这些网格引用的 shard 目录（job_id 目录下的分片）
dirs = set()
for ck, e in entries.items():
    jid = e.get("job_id")
    if jid:
        dirs.add(jid)
removed_files = 0
for jid in dirs:
    d = root / jid
    if d.exists():
        for f in d.iterdir():
            try:
                f.unlink(); removed_files += 1
            except OSError:
                pass
        try:
            d.rmdir()
        except OSError:
            pass
print(f"删除 shard 文件: {removed_files}, 目录: {len(dirs)}")
json.dump(m, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("manifest 已更新")
# 确认
m2 = json.loads(mp.read_text(encoding="utf-8"))
print("stock_minutes 剩余网格:", len(m2.get("stock_minutes", {})))
print("其他表网格:", {k: len(v) for k, v in m2.items() if k != "stock_minutes"})