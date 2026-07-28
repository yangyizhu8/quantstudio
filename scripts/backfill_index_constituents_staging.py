#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指数成分历史快照回填（staging 安全范式）。

数据流：
  tushare index_weight（历史成分）
  → 复刻 adapter._fetch_index_constituents 的月末取数逻辑（补全 000688/000852 后缀）
  → 经 DuckDBWriter（复用 writers 契约，按表主键 upsert）写入 staging 库 index_constituents
  → 经 refresh_snapshot_meta（复用 index_constituents_meta 契约，compute_snapshot_status 诚实打点）
    写入 index_constituents_snapshot_meta

四阶段（互斥）：
  --prepare    复制生产库 + config 到 staging（复制=只读，绝不修改生产库）
  --run-task   拉取历史成分写入 staging（复用现有契约，不绕过写入）
  --audit      对 staging 做完整性 + PIT 抽样校验（只读）
  --promote    dry-run 仅打印将 staging 替换生产库的命令，绝不执行

安全铁律：
  * 生产库在 prepare 阶段**仅被复制（读）**；run-task/audit/promote 绝不打开生产库写入。
  * run-task 开头校验 staging marker，防止误对生产库执行写入。
  * promote 永远只打印命令，不可逆替换交由人工执行。
  * 不修改任何框架代码（writers / meta / adapter / provider 原样复用）。
"""
import argparse
import datetime as _dt
import hashlib
import logging
import os
import shutil
from pathlib import Path

import pandas as pd

# ---- 项目内契约（复用，不修改） ----
from quantstudio._paths import DATA_ROOT, db_path as _default_production_db
from quantstudio._secrets import load_secrets_env

# 项目根（scripts/ 的父目录），用于定位 config 目录；与 _paths.DATA_ROOT 解耦。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

STAGING_DB_NAME = "quantstudio.db"
STAGING_MARKER = ".staging_index_backfill_marker"

# 用户指定回填的指数
DEFAULT_INDICES = ["000300", "000905", "000852", "399101", "000016", "000688"]
# 固定成分指数（按 expectations expected_count 校验）
FIXED_INDICES = {"000016", "000300", "000688", "000852", "000905"}
# 可变成分指数（399101 中小板综，n>0 即可）
VARIABLE_INDICES = {"399101"}

# adapter._fetch_index_constituents 的 index_map 缺失 000688/000852，
# 此处补完整后缀映射（不修改框架代码，仅脚本内使用）。
INDEX_SUFFIX_MAP = {
    "000016": "000016.SH", "000300": "000300.SH", "000688": "000688.SH",
    "000905": "000905.SH", "000852": "000852.SH", "399101": "399101.SZ",
    "399001": "399001.SZ", "399006": "399006.SZ",
}

logger = logging.getLogger("backfill_index_constituents")


def _today_str():
    return _dt.date.today().strftime("%Y%m%d")


def default_staging_root():
    return str(DATA_ROOT / f"staging_index_backfill_{_today_str()}")


def staging_db_path(staging_root):
    return Path(staging_root) / STAGING_DB_NAME


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# 复刻 DuckDBDataAccess._end_ms 口径（Asia/Shanghai 当日 23:59:59.999 ms），
# 保证写入的 time 与 query_index_constituents 的 as_of 口径完全一致，PIT 才能命中。
def day_end_ms(date_str: str) -> int:
    ts = pd.Timestamp(str(date_str)[:10]).tz_localize("Asia/Shanghai")
    return int((ts.value // 10**6) + 86_399_999)


def load_token():
    load_secrets_env()
    tok = os.environ.get("TUSHARE_TOKEN")
    if not tok:
        raise RuntimeError("TUSHARE_TOKEN 未从 config/secrets.env 加载，请检查 secrets 配置")
    return tok


def _ensure_disk_space(src: Path, staging_root: Path):
    try:
        free = shutil.disk_usage(staging_root.parent).free
    except Exception:
        return
    need = src.stat().st_size * 2  # 复制 1 份 + 写入余量
    if free < need:
        raise RuntimeError(
            f"磁盘空间不足：剩余 {free // (1024 ** 3)}GB，预计需要 {need // (1024 ** 3)}GB "
            f"（生产库 {src.stat().st_size // (1024 ** 3)}GB × 2）。"
            f"请清理磁盘，或改用 schema-only 复制。")


# --------------------------------------------------------------------------
# prepare：复制生产库 + config 到 staging（生产库仅被读取）
# --------------------------------------------------------------------------
def phase_prepare(args):
    src = Path(args.source_db) if args.source_db else Path(_default_production_db())
    if not src.exists():
        raise RuntimeError(f"生产库不存在: {src}")
    staging_root = Path(args.staging_root)
    staging_db = staging_db_path(staging_root)
    if staging_db.exists():
        raise RuntimeError(f"staging 库已存在: {staging_db}，请先清理再 prepare")
    staging_root.mkdir(parents=True, exist_ok=True)
    _ensure_disk_space(src, staging_root)

    logger.info(f"[prepare] 复制生产库 {src} -> {staging_db}")
    shutil.copy2(src, staging_db)
    logger.info(f"[prepare] staging SHA256: {sha256_of(staging_db)}")

    cfg_src = PROJECT_ROOT / "config"
    cfg_dst = staging_root / "config"
    if cfg_dst.exists():
        shutil.rmtree(cfg_dst)
    shutil.copytree(cfg_src, cfg_dst)

    (staging_root / STAGING_MARKER).write_text(
        f"index_constituents backfill staging\ncreated={_dt.datetime.now().isoformat()}\n")
    logger.info(f"[prepare] staging 就绪: {staging_root}")
    logger.info(f"[prepare] 生产库未被修改（仅被读取复制）。")


def _auto_end_for_index(bare: str, staging_db: Path, default_end: str) -> str:
    """取该指数在 staging 库最早 complete 快照 time 的上一月末作为 end（仅填补空缺，不覆盖现有）；
    若无 complete 快照则回填到 default_end。"""
    import duckdb
    try:
        con = duckdb.connect(str(staging_db), read_only=True)
        r = con.execute(
            "SELECT MIN(time) FROM index_constituents_snapshot_meta "
            "WHERE index_code=? AND status='complete'", [bare]).fetchone()
        con.close()
    except Exception:
        r = (None,)
    t = r[0] if r else None
    if t is None:
        return default_end
    d = pd.Timestamp(t, unit="ms", tz="Asia/Shanghai")
    first_of_month = pd.Timestamp(d.year, d.month, 1, tz="Asia/Shanghai")
    last_prev = first_of_month - pd.Timedelta(days=1)
    return last_prev.strftime("%Y%m%d")


def fetch_index_weight_history(adapter, bare: str, start_yyyymmdd: str, end_yyyymmdd: str) -> pd.DataFrame:
    """复刻 adapter._fetch_index_constituents 的月末取数逻辑，补全 000688/000852 后缀。

    按年分段调用 tushare index_weight（降低单次返回量 + 限频友好），
    按每月最后一个有数据的交易日去重，输出 index_constituents 表格式
    （index_code, code, time, weight, data_source），data_source 标记溯源为
    "tushare"，与 refresh_snapshot_meta 的 data_source 参数一致，保证主表/meta
    溯源一致（表 schema 含 data_source 列，新数据不再为 NULL）。
    """
    ts_code = INDEX_SUFFIX_MAP.get(bare, f"{bare}.SZ")
    sy, ey = int(start_yyyymmdd[:4]), int(end_yyyymmdd[:4])
    chunks = []
    for y in range(sy, ey + 1):
        y0 = f"{y}0101"
        y1 = f"{y}1231"
        if y1 > end_yyyymmdd:
            y1 = end_yyyymmdd
        if y0 < start_yyyymmdd:
            y0 = start_yyyymmdd
        try:
            adapter.rate_limiter.acquire()
            df = adapter._client.index_weight(index_code=ts_code, start_date=y0, end_date=y1)
        except Exception as e:
            logger.warning(f"[fetch] {bare} {y} 年 index_weight 失败: {e}")
            continue
        if df is None or len(df) == 0:
            continue
        df = df.copy()
        df["trade_date"] = df["trade_date"].astype(str)
        df["ym"] = df["trade_date"].str[:6]
        last_td = set(df.groupby("ym")["trade_date"].max())
        df = df[df["trade_date"].isin(last_td)]
        chunks.append(df)
    if not chunks:
        return pd.DataFrame(columns=["index_code", "code", "time", "weight", "data_source"])
    full = pd.concat(chunks, ignore_index=True)
    full = full.drop_duplicates(subset=["con_code", "trade_date"], keep="last")
    rows = []
    for _, r in full.iterrows():
        td = str(r["trade_date"])
        ds = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
        w = r.get("weight")
        rows.append({
            "index_code": bare,
            "code": str(r["con_code"]).split(".")[0],
            "time": day_end_ms(ds),
            "weight": (float(w) if pd.notna(w) else None),
            "data_source": "tushare",
        })
    return pd.DataFrame(rows, columns=["index_code", "code", "time", "weight", "data_source"])


# --------------------------------------------------------------------------
# run-task：拉取历史成分写入 staging（复用 writers + meta 契约）
# --------------------------------------------------------------------------
def phase_run_task(args):
    staging_root = Path(args.staging_root)
    if not (staging_root / STAGING_MARKER).exists():
        raise RuntimeError("staging marker 不存在，请先 --prepare（防止误写生产库）")
    staging_db = staging_db_path(staging_root)
    if not staging_db.exists():
        raise RuntimeError(f"staging 库不存在: {staging_db}")

    indices = args.indices or DEFAULT_INDICES
    start = args.start.replace("-", "")
    default_end = args.end or _dt.date.today().strftime("%Y%m%d")

    from quantstudio.pipeline.writers import DuckDBWriter
    from quantstudio.pipeline.index_constituents_meta import refresh_snapshot_meta
    from quantstudio.pipeline.sources.tushare_adapter import TushareAdapter

    # 仅指向 staging 库，绝不开生产库写
    writer = DuckDBWriter({"type": "duckdb", "path": str(staging_db)})
    token = load_token()
    adapter = TushareAdapter({"name": "tushare", "token": token,
                              "rate_limit": {"calls_per_min": 200, "wait_on_429": True}})

    for bare in indices:
        end = args.end or _auto_end_for_index(bare, staging_db, default_end)
        logger.info(f"[run-task] {bare}: {start} ~ {end}")
        df = fetch_index_weight_history(adapter, bare, start, end)
        logger.info(f"[run-task] {bare}: 解析 {len(df)} 行成分")
        if len(df) == 0:
            continue
        writer.write(df, "index_constituents", batch_id=f"idx_backfill_{bare}")

    # 诚实打点：复用 refresh_snapshot_meta（内部 compute_snapshot_status）
    import duckdb
    con = duckdb.connect(str(staging_db))
    try:
        refresh_snapshot_meta(con, index_codes=indices, data_source="tushare")
    finally:
        con.close()
    logger.info("[run-task] 写入 + 打点完成（仅写入 staging，生产库未动）")


# --------------------------------------------------------------------------
# audit：对 staging 做完整性 + PIT 抽样校验（只读）
# --------------------------------------------------------------------------
def phase_audit(args):
    staging_db = staging_db_path(Path(args.staging_root))
    if not staging_db.exists():
        raise RuntimeError(f"staging 库不存在: {staging_db}")
    import duckdb
    con = duckdb.connect(str(staging_db), read_only=True)
    indices = args.indices or DEFAULT_INDICES

    print("=== 各指数快照 status 分布 ===")
    for bare in indices:
        rows = con.execute(
            "SELECT status, COUNT(*) FROM index_constituents_snapshot_meta "
            "WHERE index_code=? GROUP BY status ORDER BY status", [bare]).fetchall()
        print(f"  {bare}: {dict(rows)}")

    print("\n=== 完整性校验 ===")
    ok = True
    for bare in indices:
        snaps = con.execute(
            "SELECT time, n_constituents, expected_count, status FROM "
            "index_constituents_snapshot_meta WHERE index_code=? AND status='complete' "
            "ORDER BY time", [bare]).fetchall()
        if not snaps:
            print(f"  {bare}: 无 complete 快照 ❌")
            ok = False
            continue
        if bare in FIXED_INDICES:
            bad = [s for s in snaps if (s[1] or 0) < (s[2] or 0)]
            if bad:
                print(f"  {bare}: {len(bad)} 个 complete 快照 n<expected ❌")
                ok = False
            else:
                print(f"  {bare}: 全部 {len(snaps)} 个 complete 快照 n>=expected ✅")
        else:  # variable
            bad = [s for s in snaps if (s[1] or 0) <= 0]
            if bad:
                print(f"  {bare}: {len(bad)} 个 complete 快照 n<=0 ❌")
                ok = False
            else:
                print(f"  {bare}: 全部 {len(snaps)} 个 complete 快照 n>0 ✅")

    inv = con.execute(
        "SELECT index_code, time FROM index_constituents_snapshot_meta "
        "WHERE status='invalid'").fetchall()
    if inv:
        print(f"\n  ⚠️ invalid 快照: {inv}")
        ok = False
    else:
        print("\n  无 invalid 快照 ✅")

    print("\n=== PIT 抽样（复刻 query_index_constituents 语义）===")
    samples = [("000300", "2021-06-30"), ("399101", "2022-06-30")]
    for bare, ds in samples:
        as_of = day_end_ms(ds)
        snap_time = con.execute(
            "SELECT MAX(time) FROM index_constituents WHERE index_code=? AND time<=?",
            [bare, as_of]).fetchone()[0]
        if snap_time is None:
            print(f"  {bare}@{ds}: 无快照 ❌")
            ok = False
            continue
        n = con.execute(
            "SELECT COUNT(DISTINCT code) FROM index_constituents "
            "WHERE index_code=? AND time=?", [bare, snap_time]).fetchone()[0]
        print(f"  {bare}@{ds}: 最新快照time={snap_time} 成分数={n} {'✅' if n > 0 else '❌'}")
        if n <= 0:
            ok = False

    print("\n=== data_source 溯源抽查（验收点④）===")
    # 抽新回填快照（取早于原生产库覆盖起点的日期，确保是本次新写入），确认 data_source='tushare'
    ds_checks = [("000300", "2019-06-30"), ("000852", "2019-06-30"), ("399101", "2020-06-30")]
    for bare, ds in ds_checks:
        as_of = day_end_ms(ds)
        snap_time = con.execute(
            "SELECT MAX(time) FROM index_constituents "
            "WHERE index_code=? AND time<=? AND data_source='tushare'",
            [bare, as_of]).fetchone()[0]
        if snap_time is None:
            sample = con.execute(
                "SELECT DISTINCT data_source FROM index_constituents "
                "WHERE index_code=? AND time<=? LIMIT 1", [bare, as_of]).fetchone()
            print(f"  {bare}@{ds}: 无 data_source='tushare' 快照 ❌ (sample={sample})")
            ok = False
            continue
        vals = con.execute(
            "SELECT DISTINCT data_source FROM index_constituents "
            "WHERE index_code=? AND time=?", [bare, snap_time]).fetchall()
        flat = sorted({v[0] for v in vals})
        print(f"  {bare}@{ds}: 快照time={snap_time} data_source={flat} "
              f"{'✅' if flat == ['tushare'] else '❌'}")
        if flat != ['tushare']:
            ok = False
    con.close()
    print("\n审计结论:", "全部通过 ✅" if ok else "存在问题 ❌")
    return ok


# --------------------------------------------------------------------------
# promote：dry-run 仅打印替换命令（绝不执行）
# --------------------------------------------------------------------------
def phase_promote(args):
    prod = Path(args.source_db) if args.source_db else Path(_default_production_db())
    staging_db = staging_db_path(Path(args.staging_root))
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("【promote 仅 dry-run，不执行任何写入/替换】")
    print("将 staging 库替换生产库（不可逆），人工执行步骤：")
    print(f"  1) 备份生产库:  cp \"{prod}\" \"{prod}.bak.{ts}\"")
    print(f"  2) 替换生产库:  cp \"{staging_db}\" \"{prod}\"")
    print(f"      （或更稳妥： mv \"{prod}\" \"{prod}.bak.{ts}\" && cp \"{staging_db}\" \"{prod}\"）")
    print(f"  3) 复核:  python scripts/backfill_index_constituents_staging.py "
          f"--audit --staging-root \"{args.staging_root}\"")
    print(f"  4) 清理:  rm -rf \"{args.staging_root}\"")


def main():
    p = argparse.ArgumentParser(description="指数成分历史快照回填（staging 安全范式）")
    p.add_argument("--prepare", action="store_true", help="复制生产库+config 到 staging")
    p.add_argument("--run-task", action="store_true", help="拉取历史成分写入 staging")
    p.add_argument("--audit", action="store_true", help="对 staging 做完整性+PIT 校验")
    p.add_argument("--promote", action="store_true", help="dry-run 打印替换命令")
    p.add_argument("--source-db", default=None, help="生产库路径（默认 quantstudio._paths.db_path）")
    p.add_argument("--staging-root", default=default_staging_root(), help="staging 根目录")
    p.add_argument("--indices", nargs="*", default=None, help="要回填的指数裸码列表")
    p.add_argument("--start", default="2017-01-01", help="回填起始日 YYYY-MM-DD")
    p.add_argument("--end", default=None, help="回填截止日 YYYYMMDD（默认按各指数最早 complete 前）")
    args = p.parse_args()

    chosen = [x for x in ("prepare", "run_task", "audit", "promote") if getattr(args, x)]
    if len(chosen) != 1:
        p.error("必须且只能选择一个阶段: --prepare / --run-task / --audit / --promote")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if chosen[0] == "prepare":
        phase_prepare(args)
    elif chosen[0] == "run_task":
        phase_run_task(args)
    elif chosen[0] == "audit":
        phase_audit(args)
    elif chosen[0] == "promote":
        phase_promote(args)


if __name__ == "__main__":
    main()
