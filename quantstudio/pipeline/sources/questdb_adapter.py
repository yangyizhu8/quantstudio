# -*- coding: utf-8 -*-
"""QuestDB 合规源适配器（本地 PG wire，64 表回填 · 方案 v1.3 §2.1）

定位：把本地 QuestDB（127.0.0.1:8812）作为**合规数据源**接入既有采集管线，
与 mcp_adapter 同构输出（batch 流 + metadata），使回填表与云端拉取表在
入库/水位/质量门/巡检体系中**不可区分**（方案 §一-4、验收 V3）。

设计要点
  1. 全部 64 表均为 passthrough：列名/类型原样搬运（duckdb 目标表名 = 源表名），
     不做 column_map / normalize / aligner / QFQ——与 mcp_adapter._fetch_passthrough 同语义。
  2. 双字段时间语义（总调度 2026-09-12 采纳）：
       date_basis      业务事件时间（语义键，如 trade_date / publish_time）
       watermark_basis 水位推进基准（对齐云端更新语义；晚到/重刷行按 ingest/fetch 追踪）
     窗口过滤一律用 watermark_basis（增量正确性），date_basis 供下游语义使用。
  3. 分页：watermark_basis 日切片 + 批内 keyset 翻页；fetch_table_streaming 逐片 yield。
     **禁止静默截断**：翻页直至取尽（末片不足 batch 即结束）。
  4. 单位声明（B1-4 纪律）：metadata["units"] 逐列声明，来自 questdb_table_map.json。
  5. 锁探测/避让/DEDUP 由回填编排器负责（见 docs/questdb-backfill-guard-spec.md）。

依赖：psycopg2（QuestDB PG wire 兼容子集）；行值比较不可用，改用词法展开。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import pandas as pd

from .base import BaseSourceAdapter

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[3]                      # quantstudio/pipeline/sources -> repo root
_MAP_PATH = _ROOT / "config" / "profiles" / "mcp_only" / "questdb_table_map.json"

DEFAULT_PG = dict(host="127.0.0.1", port=8812, user="admin", password="quest", dbname="qdb")


def _load_table_map(path: Optional[Path] = None) -> Dict:
    p = path or _MAP_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _quote(name: str) -> str:
    return '"' + name + '"'


def _keyset_predicate(order_keys: List[str], last_key: List) -> Tuple[str, List]:
    """(a,b,...) > (x,y,...) 的词法展开（QuestDB PG wire 不支持行值比较）。

    (a,b) > (x,y)  等价于  a > x OR (a = x AND b > y)
    """
    terms, params = [], []
    for i, k in enumerate(order_keys):
        eqs = []
        for j in range(i):
            eqs.append(_quote(order_keys[j]) + " = %s")
            params.append(last_key[j])
        gt = _quote(k) + " > %s"
        term = (" AND ".join(eqs + [gt])) if eqs else gt
        terms.append("(" + term + ")")
        params.append(last_key[i])
    return "(" + " OR ".join(terms) + ")", params


class QuestDBAdapter(BaseSourceAdapter):
    """QuestDB 源适配器（passthrough 语义）。

    用法（编排器）：
        adapter = QuestDBAdapter(dict(name="questdb"))
        meta, shards = adapter.fetch_table_streaming("daily_info", "2024-01-01", "2026-09-12")
        for df in shards:      # 逐片入库（writers.py），内存受控
            ...
    """

    SOURCE_NAME = "questdb"

    def __init__(self, config: Optional[Dict] = None, table_map_path: Optional[Path] = None):
        super().__init__(config or dict(name=self.SOURCE_NAME))
        self._map_doc = _load_table_map(table_map_path)
        self._tables: Dict[str, Dict] = self._map_doc.get("tables") or {}
        api = (config or {}).get("api") or {}
        self.pg = {**DEFAULT_PG, **{k: v for k, v in api.items() if k in DEFAULT_PG}}
        self._batch_size = int((config or {}).get("batch_size", 200000))
        self._days_per_slice = int((config or {}).get("days_per_slice", 1))

    # ── 元信息 ────────────────────────────────────────────────
    def known_tables(self) -> List[str]:
        return sorted(self._tables)

    def spec(self, table: str) -> Dict:
        spec = self._tables.get(table)
        if not spec:
            raise ValueError("[QuestDBAdapter] 未在 questdb_table_map.json 登记的表: " + str(table))
        return spec

    def supports_task(self, table: str, freq: str = "daily") -> Tuple[bool, str]:
        if table not in self._tables:
            return False, table + " 不在 QuestDB 映射表（64 表）内"
        want = self._tables[table].get("freq", "daily")
        if freq != want:
            return False, f"{table} 仅支持 freq={want}，请求 {freq}"
        return True, ""

    def supports_freq(self, freq: str) -> bool:
        return freq == "daily"

    def is_passthrough(self, table: str) -> bool:
        return True   # 64 表全为 passthrough（方案 §2.3）

    # ── 连接 ──────────────────────────────────────────────────
    def _connect(self):
        import psycopg2
        return psycopg2.connect(host=self.pg["host"], port=self.pg["port"],
                                user=self.pg["user"], password=self.pg["password"],
                                dbname=self.pg["dbname"])

    @staticmethod
    def _norm_day(v) -> str:
        s = str(v).strip()
        if len(s) >= 10 and s[4] == "-":
            return s[:10]
        if len(s) == 8 and s.isdigit():
            return s[:4] + "-" + s[4:6] + "-" + s[6:]
        return s

    # ── 主入口：全量（小表）────────────────────────────────────
    def fetch_table(self, table: str, start: str, end: str,
                    freq: str = "daily",
                    codes: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict]:
        """拉取整窗（内存全驻留）——大表请用 fetch_table_streaming。"""
        meta, shards = self.fetch_table_streaming(table, start, end, freq=freq, codes=codes)
        frames = list(shards)
        df = (pd.concat(frames, ignore_index=True) if frames
              else pd.DataFrame(columns=[c["target"] for c in meta["columns"]]))
        meta["rows"] = len(df)
        logger.info("[QuestDBAdapter] %s fetch_table done rows=%d window=%s~%s shards=%d",
                    table, len(df), start, end, len(frames))
        return df, meta

    # ── 主入口：流式（大表；逐片 yield）─────────────────────────
    def fetch_table_streaming(self, table: str, start: str, end: str,
                              freq: str = "daily",
                              codes: Optional[List[str]] = None
                              ) -> Tuple[Dict, Iterator[pd.DataFrame]]:
        """流式拉取：返回 (metadata, shard_iter)。逐片 yield（内存峰值=单片）。"""
        spec = self.spec(table)
        meta = self._build_meta(spec, table, freq, start, end, codes)
        return meta, self._iter_shards(spec, start, end, codes)

    def fetch_table_chunked(self, table: str, start: str, end: str,
                            freq: str = "daily",
                            codes: Optional[List[str]] = None
                            ) -> Tuple[Dict, Iterator[Tuple[str, pd.DataFrame]]]:
        """分片拉取（带确定性片键）：返回 (metadata, iter[(chunk_key, DataFrame)])。

        chunk_key = 该片的 watermark_basis 日切片起点（如 '2026-09-11 00:00:00'），
        确定性且可重放 —— 供 writers.write_passthrough_chunked 的分片 ledger 续写
        （B+ 增补：崩溃/避让暂停后按片续插，不整表重跑）。
        """
        spec = self.spec(table)
        meta = self._build_meta(spec, table, freq, start, end, codes)
        return meta, self._iter_shards(spec, start, end, codes, with_key=True)

    # ── metadata（与 mcp_adapter 同构 + 双字段扩展）─────────────
    def _build_meta(self, spec: Dict, table: str, freq: str,
                    start: str, end: str, codes) -> Dict:
        units = {c["target"]: c.get("unit", "source_native") for c in spec["columns"]}
        return dict(
            source=self.SOURCE_NAME, table=table, freq=freq,
            start=self._norm_day(start), end=self._norm_day(end),
            columns_origin=[c["source"] for c in spec["columns"]],
            columns=[dict(source=c["source"], target=c["target"], type=c["type"])
                     for c in spec["columns"]],
            code_format="bare6",
            date_format="questdb_timestamp",
            units=units,
            unit_basis=spec.get("units_basis"),
            passthrough=True,
            pipeline="questdb",
            date_basis=spec.get("date_basis"),
            watermark_basis=spec.get("watermark_basis"),
            watermark_confidence=spec.get("watermark_confidence"),
            dedup_keys=spec.get("dedup_keys"),
            target_table=spec.get("target_table", table),
            qdb_rows=spec.get("qdb_rows"),
            codes_filter=None if not codes or "ALL" in codes else list(codes),
        )

    # ── 分页：watermark_basis 日切片 + 批内 keyset 翻页 ─────────
    def _iter_shards(self, spec: Dict, start: str, end: str,
                     codes, with_key: bool = False):
        table = spec["source_table"]
        wm = spec["watermark_basis"]
        order_keys = [wm] + [k for k in (spec.get("dedup_keys") or []) if k != wm]
        sel_cols = [c["source"] for c in spec["columns"]]
        ren = {c["source"]: c["target"] for c in spec["columns"]}
        sel_sql = ", ".join(_quote(c) for c in sel_cols)
        order_sql = ", ".join(_quote(k) for k in order_keys)

        conn = self._connect()
        cur = None
        try:
            cur = conn.cursor()
            # 窗口钳制：先取源表 watermark_basis 实际 min/max，避免稀疏表空日轮询
            # （64 表横跨多年，逐日空探会把标定吞吐稀释成往返开销）
            lo, hi = self._norm_day(start), self._norm_day(end)
            try:
                cur.execute("SELECT min(" + _quote(wm) + "), max(" + _quote(wm) + ") FROM "
                            + _quote(table))
                r = cur.fetchone()
                if r and r[0] is not None:
                    t_min, t_max = str(r[0])[:10], str(r[1])[:10]
                    lo = max(lo, t_min)
                    hi = min(hi, t_max)
                    if lo > hi:
                        logger.warning("[QuestDBAdapter] %s 请求窗 %s~%s 与源数据窗 %s~%s 无交集",
                                       table, start, end, t_min, t_max)
                        return
                    logger.info("[QuestDBAdapter] %s 窗口钳制 -> %s~%s (请求 %s~%s, 源 %s~%s)",
                                table, lo, hi, start, end, t_min, t_max)
            except Exception as e:
                logger.warning("[QuestDBAdapter] %s min/max 钳制失败，按请求窗执行: %s", table, e)
            total = 0
            for (d0, d1) in self._day_ranges(lo, hi):
                last_key = None
                while True:
                    where = _quote(wm) + " >= %s AND " + _quote(wm) + " < %s"
                    params: List = [d0, d1]
                    if codes and "ALL" not in codes:
                        where += " AND " + _quote("ts_code") + " = ANY(%s)"
                        params.append(list(codes))
                    if last_key is not None:
                        pred, pp = _keyset_predicate(order_keys, last_key)
                        where += " AND " + pred
                        params.extend(pp)
                    sql = ("SELECT " + sel_sql + " FROM " + _quote(table) +
                           " WHERE " + where + " ORDER BY " + order_sql +
                           " LIMIT " + str(self._batch_size))
                    # 连接自愈（2026-09-12 实测缺陷修复）：S5 避让暂停可达数十分钟，
                    # 期间 PG 连接被服务端/中间层闲置断开 → 恢复后首查报
                    # "could not receive data from server ... connection abort (10053)"。
                    # 片键确定 ⇒ 重连重试同一片是幂等的（不会重复计入 staging）。
                    rows, attempt = None, 0
                    while rows is None:
                        try:
                            cur.execute(sql, params)
                            rows = cur.fetchall()
                        except Exception as e:  # noqa: BLE001
                            attempt += 1
                            if attempt > 3:
                                raise
                            logger.warning("[QuestDBAdapter] %s 查询失败（第 %d 次），重连重试 "
                                           "slice=%s offset_key=%s: %s",
                                           table, attempt, d0[:10], last_key, str(e)[:110])
                            try:
                                cur.close()
                            except Exception:
                                pass
                            try:
                                conn.close()
                            except Exception:
                                pass
                            time.sleep(2 * attempt)
                            conn = self._connect()
                            cur = conn.cursor()
                    if not rows:
                        break
                    df = pd.DataFrame(rows, columns=sel_cols).rename(columns=ren)
                    total += len(df)
                    logger.info("[QuestDBAdapter] %s shard rows=%d slice=%s cum=%d",
                                table, len(df), d0[:10], total)
                    yield (d0, df) if with_key else df
                    if len(rows) < self._batch_size:
                        break
                    last_key = [rows[-1][sel_cols.index(k)] for k in order_keys]
            if total == 0:
                logger.warning("[QuestDBAdapter] %s 窗口内无数据 (%s~%s, watermark_basis=%s)",
                               table, start, end, wm)
        finally:
            try:
                if cur is not None:
                    cur.close()
                conn.close()
            except Exception:
                pass

    def _day_ranges(self, start: str, end: str) -> List[Tuple[str, str]]:
        """按日（或 N 日）切窗；闭区间语义 [start, end]。"""
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(end, "%Y-%m-%d")
        step = max(1, self._days_per_slice)
        out, cur = [], s
        while cur <= e:
            nxt = min(cur + timedelta(days=step), e + timedelta(days=1))
            out.append((cur.strftime("%Y-%m-%d 00:00:00"), nxt.strftime("%Y-%m-%d 00:00:00")))
            cur = nxt
        return out

    # ── 水位 / 对账 ───────────────────────────────────────────
    def get_last_date(self, table: str, freq: str = "daily") -> Optional[str]:
        """源表 watermark_basis 最大值（YYYY-MM-DD）；无数据返回 None。"""
        spec = self.spec(table)
        wm = spec["watermark_basis"]
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT max(" + _quote(wm) + ") FROM " + _quote(spec["source_table"]))
            r = cur.fetchone()
            cur.close()
            return None if (not r or r[0] is None) else str(r[0])[:10]
        finally:
            conn.close()

    def count_window(self, table: str, start: str, end: str) -> int:
        """窗口行数（V4 对账用）。"""
        spec = self.spec(table)
        wm = spec["watermark_basis"]
        e1 = (datetime.strptime(self._norm_day(end), "%Y-%m-%d") + timedelta(days=1))
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT count() FROM " + _quote(spec["source_table"]) +
                        " WHERE " + _quote(wm) + " >= %s AND " + _quote(wm) + " < %s",
                        [self._norm_day(start) + " 00:00:00", e1.strftime("%Y-%m-%d 00:00:00")])
            n = int(cur.fetchone()[0])
            cur.close()
            return n
        finally:
            conn.close()


def load_table_map() -> Dict:
    """便捷入口：读取 64 表映射表（供编排器/巡检复用）。"""
    return _load_table_map()
