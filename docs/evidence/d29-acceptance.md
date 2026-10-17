# D-29 ④验收证据（profit_ability 映射，七判据）

- **日期**：2026-10-17（③实施当日；②审通过后）
- **基线**：HEAD `4764a0e`（D-33 推送后）｜快照 `5c2466b3`
- **方案**：`docs/qmt-profit-ability-mapping-design.md` v1.0（②审通过）
- **端测件**：`docs/probe/qmt_profit_ability_e2e_probe-20261017.py`（v1，判据 3）／
  `docs/probe/qmt_profit_ability_e2e_probe_v2-20261017.py`（v2 同期对齐，判据 4/5）

## 一、判据 1–7 判定汇总

| # | 判据 | 判定 | 证据 |
| --- | --- | --- | --- |
| 1 | 矩阵哈希同批追认 | **PASS** | `check_fund_matrix --check --reverify`：`qmt_wrapper_hash 20f58868117f → 6927447ea23c`（yaml＋md 同批）；追认后 `--check` **OK**；负向门 `test_fund_matrix_qmt_gate.py` **5 passed** |
| 2 | 六策略零回归 | **PASS** | `run_contract_gate.py --strategies` → **CONTRACT GATE : PASS**（`docs/evidence/d29-contract-gate.txt`）；既有 `test_source_import_qmt.py` **49 passed**；QMT 四套件 **53 passed**（零衰减） |
| 3 | 映射生效·消费点可达性 | **PASS** | 2026-10-17 15:23 端测（v1）：`QSFUNDHIST t=profit_ability` **kept=152**（改前恒 0）；`[D29_E2E] C1_roe rows=152 nonnull=152 cols=['roe']`；`C2_npm rows=152 nonnull=152`；`QS_QMT_HISTDBG3 t=profit_ability rows=8743 ua=ut=uk=uv=79` → `QS_QMT_HISTFOLD after=79 intravar=0` |
| 4 | 口径 A/B 对照 | **PASS（口径一致·逐位一致）** | 见 §二（2026-10-17 15:45 端测 v2 同期对齐） |
| 5 | 阈值档对照 | **不适用（附理由）** | F-Score 的 ROE 用法＝**降序取前 8**（`F-Score选股RSRS择时.py:341/:347`；`:6` 的 `ROA≥0.5pct` 为 ROA 阈值非 ROE）⇒ **仅排序/取前 N、无阈值**，单位不敏感 ⇒ 按方案记不适用 |
| 6 | 契约矩阵登记 | **PASS** | yaml `meta.qmt_rows`：`qmt-pa-01`（roe→du_return_on_equity）／`qmt-pa-02`（net_profit_margin→net_profit），**两行各自验收** |
| 7 | 纯增益 | **PASS** | `source_import_qmt.py` diff = **+13 行 / 0 删除**（仅新增 `profit_ability` 一行）；eps/growth_ability/valuation 三映射逐字未变（单测断言）；既有套件零衰减 |

## 二、判据 4 逐位比对（同期对齐）

**本地基线**：`data/quantstudio.db` → `fin_indicator`（只读查询，留档 `docs/evidence/d29-local-roe-baseline.txt`）
**QMT 读数**：v2 探针 2026-10-17 15:45（`[D29_E2E2]` 行，`(end_date_ms, 值)` 对）

| 代码 | 期次 | 本地 `fin_indicator.roe` | QMT `du_return_on_equity` | 判定 |
| --- | --- | --- | --- | --- |
| 000001 | 2023-09-29 | 8.8025 | 8.8025 | **IDENTICAL** |
| 000001 | 2023-12-30 | 10.2436 | 10.2436 | **IDENTICAL** |
| 000001 | 2024-03-30 | 3.1176 | 3.1176 | **IDENTICAL** |
| 000001 | 2024-06-29 | 5.4242 | 5.4242 | **IDENTICAL** |
| 000001 | 2024-09-29 | 8.2528 | 8.2528 | **IDENTICAL** |
| 000001 | 2024-12-30 | 9.2038 | 9.2038 | **IDENTICAL** |
| 000001 | 2025-03-31 | 2.8165 | 2.8165 | **IDENTICAL** |
| 000001 | 2025-06-30 | 4.9497 | 4.9497 | **IDENTICAL** |
| 000001 | 2025-09-30 | 7.5711 | 7.5711 | **IDENTICAL** |
| 000001 | 2025-12-31 | 8.1514 | 8.1514 | **IDENTICAL** |
| 000001 | 2026-03-31 | 2.652 | 2.652 | **IDENTICAL** |
| 600000 | 2023-06-29 | 3.2797 | 3.2797 | **IDENTICAL** |
| 600000 | 2023-09-29 | 3.9596 | 3.9596 | **IDENTICAL** |
| 600000 | 2024-06-29 | 3.6712 | 3.6712 | **IDENTICAL** |
| 600000 | 2024-09-29 | 4.8669 | 4.8669 | **IDENTICAL** |
| 600000 | 2024-12-30 | 6.195 | 6.195 | **IDENTICAL** |
| 600000 | 2025-03-31 | 2.3725 | 2.3725 | **IDENTICAL** |
| 600000 | 2025-06-30 | 3.8893 | 3.8893 | **IDENTICAL** |
| 600000 | 2025-09-30 | 4.9494 | 4.9494 | **IDENTICAL** |
| 600000 | 2025-12-31 | 6.4403 | 6.4403 | **IDENTICAL** |
| 600000 | 2026-03-31 | 2.1641 | 2.1641 | **IDENTICAL** |

**判定 = 口径一致（逐位一致）**：既非「倍数恒定（单位差）」亦非「量级一致数值不等（口径差）」
⇒ `du_return_on_equity` 与本地 `roe` **同口径同单位**（年内累计百分比 ROE），**无需任何换算系数**。

**两点如实披露**：
1. **覆盖差异（非口径差）**：本地 `fin_indicator.roe` 最早 **2018Q1**（ETL 回溯深度）；QMT 侧 **79 期回溯至 1990s**
   ⇒ 仅 2018+ 区间可同期比对；本表已覆盖 2023Q3–2026Q1 全重叠区（21/21 逐位一致）。
   另：600000 本地缺 **2024-03-30** 期（QMT 有 2.3738）⇒ **本地缺期**（ETL 覆盖缺口，非口径差），登记为观察项。
2. **`qmt-pa-02`（net_profit_margin）对照不可比**：本地 `fin_indicator` **无该列**（列集：eps/diluted_eps/bps/roe/
   pe_ttm/pb/ps_ttm/np_yoy/or_yoy/tr_yoy 等）⇒ 该行**口径 A/B 无本地基线**；但映射本身有效
   （`C2_npm rows=152 nonnull=304`）。建议：如需口径对照，另寻本地源（PTrade 口径文档／其他表）——**候独立立项**。

## 三、④验收结论

**④验收通过**：判据 **1/2/3/4/6/7 PASS**；判据 **5 不适用（附理由）**；`qmt-pa-02` 口径对照**不可比（附理由＋观察项登记）**。
**→ 呈 ⑤ 用户确认**（经总调度转呈）。

**端测时点合规**：15:23 / 15:45 均**盘后** ✓；两轮同窗同参数，读数稳定（kept=152 两轮一致）。
