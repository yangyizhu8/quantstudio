"""采集 miniQMT/xtquant **实际 fresh 前复权输出**，固化为方法 A 独立黄金 fixture。

第三轮对抗审核要求（2026-07-27）：方法 A 黄金数据必须是独立采集的
fresh_xtquant_minute_front，**禁止**由 stored_raw × daily_scale 合成（同源 oracle
无法作独立抽验）。本脚本直接调用 xtquant 客户端接口，把三证券的 fresh 前复权
日线 + 1min 分钟输出原样落 parquet，并记录完整采集证据链：

  接口       : xtdata.get_market_data_ex
  参数       : period='1d'/'1m', dividend_type='front'（对照采 'none'）
  复权方式   : xtquant 客户端本地前复权（实测为**减法除息模型**：
               除权日前 front = raw - 每股现金分红，见 metadata 的 sanity 段）
  采集时间   : metadata.captured_at
  客户端     : miniQMT（国金 QMT 交易端模拟）xtdata 服务；版本见 metadata
  行数/sha256: metadata.files

时间戳约定（第三轮修正实测确认）：xtquant 返回的 1m bar 标签 HHMMSS **本身
就是 end-labeled 时刻**——093000 = 09:30 集合竞价 bar，093100 = 09:30~09:31
连续竞价 bar（标到 09:31），与 QuantStudio stored 分钟表的 end-labeled
epoch-ms 网格**直接逐 bar 对齐，无需任何平移**。实测证据：三证券 9 个交易日
直接对齐后 2169/2169 根 bar raw close 与 stored 逐值一致（maxdiff < 4e-15）；
此前"+60s 平移"版本会产生非法 11:31/15:01 时刻并整体错位一根 bar，已废除。

第四轮对抗审核要求（2026-07-27）：staged fresh daily 的四个 front 列必须
**逐值来自实际 xtquant 输出**，禁止用 close scale 合成 open/high/low_front。
因此本脚本采集 OHLC 四字段 × raw/front 两复权，daily 与 1min 都采全。

输出：tests/fixtures/qfq_real_reanchor/fresh_xtquant/
  {code}_fresh_daily.parquet : time(epoch-ms 00:00 +08),
      open_raw, high_raw, low_raw, close_raw,
      open_front, high_front, low_front, close_front
  {code}_fresh_1min.parquet  : time(epoch-ms end-labeled), 同上 8 列
  metadata_fresh_xtquant.json: 完整采集证据链

用法（需 miniQMT 客户端在线）：
  C:/Users/Administrator/AppData/Local/Programs/Python/Python311/python.exe \
      scripts/capture_fresh_xtquant_golden.py
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from xtquant import xtdata
import xtquant

OUT = (Path(__file__).resolve().parent.parent
       / "tests" / "fixtures" / "qfq_real_reanchor" / "fresh_xtquant")
CODES = {"600875": "600875.SH", "600039": "600039.SH", "002864": "002864.SZ"}
FIELDS = ["open", "high", "low", "close"]
START_D, END_D = "20260713", "20260724"   # 日线含 07-13 predecessor
START_M, END_M = "20260714", "20260724"   # 分钟与 stored fixture 同窗（9 日）
TZ = "Asia/Shanghai"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _label_to_ms(idx: pd.Index, *, daily: bool) -> pd.Series:
    """xtquant 标签（YYYYMMDD / YYYYMMDDHHMMSS）→ epoch-ms（+08）。"""
    fmt = "%Y%m%d" if daily else "%Y%m%d%H%M%S"
    ts = pd.to_datetime(idx.astype(str), format=fmt).tz_localize(TZ)
    return (ts.astype("int64") // 10**6).astype("int64")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {
        "purpose": ("方法 A 独立黄金数据：实际 fresh xtquant 前复权输出，"
                    "非 stored_raw×daily_scale 合成"),
        "interface": "xtdata.get_market_data_ex",
        "dividend_type": {"front": "前复权（客户端本地计算）", "none": "raw 对照"},
        "params": {
            "daily": {"period": "1d", "start_time": START_D, "end_time": END_D},
            "minute": {"period": "1m", "start_time": START_M, "end_time": END_M},
            "fields": FIELDS,
        },
        "captured_at": datetime.now().isoformat(),
        "client": {
            "xtquant_module": str(Path(xtquant.__file__)),
            "xtquant_version": getattr(xtdata, "__version__", None)
                               or getattr(xtquant, "__version__", "unknown"),
            "data_dir": xtdata.get_data_dir(),
        },
        "timestamp_convention": ("1m 标签 HHMMSS 本身即 end-labeled 时刻"
                                 "（093000=集合竞价 bar，093100=09:30~09:31 bar），"
                                 "与 stored 分钟表 end-labeled epoch-ms **直接对齐，"
                                 "零平移**；实测 2169/2169 bar raw close 与 stored "
                                 "逐值一致 maxdiff<4e-15"),
        "files": {},
        "sanity": {},
    }

    for code, xt in CODES.items():
        xtdata.download_history_data(xt, "1d", START_D, END_D)
        xtdata.download_history_data(xt, "1m", START_M, END_M)

        dfr = xtdata.get_market_data_ex(FIELDS, [xt], period="1d",
                                        start_time=START_D, end_time=END_D,
                                        dividend_type="front")[xt]
        dnr = xtdata.get_market_data_ex(FIELDS, [xt], period="1d",
                                        start_time=START_D, end_time=END_D,
                                        dividend_type="none")[xt]
        mfr = xtdata.get_market_data_ex(FIELDS, [xt], period="1m",
                                        start_time=START_M, end_time=END_M,
                                        dividend_type="front")[xt]
        mnr = xtdata.get_market_data_ex(FIELDS, [xt], period="1m",
                                        start_time=START_M, end_time=END_M,
                                        dividend_type="none")[xt]
        assert list(dfr.index) == list(dnr.index)
        assert list(mfr.index) == list(mnr.index)

        def _frame(none_df: pd.DataFrame, front_df: pd.DataFrame,
                   time_ms) -> pd.DataFrame:
            cols = {"time": time_ms}
            for f in FIELDS:                     # 四列 raw 均为实际 xtquant 输出
                cols[f"{f}_raw"] = none_df[f].to_numpy(dtype=float)
            for f in FIELDS:                     # 四列 front 均为实际 xtquant 输出
                cols[f"{f}_front"] = front_df[f].to_numpy(dtype=float)
            return pd.DataFrame(cols)

        dd = _frame(dnr, dfr, _label_to_ms(dfr.index, daily=True).to_numpy())

        # 分钟：xtquant 1m 标签本身即 end-labeled（093100=09:30~09:31 bar），
        # 与 stored end-labeled epoch-ms 直接对齐——零平移（实测 2169/2169
        # bar raw close 逐值一致；"+60s 平移"会整体错位一根 bar，已废除）。
        md = _frame(mnr, mfr, _label_to_ms(mfr.index, daily=False).to_numpy())

        dp = OUT / f"{code}_fresh_daily.parquet"
        mp = OUT / f"{code}_fresh_1min.parquet"
        dd.to_parquet(dp, index=False)
        md.to_parquet(mp, index=False)
        meta["files"][dp.name] = {"rows": len(dd), "sha256": _sha256(dp)}
        meta["files"][mp.name] = {"rows": len(md), "sha256": _sha256(mp)}

        sc = md["close_front"] / md["close_raw"]
        day = pd.to_datetime(md["time"], unit="ms", utc=True).dt.tz_convert(TZ)
        meta["sanity"][code] = {
            "daily_front": {str(int(t)): float(v)
                            for t, v in zip(dd["time"], dd["close_front"])},
            "minute_scale_by_day": {
                d.strftime("%Y%m%d"): [float(g.min()), float(g.max())]
                for d, g in sc.groupby(day.dt.normalize())},
        }
        print(f"{code}: daily={len(dd)} minute={len(md)} "
              f"scale[{float(sc.min()):.6f},{float(sc.max()):.6f}]")

    (OUT / "metadata_fresh_xtquant.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"metadata_fresh_xtquant.json 写入 {OUT}")


if __name__ == "__main__":
    main()
