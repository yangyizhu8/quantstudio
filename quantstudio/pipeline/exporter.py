"""
KLineExporter — 统一原生分库导出器

职责：从单库 quantstudio.db（pipeline 内部格式，含 code 列、主键 code+time）
读取某股票数据，按 统一原生格式导出为按股票分库的 .db 文件：
    - 无 code 列（分库设计，每库单股票）
    - time 作单主键
    - 表名 kline_1d / kline_1m / kline_5m / tick（统一原生命名）
    - 目录结构：SH/600000.db、SZ/000001.db、BJ/830001.db

对接 统一引擎时调用此导出器生成引擎能读的库。

使用：
    exp = KLineExporter("data/quantstudio.db", out_dir="data/kline_db")
    exp.export(code="600000", freqs=["daily", "1min"])
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import pandas as pd

from .aligner import market_of_code
from quantstudio._paths import db_path, DATA_ROOT

logger = logging.getLogger(__name__)


# pipeline 表名 → 统一原生表名 + freq 映射
FREQ_TO_KLINE = {
    "daily": ("stock_daily", "kline_1d"),
    "1min": ("stock_minutes", "kline_1m"),
    "5min": ("stock_minutes", "kline_5m"),
    "15min": ("stock_minutes", "kline_15m"),
    "30min": ("stock_minutes", "kline_30m"),
    "60min": ("stock_minutes", "kline_60m"),
    "tick": ("tick", "tick"),
}


class KLineExporter:
    """统一原生分库导出器

    config 示例：
        exp = KLineExporter("data/quantstudio.db", "data/kline_db")
    """

    def __init__(self, src_db: str | Path, out_dir: str | Path):
        self.src_db = Path(src_db)
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def export(self, code: str, freqs: Optional[List[str]] = None,
               start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> Path:
        """导出单股票的 统一原生 .db 文件。

        Args:
            code: 6 位裸码（如 600000）
            freqs: 要导出的频率列表（默认 ["daily"]）
            start_ms/end_ms: 毫秒时间戳范围（可选）

        Returns:
            导出的 .db 文件路径
        """
        freqs = freqs or ["daily"]
        market = market_of_code(code)  # SH / SZ / BJ
        # 统一目录结构：SH/600000.db
        market_dir = self.out_dir / market
        market_dir.mkdir(parents=True, exist_ok=True)
        out_path = market_dir / f"{code}.db"

        try:
            import duckdb
        except ImportError as e:
            raise ImportError("需安装 duckdb") from e

        # 源库读取
        with duckdb.connect(str(self.src_db), read_only=True) as src:
            with duckdb.connect(str(out_path)) as dst:
                for freq in freqs:
                    if freq not in FREQ_TO_KLINE:
                        logger.warning(f"[Exporter] 未知频率 {freq}，跳过")
                        continue
                    src_table, kh_table = FREQ_TO_KLINE[freq]
                    df = self._read_source(src, src_table, code, freq, start_ms, end_ms)
                    if len(df) == 0:
                        logger.info(f"[Exporter] {code}/{freq}: 0 行，跳过 {kh_table}")
                        continue
                    # 去掉 code 列（统一分库设计无 code 列）
                    if "code" in df.columns:
                        df = df.drop(columns=["code"])
                    # 写入 统一原生表（time 单主键）
                    dst.register("_tmp_export", df)
                    dst.execute(f"CREATE TABLE IF NOT EXISTS {kh_table} AS SELECT * FROM _tmp_export WHERE 1=0")
                    dst.execute(f"INSERT INTO {kh_table} SELECT * FROM _tmp_export")
                    dst.unregister("_tmp_export")
                    logger.info(f"[Exporter] {code}/{freq} → {kh_table}: {len(df)} 行")

        logger.info(f"[Exporter] 导出完成: {out_path}")
        return out_path

    def export_batch(self, codes: List[str], freqs: Optional[List[str]] = None) -> List[Path]:
        """批量导出多只股票"""
        paths = []
        for code in codes:
            try:
                p = self.export(code, freqs)
                paths.append(p)
            except Exception as e:
                logger.error(f"[Exporter] {code} 导出失败: {e}")
        return paths

    def _read_source(self, conn, src_table: str, code: str, freq: str,
                     start_ms: Optional[int], end_ms: Optional[int]) -> pd.DataFrame:
        """从源库读取单股票数据"""
        conditions = [f"code = '{code}'"]
        if freq != "daily" and src_table == "stock_minutes":
            conditions.append(f"freq = '{freq}'")
        if start_ms is not None:
            conditions.append(f"time >= {start_ms}")
        if end_ms is not None:
            conditions.append(f"time <= {end_ms}")
        where = " AND ".join(conditions)
        try:
            df = conn.execute(
                f"SELECT * FROM {src_table} WHERE {where} ORDER BY time").fetchdf()
        except Exception as e:
            logger.warning(f"[Exporter] 读 {src_table} 失败: {e}")
            return pd.DataFrame()
        return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ROOT = Path(__file__).resolve().parent.parent.parent
    exp = KLineExporter(db_path(), DATA_ROOT / "kline_db")
    # 导出 600000 日线
    try:
        p = exp.export("600000", freqs=["daily"])
        print(f"导出: {p}")
        # 验证导出库结构
        import duckdb
        with duckdb.connect(str(p)) as conn:
            tables = conn.execute("SHOW TABLES").fetchall()
            print(f"表: {tables}")
            if tables:
                tname = tables[0][0]
                cnt = conn.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()[0]
                cols = conn.execute(f"DESCRIBE {tname}").fetchall()
                print(f"{tname}: {cnt} 行, 列={[c[0] for c in cols]}")
                print(f"  含 code 列? {'code' in [c[0] for c in cols]}（应为 False）")
                sample = conn.execute(f"SELECT time, open, close FROM {tname} LIMIT 3").fetchall()
                print(f"  样本: {sample}")
    except Exception as e:
        print(f"导出失败（可能源库为空）: {e}")
