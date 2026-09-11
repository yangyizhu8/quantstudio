# -*- coding: utf-8 -*-
"""数据包 Part 1：主库 -> 包文件 拷贝段（package-first 架构，2026-09-12 裁定）

语义：
  · 只读 ATTACH 主库（READ_ONLY，与在跑的读者共存，不占写锁）
  · CTAS 逐表拷贝主库全部表（实测 51 张 = 24 映射表 + 27 附属表，其中 qfq_* 14 张）
  · 逐表行数核验 + manifest 落盘（逐表覆盖声明）
  · 包文件是新建独立 duckdb；主库零写入

用法：
  python scripts/questdb_package_part1.py                 # 创建/续拷包文件
  python scripts/questdb_package_part1.py --verify-only   # 只核验
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MAIN_DB = ROOT / "data" / "quantstudio.db"
LOG = logging.getLogger("package_part1")


def default_package() -> Path:
    return ROOT / f"quantstudio_data_package_{datetime.now().strftime('%Y%m%d')}.db"


def main():
    ap = argparse.ArgumentParser(description="数据包 Part 1：主库拷贝段")
    ap.add_argument("--package", type=str, default="")
    ap.add_argument("--mode", choices=["file-copy", "ctas"], default="file-copy",
                    help="file-copy（默认，保留完整 schema/约束，推荐）| ctas（丢约束，"
                         "会导致 writer schema 闸判 partial_or_mixed，仅作历史路径）")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--skip-backups", action="store_true",
                    help="跳过 *_backup_* 备份表（减小包体）")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    import duckdb

    pkg = Path(args.package) if args.package else default_package()
    manifest_path = pkg.with_suffix(".manifest.json")

    # ── file-copy 模式（默认）：整库文件复制 ─────────────────────────
    # 背景（2026-09-12 实测）：CTAS 只复列与数据，**不复制 PK/NOT NULL/DEFAULT/
    # CHECK/UNIQUE 约束**，而 DuckDBWriter 的 QFQ schema 安全闸以
    # verify_fingerprint(TARGET_MAIN_DB_2_1_FINGERPRINT) 校验这些约束 ——
    # CTAS 产出的包被判 partial_or_mixed，writer init 直接拒绝。
    # 故默认改为**文件级复制**：字节精确、约束完整、且远快于 EXPORT/IMPORT 往返。
    # 前置：主库无读写成开持有者（RW-open 失败且持有者为只读进程）。
    if args.mode == "file-copy" and not args.verify_only:
        import shutil
        import subprocess as _sp
        if pkg.exists():
            LOG.info("包文件已存在，跳过复制: %s", pkg)
        else:
            rw_ok = True
            try:
                c = duckdb.connect(str(MAIN_DB))
                c.execute("SELECT 1").fetchone()
                c.close()
            except Exception as e:
                rw_ok = False
                LOG.info("主库 RW-open 失败（存在持有者）——按只读持有者处理，文件视为稳定")
                LOG.info("  持有者信息: %s", str(e)[:200].replace("\n", " "))
            LOG.info("file-copy: %s -> %s", MAIN_DB.name, pkg.name)
            t0 = __import__("time").time()
            shutil.copy2(MAIN_DB, pkg)
            wal = Path(str(MAIN_DB) + ".wal")
            if wal.exists() and wal.stat().st_size > 0:
                shutil.copy2(wal, Path(str(pkg) + ".wal"))
                LOG.warning("同步复制主库 .wal（%.2f MB）", wal.stat().st_size / 1024 ** 2)
            LOG.info("file-copy 完成 %.1fs, %.2f GB",
                     __import__("time").time() - t0, pkg.stat().st_size / 1024 ** 3)
            manifest_path.write_text(json.dumps(dict(
                schema="data_package_v1", part1_mode="file-copy",
                created_at=datetime.now().isoformat(timespec="seconds"),
                package=str(pkg), source_db=str(MAIN_DB),
                source_size_gb=round(MAIN_DB.stat().st_size / 1024 ** 3, 2),
                package_size_gb=round(pkg.stat().st_size / 1024 ** 3, 2),
            ), ensure_ascii=False, indent=1), encoding="utf-8")
        con0 = duckdb.connect(str(pkg), read_only=True)
        try:
            from quantstudio.pipeline.qfq_schema_status import detect_schema_status
            st = detect_schema_status(con0)
            LOG.info("包文件 schema 状态: %s", st.value)
        except Exception as e:
            LOG.warning("schema 状态检测失败: %s", str(e)[:120])
        n_tabs = con0.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_catalog = current_catalog() AND table_schema='main'").fetchone()[0]
        LOG.info("包文件表数: %d", n_tabs)
        con0.close()
        return 0

    # 单连接双 ATTACH：包文件为默认库，主库以 READ_ONLY 挂为 m 目录。
    # （duckdb 的 ATTACH 目录仅在**同一连接**内可见——跨连接引用会报 schema "m" does not exist）
    pkg.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(pkg))
    con.execute(f"ATTACH IF NOT EXISTS '{MAIN_DB}' AS m (READ_ONLY)")
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_catalog='m' AND table_schema='main' ORDER BY table_name").fetchall()]
    if args.skip_backups:
        tables = [t for t in tables if "_backup_" not in t]
    LOG.info("主库只读 ATTACH OK，待拷贝 %d 表 -> %s", len(tables), pkg.name)

    if args.verify_only:
        if not pkg.exists():
            LOG.error("包文件不存在: %s", pkg)
            return 2
        bad = []
        for t in tables:
            a = con.execute(f'SELECT count(*) FROM m."{t}"').fetchone()[0]
            try:
                b = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            except Exception as e:
                bad.append((t, a, f"缺失: {str(e)[:60]}"))
                continue
            if a != b:
                bad.append((t, a, b))
        con.close()
        for t, a, b in bad:
            LOG.error("  核验差异 %s: 主库=%s 包=%s", t, a, b)
        LOG.info("=== Part 1 核验: %d/%d 表一致%s ===", len(tables) - len(bad), len(tables),
                 "" if not bad else f", {len(bad)} 表差异")
        return 0 if not bad else 1

    copied, skipped, failed = [], [], []
    for i, t in enumerate(tables, 1):
        try:
            # 必须限定当前（包）目录——否则 ATTACH 的主库表会被误判为已存在
            exists = con.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_catalog = current_catalog() "
                f"AND table_schema = 'main' AND table_name = '{t}'").fetchone()[0]
            if exists:
                skipped.append(t)
                continue
            con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM m."{t}"')
            n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            n_src = con.execute(f'SELECT count(*) FROM m."{t}"').fetchone()[0]
            if n != n_src:
                raise RuntimeError(f"行数不一致 src={n_src} pkg={n}")
            copied.append(dict(table=t, rows=n))
            LOG.info("[%d/%d] %-34s rows=%s", i, len(tables), t, f"{n:,}")
        except Exception as e:
            failed.append(dict(table=t, error=str(e)[:200]))
            LOG.error("[%d/%d] %-34s 失败: %s", i, len(tables), t, str(e)[:160])

    total = sum(c["rows"] for c in copied)
    manifest = dict(
        schema="data_package_v1",
        created_at=datetime.now().isoformat(timespec="seconds"),
        package=str(pkg),
        source_db=str(MAIN_DB),
        part1=dict(tables_total=len(tables), copied=len(copied),
                   skipped_existing=len(skipped), failed=len(failed),
                   rows=total, details=copied, failures=failed,
                   skipped=skipped),
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    con.close()
    LOG.info("=== Part 1 完成: 拷贝 %d 表 / 跳过 %d / 失败 %d | rows=%s ===",
             len(copied), len(skipped), len(failed), f"{total:,}")
    LOG.info("包文件: %s (%.2f GB)", pkg, pkg.stat().st_size / 1024 ** 3)
    LOG.info("manifest: %s", manifest_path)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
