# -*- coding: utf-8 -*-
"""PTrade 保真模式配置（P-A0/P-A1/P-A2，2026-08-24）。

定位（P-D9 裁定）：保真模式是**模拟平台行为、验证转换产物**的可选对齐开关，
不是让本地回测"更正确"。默认全部关闭时，本地保持正确语义锚（本地是权威）；
仅当用户为"转换产物对账验证"显式开启时，本地才迁就平台口径向平台收敛。

本模块承载：
  - :class:`PTradeFidelityConfig`：三个对齐开关的 frozen dataclass；
  - eps basis 双端（本地列 × 平台列）存在性校验（审计 v2 收尾项：任一端缺失
    → 显性报错或降级 passthrough + WARNING，禁止静默单端 fallback）；
  - 平台探针实证常量（探针甲/乙 2026-08-24 回贴）：
      * 平台 `get_Ashares` 快照：snapshot_date=2026-07-01, total=5205,
        sha256=ce35485af7bba04fca23b98a96d295eafaf44c292074122b683c01ca32fd628d；
        （快照 parquet 由 scripts/probe_platform_ashares.py 构建，本模块只消费）
      * 平台 eps 表监听（探针乙）：eps/basic_eps/diluted_eps/eps_ttm 存在；
        bps/deducted_eps/operating_eps/total_asset_share 缺失（KeyError → (0,0) 空帧）；
      * 本地 fin_indicator 实际列（DESCRIBE 实证 15 列，2026-08-24）：
        code/ann_date/end_date/eps/diluted_eps/bps/roe/pe_ttm/pb/ps_ttm/np_yoy/
        or_yoy/tr_yoy/update_flag/data_source（**无** basic_eps/eps_ttm/deducted_eps/
        operating_eps/total_asset_share）；
      * 本地 income_statement 实际列：…/basic_eps/diluted_eps（**有** basic_eps）。

数值对照实证（PIT @2026-06-30，报告期 2026-03-31）：
  code    本地eps  平台basic_eps  平台eps  平台diluted_eps  本地diluted_eps
  000001  0.67     0.67          0.75     0.67             0.67
  600000  0.52     0.52          0.54     0.52             0.52
  → 本地 eps == 平台 basic_eps/diluted_eps；平台 eps 是加权口径（+12%/+3.8% 偏差源）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 平台探针实证常量（2026-08-24 回贴固化；快照 sha 由探针甲 SUMMARY 行给出）
# ---------------------------------------------------------------------------

#: 平台 A 股池快照探针（探针甲）回贴结论 —— snapshot_date / total / content sha256
PLATFORM_ASHARE_SNAPSHOT = {
    "snapshot_date": "2026-07-01",
    "total": 5205,
    "sha256": "ce35485af7bba04fca23b98a96d295eafaf44c292074122b683c01ca32fd628d",
}


def latest_snapshot_meta(snapshot_dir: Optional[str] = None) -> Dict[str, object]:
    """扫描快照目录，取 snapshot_date 最新的 meta.json（快照刷新后自动跟随）。

    - 快照刷新纪律：新快照以 ashares_<新日期>.parquet + 同名 .meta.json 追加落盘，
      旧档保留不覆盖（审计链可追溯）——本函数取最新一份供 PIT 门禁/parquet 路径消费；
    - 目录不存在 / 无合法 meta / 字段缺失 → 回退硬编码常量 PLATFORM_ASHARE_SNAPSHOT
      （与 2026-08-24 首份快照逐字段一致，保证回退路径行为不变）。
    """
    base = Path(snapshot_dir) if snapshot_dir else Path("data/ptrade_fidelity")
    best_date = None
    best_meta = None
    try:
        for meta_path in sorted(base.glob("ashares_*.parquet.meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                d = str(meta.get("snapshot_date", ""))
                if len(d) == 10 and d[4] == "-" and d[7] == "-":
                    int(d[:4] + d[5:7] + d[8:10])  # 数字校验
                else:
                    continue
                if best_date is None or d > best_date:
                    best_date, best_meta = d, meta
            except Exception:
                continue  # 单个坏 meta 不拖垮整体，跳过
    except Exception:
        pass  # 目录不可读 → 回退常量
    if best_meta is None:
        return dict(PLATFORM_ASHARE_SNAPSHOT)
    merged: Dict[str, object] = dict(PLATFORM_ASHARE_SNAPSHOT)
    for k in ("snapshot_date", "total", "sha256"):
        if k in best_meta:
            merged[k] = best_meta[k]
    return merged

#: eps basis → (本地列规范, 本地来源表, 平台列)（双端候选；实际生效仍需存在性校验）
#: 本地"来源表"用于校验双端存在：eps/diluted_eps/bps 属 fin_indicator；
#: basic_eps 属 income_statement（fin_indicator 无此列，探针乙 DESCRIBE 实证）。
_EPS_BASIS_CANDIDATES: Dict[str, Tuple[str, str, str]] = {
    "passthrough": ("eps", "fin_indicator", "eps"),
    "basic": ("basic_eps", "income_statement", "basic_eps"),
    "diluted": ("diluted_eps", "fin_indicator", "diluted_eps"),
    "ttm": (None, None, "eps_ttm"),           # 平台有、本地无 → 单端缺失候选
    "weighted": (None, None, None),            # 平台无此列名（平台 eps 即加权）→ 非法
}

#: 本地 fin_indicator 实际列（2026-08-24 DESCRIBE 实证 15 列）
_LOCAL_FIN_INDICATOR_COLS: List[str] = [
    "code", "ann_date", "end_date", "eps", "diluted_eps", "bps", "roe",
    "pe_ttm", "pb", "ps_ttm", "np_yoy", "or_yoy", "tr_yoy", "update_flag",
    "data_source",
]

#: 本地 income_statement 实际列（2026-08-24 实证，含 basic_eps/diluted_eps）
_LOCAL_INCOME_STATEMENT_COLS: List[str] = [
    "code", "end_date", "ann_date", "operating_revenue", "operating_cost",
    "operating_profit", "total_profit", "net_profit", "np_parent_company_owners",
    "sale_expense", "manage_expense", "finance_expense", "rd_expense",
    "income_tax", "basic_eps", "update_time", "data_source",
]

#: 平台 eps 表监听（探针乙 2026-08-24：eps/basic_eps/diluted_eps/eps_ttm 存在）
_PLATFORM_EPS_COLS_EXIST: List[str] = ["eps", "basic_eps", "diluted_eps", "eps_ttm"]
#: 平台 eps 表缺失（探针乙 KeyError → (0,0) 空帧实证）
_PLATFORM_EPS_COLS_MISSING: List[str] = [
    "bps", "deducted_eps", "operating_eps", "total_asset_share",
]

#: 每端缺失时的处置模式：'error'（fail-closed 显性报错，默认）| 'degrade'（降级 passthrough + WARNING）
#: 审计 v2 收尾项允许两者之一；默认 fail-closed 最安全（保真模式是显式 opt-in）。
_DEFAULT_MISSING_MODE: str = "error"


@dataclass(frozen=True)
class PTradeFidelityConfig:
    """PTrade 保真模式配置（三开关全默认关闭 = 本地正确语义锚不变）。

    Attributes:
        fidelity_ashares_snapshot: P-A0 —— 本地 get_Ashares 用平台快照（2026-07-01）
            替代本地全市场股池。仅当回测起点 >= snapshot_date 时生效（PIT 门禁），
            否则 ValueError fail-closed；快照超龄 >30 天 WARNING（不阻断）。
        fidelity_st_filter: P-A1 —— 本地 'ST' 过滤对齐平台（退市风险兜底仅 price 分支，
            source in ('price','both') 才视为有效），消除本地 circ_mv<5亿 扩展。
        fidelity_eps_basis: P-A2 —— eps 口径双端映射：passthrough(默认, 本地eps↔平台eps)/
            basic(本地income_statement.basic_eps↔平台basic_eps)/
            diluted(本地fin_indicator.diluted_eps↔平台diluted_eps)。
            单端缺失（ttm/bps/weighted）→ 显性报错或降级 passthrough+WARNING，禁止静默 fallback。
    """

    fidelity_ashares_snapshot: bool = False
    fidelity_st_filter: bool = False
    # P-D13 D2（2026-08-27 审计通过）：eps 口径对齐默认——'basic'（探针三实证
    # 平台 basic_eps == 本地 eps Δ=0.0000；passthrough 容忍加权口径差异 = L6 分叉
    # 的口径分量持续存在）。'passthrough' 仍可显式指定（向后兼容）。
    # D8：默认变更行为影响纳入合并基线重验（P-A3+B2+D2+D3 统一）。
    fidelity_eps_basis: Literal["passthrough", "basic", "diluted", "ttm", "weighted"] = "basic"
    #: 单端缺失处置：'error'（默认 fail-closed）| 'degrade'（降级 passthrough + WARNING）
    eps_missing_mode: Literal["error", "degrade"] = _DEFAULT_MISSING_MODE
    #: 快照 parquet 目录（缺省 data/ptrade_fidelity，构建器 probe_platform_ashares.py 同款）
    snapshot_dir: Optional[str] = None
    #: 快照超龄阈值（天）
    snapshot_max_age_days: int = 30

    # -- 运行时物化状态（非 frozen 字段，由 validate() 填充） --------------------
    _resolved_eps_basis: str = field(default="passthrough", init=False, repr=False, compare=False)
    _dual_end_ok: bool = field(default=False, init=False, repr=False, compare=False)

    # ------------------------------------------------------------------
    # 双端校验（审计 v2 收尾项核心）
    # ------------------------------------------------------------------
    def resolve_eps_basis(
        self,
        local_cols_override: Optional[Dict[str, List[str]]] = None,
        platform_cols_override: Optional[Dict[str, List[str]]] = None,
    ) -> Tuple[str, bool]:
        """校验 fidelity_eps_basis 双端存在性，返回 (实际生效 basis, 是否双端可用)。

        规则（探针实证 + 审计 v2 收尾项）：
          - 双端列都存在（本地列 ∈ 本地表实际列 ∧ 平台列 ∈ 平台 eps 表存在列）→ 生效；
          - 任一端缺失 → 按 eps_missing_mode：
              * 'error'   → raise ValueError（fail-closed，显性报错，默认）；
              * 'degrade' → 返回 ('passthrough', False) + WARNING 日志；
          - 禁止静默单端 fallback（绝不静默改用另一列）。
        """
        basis = self.fidelity_eps_basis
        cand = _EPS_BASIS_CANDIDATES.get(basis)
        if cand is None:
            raise ValueError(
                f"fidelity_eps_basis={basis!r} 非法（允许: passthrough/basic/diluted/ttm/weighted）"
            )
        local_col, local_table, platform_col = cand

        # 平台侧：探针实证列集（可被测试 override 模拟"平台缺列"场景）
        platform_exist = set(
            platform_cols_override.get("eps", _PLATFORM_EPS_COLS_EXIST)
            if platform_cols_override else _PLATFORM_EPS_COLS_EXIST
        )

        # 本地侧：按来源表取实际列（可被测试 override 模拟"本地缺列"场景）
        local_cols = local_cols_override or {
            "fin_indicator": _LOCAL_FIN_INDICATOR_COLS,
            "income_statement": _LOCAL_INCOME_STATEMENT_COLS,
        }
        if local_table is None:
            local_present = False
        else:
            local_present = local_col in set(local_cols.get(local_table, []))

        platform_present = platform_col is not None and platform_col in platform_exist

        if local_present and platform_present:
            return basis, True

        # ---- 单端缺失：显性报错或降级 passthrough + WARNING ----
        missing_sides = []
        if not local_present:
            missing_sides.append(
                f"本地 {local_table or '无表'}.{local_col or '<无列>'}"
                if local_table else "本地（无对应列）"
            )
        if not platform_present:
            missing_sides.append(f"平台 eps.{platform_col or '<无此列名>'}")
        detail = "; ".join(missing_sides) if missing_sides else "未知缺失"

        if self.eps_missing_mode == "degrade":
            logger.warning(
                "PTradeFidelityConfig.fidelity_eps_basis=%s 单端缺失（%s）"
                "→ 降级 passthrough（保真 eps 对齐不生效，本地保持正确语义锚）",
                basis, detail,
            )
            return "passthrough", False

        raise ValueError(
            f"fidelity_eps_basis={basis!r} 双端校验失败：{detail}。"
            f"保真模式禁止静默单端 fallback；请改用 passthrough/basic/diluted"
            f"（或显式 eps_missing_mode='degrade' 降级）。\n"
            f"平台 eps 表存在列: {sorted(platform_exist)}；"
            f"本地 {local_table or 'eps'} 表实际列: "
            f"{sorted(local_cols.get(local_table, [])) if local_table else '无'}"
        ) from None

    def resolve(
        self,
        backtest_start_date: str,
        local_cols_override: Optional[Dict[str, List[str]]] = None,
        platform_cols_override: Optional[Dict[str, List[str]]] = None,
    ) -> "PTradeFidelityConfig":
        """完整校验并物化运行时状态。

        - P-A0 PIT 门禁：fidelity_ashares_snapshot=True 时
            backtest_start_date 必须 >= snapshot_date，否则 ValueError fail-closed；
            snapshot_date 缺失/快照文件缺失 → 显性报错（不得静默关闭）。
        - P-A2 eps basis 双端校验。
        """
        resolved = self._resolve_eps(local_cols_override, platform_cols_override)
        if self.fidelity_ashares_snapshot:
            self._check_ashare_pit(backtest_start_date)
        return resolved

    def _resolve_eps(
        self,
        local_cols_override: Optional[Dict[str, List[str]]] = None,
        platform_cols_override: Optional[Dict[str, List[str]]] = None,
    ) -> "PTradeFidelityConfig":
        eps_basis, ok = self.resolve_eps_basis(local_cols_override, platform_cols_override)
        obj = object.__new__(PTradeFidelityConfig)
        obj.__dict__.update(self.__dict__)
        obj.__dict__["_resolved_eps_basis"] = eps_basis
        obj.__dict__["_dual_end_ok"] = ok
        return obj

    # ------------------------------------------------------------------
    # P-A0 PIT 门禁 + 超龄告警
    # ------------------------------------------------------------------
    def _check_ashare_pit(self, backtest_start_date: str) -> None:
        from datetime import datetime

        snapshot_date = str(latest_snapshot_meta(self.snapshot_dir)["snapshot_date"])
        try:
            start = datetime.strptime(backtest_start_date[:10], "%Y-%m-%d")
            snap = datetime.strptime(snapshot_date, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(
                f"fidelity_ashares_snapshot=True 需要有效日期："
                f"backtest_start_date={backtest_start_date!r}, snapshot_date={snapshot_date!r}"
            ) from e
        if start < snap:
            raise ValueError(
                f"PIT 门禁失败：回测起点 {backtest_start_date} < 快照日 {snapshot_date}。"
                f"平台 A 股池快照只能用于 {snapshot_date} 之后的窗口（快照 PIT 语义），"
                f"请调整 backtest_start_date 或关闭 fidelity_ashares_snapshot。"
            )
        age_days = (start - snap).days
        if age_days > self.snapshot_max_age_days:
            logger.warning(
                "PTradeFidelity: 快照 %s 已超龄 %d 天（阈值 %d 天），"
                "快照 PIT 可信度下降，请尽快更新探针日志重构建快照",
                snapshot_date, age_days, self.snapshot_max_age_days,
            )

    # ------------------------------------------------------------------
    # 快照文件访问（P-A0 消费侧）
    # ------------------------------------------------------------------
    def snapshot_parquet_path(self) -> Path:
        """快照 parquet 路径（data/ptrade_fidelity/ashares_<snapshot_date>.parquet）。

        snapshot_date 取快照目录最新 meta.json（latest_snapshot_meta），
        快照刷新（追加新档）后自动指向最新快照。
        """
        base = Path(self.snapshot_dir) if self.snapshot_dir else Path("data/ptrade_fidelity")
        return base / f"ashares_{latest_snapshot_meta(self.snapshot_dir)['snapshot_date']}.parquet"

    def load_ashare_snapshot(self) -> List[str]:
        """读取平台 A 股池快照 → 裸码列表（6 位）。文件缺失 → 显性报错（fail-closed）。

        快照由 scripts/probe_platform_ashares.py 构建（sha256 校验通过才落盘）；
        此处只消费，不重建。
        """
        path = self.snapshot_parquet_path()
        if not path.exists():
            _meta = latest_snapshot_meta(self.snapshot_dir)
            raise FileNotFoundError(
                f"P-A0 快照文件缺失: {path}\n"
                f"请先运行: python scripts/probe_platform_ashares.py <FASHARES 日志文件> "
                f"(平台探针 probe_fidelity_ashares_ptrade.py 输出，snapshot_date="
                f"{_meta['snapshot_date']}, total="
                f"{_meta['total']})"
            )
        import pandas as pd
        df = pd.read_parquet(path)
        if "code" not in df.columns:
            raise ValueError(f"P-A0 快照无 code 列: {path}")
        codes = [str(c).zfill(6) for c in df["code"].tolist()]
        _meta = latest_snapshot_meta(self.snapshot_dir)
        if len(codes) != _meta["total"]:
            logger.warning(
                "P-A0 快照 code 数量 %d != 探针 total %s（快照被篡改或重建？）",
                len(codes), _meta["total"],
            )
        return codes