"""验收③：隔离区防护能力复验（用户 2026-09-23 追加验收项）。

目标：证明「异常样本写入路径」可达，且 max_rows 顶格机制与 archive_expired
      腾退机制按设计工作（不破坏、不丢数据）。
全程使用临时隔离库，不触碰生产 data/quarantine.db。
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
from quantstudio.pipeline.quarantine import Quarantine  # noqa: E402

tmpdir = Path(tempfile.mkdtemp(prefix="qs_qacc_"))
db = tmpdir / "q.db"

# 1) 顶格机制：max_rows=100，写 3 批共 150 行
q = Quarantine(db, max_rows=100, retention_days=7)
rows = [{"code": "600000", "value": i} for i in range(50)]
w1 = q.write("b1", "stock_minutes", "mcp", rows, ["UnitCheck"])
w2 = q.write("b2", "stock_minutes", "mcp", rows, ["UnitCheck"])
w3 = q.write("b3", "stock_minutes", "mcp", rows, ["UnitCheck"])
print(f"[顶格] 写入序列 = {w1}, {w2}, {w3}（期望 50, 50, 0 —— 第三批因顶格被拒）")
assert (w1, w2, w3) == (50, 50, 0), (w1, w2, w3)
print("  OK 第三批被拒 50 行（模拟客户 500,809 / 500K 顶格现象）")

# 2) 归档腾退：retention_days=0 → 全部 pending 视为超期 → 归档
n_arch = q.archive_expired(retention_days=0)
print(f"\n[腾退] archive_expired(0) 归档 {n_arch} 行（期望 100）")
assert n_arch == 100, n_arch
stats = q.stats()
print(f"  归档后状态分布 = {stats}（期望 pending_repair=0, archived=100）")
assert stats.get("pending_repair", 0) == 0 and stats.get("archived", 0) == 100, stats
print("  OK 归档不删除（archived 保留行数与 payload），仅释放 pending 配额")

# 3) 防护能力恢复：腾退后必须能再次写入
w4 = q.write("b4", "stock_minutes", "mcp", rows[:30], ["UnitCheck"])
print(f"\n[恢复] 腾退后再次写入 = {w4}（期望 30）")
assert w4 == 30, w4
print("  OK 防护能力恢复：真实异常样本写入路径可达（用户追加验收项达成）")

# 4) 归档行仍可查（不丢数据）
con = sqlite3.connect(db)
cnt = con.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
sample = con.execute(
    "SELECT batch_id, table_name, failed_rules, status FROM quarantine LIMIT 1").fetchone()
payload_len = con.execute(
    "SELECT LENGTH(original_payload) FROM quarantine LIMIT 1").fetchone()[0]
con.close()
print(f"\n[不丢数据] 全表行数 = {cnt}（期望 130 = 100 archived + 30 pending）")
print(f"  样例 = {sample}；payload 长度 = {payload_len}")
assert cnt == 130, cnt
assert payload_len > 0
print("\nPASS：隔离区 顶格 -> 归档腾退 -> 写入恢复 全链路按设计工作，且归档不丢数据")