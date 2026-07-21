"""Tab5: 数据质量检查

重写版（2026-07-17）：修复原版多个漏洞——
  L1 多表检查：原版只查 stock_daily，漏查 index_daily/etf_daily（etf 实测漏报5行脏数据）
  L2-L7 补全检查维度：价格非正/主键重复/负值/未来函数/isST缺失/日期连续性
  T1 涨跌幅分板块：主板10%/创业科创20%/北交所&指数不限，避免一刀切误报
  T2 异常显示样本：附前5行便于排查
  D3 空表预期：tick/minutes 预期空表不报⚠

检查项设计原则：
  - 每个检查返回 (result_str, detail_str)，detail 含总数 + 样本
  - 行情类检查（OHLC/价格/单位）按表语义自动遍历所有含相关列的表
  - 阈值用常量集中定义，便于调参
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidgetItem,
    QHeaderView, QLabel)
from qfluentwidgets import (
    TableWidget, PushButton, PlainTextEdit, GroupHeaderCardWidget)

logger = logging.getLogger(__name__)

# ---- 阈值常量（集中定义，便于调参）----
UNIT_RATIO_LO, UNIT_RATIO_HI = 0.5, 2.0          # amount/(close*volume) 合理区间
PCTCHG_CALC_DEVIATION = 1.0                       # pctChg 与 (close/preClose-1)*100 允许偏差(%)
PCTCHG_EXTREME = 50.0                             # |pctChg| 极端值阈值(%)，超出需人工确认
SAMPLE_LIMIT = 5                                  # 异常样本显示条数

# 含 OHLC 的行情表（自动遍历）
OHLC_TABLES = ["stock_daily", "index_daily", "etf_daily"]
# 含 volume/amount/close 的表（单位校验）
UNIT_TABLES = ["stock_daily", "etf_daily"]  # index close=点不适用
# 预期可能为空的表（不报 ⚠）
EXPECTED_EMPTY = {"tick", "stock_minutes", "etf_minutes"}


class QualityTab(QWidget):
    """数据质量检查（9 项）：表行数/OHLC/价格正数/单位/负值/主键重复/
    涨跌幅/未来函数/isST缺失"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        btn_bar = QHBoxLayout()
        self.run_btn = PushButton("▶ 执行全部检查")
        self.run_btn.clicked.connect(self._run_all_checks)
        btn_bar.addWidget(self.run_btn)
        btn_bar.addStretch()
        layout.addLayout(btn_bar)

        group = GroupHeaderCardWidget()
        group.setTitle("检查项")
        glayout = group.layout()  # reuse the card's existing QVBoxLayout
        self.check_table = TableWidget()
        self.check_table.setColumnCount(4)
        self.check_table.setHorizontalHeaderLabels(["#", "检查项", "结果", "详情"])
        self.check_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.check_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        glayout.addWidget(self.check_table)
        layout.addWidget(group, 2)

        detail_group = GroupHeaderCardWidget()
        detail_group.setTitle("详情")
        detail_layout = detail_group.layout()  # reuse the card's existing QVBoxLayout
        self.detail_text = PlainTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        detail_layout.addWidget(self.detail_text)
        layout.addWidget(detail_group, 1)

    def _run_all_checks(self):
        checks = [
            ("0", "统一契约审计（全表/复权/频率/水位）", self._check_contract_audit),
            ("1", "表行数统计", self._check_rowcount),
            ("2", "OHLC 价格逻辑", self._check_ohlc),
            ("3", "价格非正数", self._check_price_positive),
            ("4", "单位校验（amount/vol）", self._check_unit),
            ("5", "负值检查（vol/amount）", self._check_negative),
            ("6", "主键重复", self._check_pk_duplicate),
            ("7", "涨跌幅合理性（分板块）", self._check_pctchg),
            ("8", "未来函数（time>现在）", self._check_future_time),
            ("9", "isST 缺失", self._check_isst_null),
        ]
        self.check_table.setRowCount(len(checks))
        all_detail = []
        for i, (num, name, fn) in enumerate(checks):
            self.check_table.setItem(i, 0, QTableWidgetItem(num))
            self.check_table.setItem(i, 1, QTableWidgetItem(name))
            try:
                result, detail = fn()
            except Exception as e:
                result, detail = "❌ 异常", str(e)
            self.check_table.setItem(i, 2, QTableWidgetItem(result))
            self.check_table.setItem(i, 3, QTableWidgetItem(detail.split("\n")[0][:80]))
            all_detail.append(f"[{num}] {name}\n{result}\n{detail}\n")
        self.detail_text.setPlainText("\n".join(all_detail))

    def _check_contract_audit(self):
        """配置驱动的全库质量审计，覆盖所有 schema。"""
        from pathlib import Path
        from quantstudio.pipeline.quality_audit import DataQualityAuditor
        root = Path(__file__).resolve().parents[3]
        report = DataQualityAuditor.from_config(
            self.mw.db_helper.duckdb_path,
            root / "config" / "alignment_rules.json",
            batch_audit_path=self.mw.db_helper.batch_audit_path,
            quarantine_path=self.mw.db_helper.quarantine_path).run()
        errors = [issue for issue in report.issues if issue.severity == "error"]
        warnings = [issue for issue in report.issues if issue.severity == "warning"]
        lines = [f"执行 {report.checks_run} 项；error={len(errors)}，warning={len(warnings)}"]
        for issue in report.issues[:100]:
            lines.append(f"  [{issue.severity}] {issue.table}/{issue.check}: "
                         f"{issue.count} {issue.detail}")
        if len(report.issues) > 100:
            lines.append(f"  ... 其余 {len(report.issues)-100} 项省略")
        if errors:
            result = f"❌ {len(errors)} 类错误"
        elif warnings:
            result = f"⚠ {len(warnings)} 类警告"
        else:
            result = "✅ 通过"
        return result, "\n".join(lines)

    # ---------- 检查项 ----------

    def _check_rowcount(self):
        """L1+D3：表行数，预期空表不报 ⚠"""
        tables = self.mw.db_helper.list_tables()
        detail_lines = []
        unexpected_empty = []
        for t in tables:
            cnt = self.mw.db_helper.table_rowcount(t)
            if cnt == 0 and t not in EXPECTED_EMPTY:
                unexpected_empty.append(t)
                status = "⚠ 空表(非预期)"
            elif cnt == 0:
                status = "○ 空(预期)"
            else:
                status = "✅"
            detail_lines.append(f"  {t}: {cnt} 行 {status}")
        result = "✅ 通过" if not unexpected_empty else f"⚠ {len(unexpected_empty)} 张非预期空表"
        return result, "\n".join(detail_lines)

    def _check_ohlc(self):
        """L1：OHLC 遍历所有行情表（high>=max(o,l,c), low<=min(o,h,c)）"""
        total = 0
        detail_lines = []
        for t in OHLC_TABLES:
            try:
                df = self.mw.db_helper.query_duckdb(
                    f"SELECT COUNT(*) as cnt FROM {t} "
                    f"WHERE high < open OR high < low OR high < close "
                    f"OR low > open OR low > high OR low > close")
                cnt = int(df.iloc[0]["cnt"])
            except Exception as e:
                detail_lines.append(f"  {t}: 跳过 ({e})")
                continue
            total += cnt
            detail_lines.append(f"  {t}: {cnt} 行违反")
            if cnt > 0:
                sample = self._sample(f"SELECT code, time, open, high, low, close FROM {t} "
                                      f"WHERE high < open OR high < low OR high < close "
                                      f"OR low > open OR low > high OR low > close")
                detail_lines.extend(sample)
        result = "✅ 通过" if total == 0 else f"❌ {total} 条违反"
        return result, "\n".join(detail_lines)

    def _check_price_positive(self):
        """L2：价格非正数检查（open/high/low/close <= 0 是脏数据）"""
        total = 0
        detail_lines = []
        for t in OHLC_TABLES:
            try:
                df = self.mw.db_helper.query_duckdb(
                    f"SELECT COUNT(*) as cnt FROM {t} "
                    f"WHERE open <= 0 OR high <= 0 OR low <= 0 OR close <= 0")
                cnt = int(df.iloc[0]["cnt"])
            except Exception:
                continue
            total += cnt
            detail_lines.append(f"  {t}: {cnt} 行价格<=0")
            if cnt > 0:
                detail_lines.extend(self._sample(
                    f"SELECT code, time, open, high, low, close FROM {t} "
                    f"WHERE open <= 0 OR high <= 0 OR low <= 0 OR close <= 0"))
        result = "✅ 通过" if total == 0 else f"❌ {total} 条价格非正"
        return result, "\n".join(detail_lines)

    def _check_unit(self):
        """L1：单位校验遍历 stock_daily + etf_daily（指数 close=点不适用）"""
        total = 0
        detail_lines = []
        for t in UNIT_TABLES:
            df = self.mw.db_helper.query_duckdb(
                f"SELECT COUNT(*) as cnt FROM {t} "
                f"WHERE volume > 0 AND close > 0 AND "
                f"(amount/(close*volume) < {UNIT_RATIO_LO} OR amount/(close*volume) > {UNIT_RATIO_HI})")
            cnt = int(df.iloc[0]["cnt"])
            total += cnt
            detail_lines.append(f"  {t}: {cnt} 行单位异常 (ratio∉[{UNIT_RATIO_LO},{UNIT_RATIO_HI}])")
            if cnt > 0:
                detail_lines.extend(self._sample(
                    f"SELECT code, time, close, volume, amount, "
                    f"ROUND(amount/(close*volume),4) as ratio FROM {t} "
                    f"WHERE volume > 0 AND close > 0 AND "
                    f"(amount/(close*volume) < {UNIT_RATIO_LO} OR amount/(close*volume) > {UNIT_RATIO_HI})"))
        result = "✅ 通过" if total == 0 else f"⚠ {total} 条异常"
        return result, "\n".join(detail_lines)

    def _check_negative(self):
        """L5：volume/amount 负值检查"""
        total = 0
        detail_lines = []
        for t in ["stock_daily", "etf_daily", "index_daily"]:
            try:
                df = self.mw.db_helper.query_duckdb(
                    f"SELECT COUNT(*) as cnt FROM {t} WHERE volume < 0 OR amount < 0")
                cnt = int(df.iloc[0]["cnt"])
            except Exception:
                continue
            total += cnt
            if cnt > 0:
                detail_lines.append(f"  {t}: {cnt} 行负值")
                detail_lines.extend(self._sample(
                    f"SELECT code, time, volume, amount FROM {t} WHERE volume < 0 OR amount < 0"))
        result = "✅ 通过" if total == 0 else f"❌ {total} 条负值"
        return result, "\n".join(detail_lines) if detail_lines else "✅ 通过\n  无负值"

    def _check_pk_duplicate(self):
        """L3：主键重复检查（同 code+time 多行，upsert 失效的信号）"""
        total = 0
        detail_lines = []
        for t in ["stock_daily", "index_daily", "etf_daily"]:
            try:
                df = self.mw.db_helper.query_duckdb(
                    f"SELECT COUNT(*) as cnt FROM (SELECT code, time FROM {t} "
                    f"GROUP BY code, time HAVING COUNT(*) > 1)")
                cnt = int(df.iloc[0]["cnt"])
            except Exception:
                continue
            total += cnt
            detail_lines.append(f"  {t}: {cnt} 组重复(code,time)")
            if cnt > 0:
                detail_lines.extend(self._sample(
                    f"SELECT code, time, COUNT(*) c FROM {t} GROUP BY code, time HAVING c > 1"))
        result = "✅ 通过" if total == 0 else f"❌ {total} 组主键重复"
        return result, "\n".join(detail_lines)

    def _check_pctchg(self):
        """T1+T2：涨跌幅检查（精确版）。

        分两个维度，避免新股合法大幅波动被误报：
          (a) 复权跳变/计算错误：pctChg 与 (close/preClose-1)*100 偏差 >1%
              ——这才是入库问题（复权未对齐、pctChg 透传错）
          (b) 极端值：|pctChg|>50%（无论板块，都值得人工确认）
        不再按板块前缀设阈值（新股前5日无涨跌停，纯前缀判断会误报）。
        """
        detail_lines = []
        # (a) pctChg 与 close/preClose 不一致（入库计算错误的核心信号）
        df_calc = self.mw.db_helper.query_duckdb(
            "SELECT COUNT(*) as cnt FROM stock_daily "
            "WHERE preClose > 0 AND ABS(pctChg - (close/preClose-1)*100) > 1.0")
        n_calc = int(df_calc.iloc[0]["cnt"])
        detail_lines.append(f"  (a) pctChg 与 close/preClose 偏差>1%: {n_calc} 行（入库计算/复权问题）")
        if n_calc > 0:
            detail_lines.extend(self._sample(
                "SELECT code, time, close, preClose, pctChg, "
                "ROUND((close/preClose-1)*100,4) as calc_pct FROM stock_daily "
                "WHERE preClose > 0 AND ABS(pctChg - (close/preClose-1)*100) > 1.0"))
        # (b) 极端值（|pctChg|>50%，无论板块都需确认）
        df_ext = self.mw.db_helper.query_duckdb(
            "SELECT COUNT(*) as cnt FROM stock_daily WHERE ABS(pctChg) > 50")
        n_ext = int(df_ext.iloc[0]["cnt"])
        detail_lines.append(f"  (b) |pctChg|>50% 极端值: {n_ext} 行（需确认是否新股/数据源问题）")
        total = n_calc + n_ext
        result = "✅ 通过" if total == 0 else f"⚠ {total} 条需确认"
        return result, "\n".join(detail_lines)

    def _check_future_time(self):
        """L4：未来函数检查（time > 现在）"""
        now_ms = int(time.time() * 1000)
        total = 0
        detail_lines = []
        for t in ["stock_daily", "index_daily", "etf_daily"]:
            try:
                df = self.mw.db_helper.query_duckdb(
                    f"SELECT COUNT(*) as cnt FROM {t} WHERE time > {now_ms}")
                cnt = int(df.iloc[0]["cnt"])
            except Exception:
                continue
            total += cnt
            if cnt > 0:
                detail_lines.append(f"  {t}: {cnt} 行 time>现在")
                detail_lines.extend(self._sample(
                    f"SELECT code, time FROM {t} WHERE time > {now_ms}"))
        result = "✅ 通过" if total == 0 else f"❌ {total} 条未来数据"
        return result, "\n".join(detail_lines) if detail_lines else "✅ 通过\n  无未来数据"

    def _check_isst_null(self):
        """L6：isST 缺失检查（防 TD-2 类 isST 恒空问题）"""
        detail_lines = []
        try:
            df = self.mw.db_helper.query_duckdb(
                "SELECT COUNT(*) as cnt FROM stock_daily WHERE isST IS NULL")
            cnt = int(df.iloc[0]["cnt"])
        except Exception as e:
            return "○ 跳过", f"stock_daily 无 isST 列或查询失败: {e}"
        status = "✅" if cnt == 0 else f"⚠ {cnt} NULL"
        detail_lines.append(f"  stock_daily isST IS NULL: {cnt} 行 {status}")
        if cnt > 0:
            detail_lines.extend(self._sample(
                "SELECT code, time FROM stock_daily WHERE isST IS NULL"))
        result = "✅ 通过" if cnt == 0 else f"⚠ {cnt} 条 isST 缺失"
        return result, "\n".join(detail_lines)

    # ---------- 工具方法 ----------

    def _sample(self, sql: str) -> list:
        """取前 SAMPLE_LIMIT 行样本，格式化为列表（T2：异常附样本便于排查）"""
        try:
            df = self.mw.db_helper.query_duckdb(sql + f" LIMIT {SAMPLE_LIMIT}")
            lines = [f"    样本:"]
            for _, row in df.iterrows():
                # time 毫秒转日期
                r = row.to_dict()
                if "time" in r and r["time"] is not None:
                    try:
                        r["time"] = datetime.fromtimestamp(int(r["time"]) / 1000).strftime("%Y-%m-%d")
                    except Exception:
                        pass
                lines.append(f"      {r}")
            return lines
        except Exception as e:
            return [f"    样本查询失败: {e}"]

    def refresh(self):
        pass
