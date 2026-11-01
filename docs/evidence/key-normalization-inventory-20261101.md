---
title: 跨表口径归一 · 存量调用点盘点（批 0 输入）
date: 2026-11-01
purpose: 为「联接键归一 + 日键复用」合并判据的分批迁移提供排期依据
knowledge_refill: [命中] QD-2026-0011（qd/D1/knowledge/qmt/QD-2026-0011.md，2026-10-28）——日键口径铁律
---

# 一、盘点范围与方法

- 范围：`quantstudio/`、`scripts/`、`tests/` 下全部 `.py`（排除 `__pycache__`、`_bak`）
- 正则：
  - code 后缀：`split('.'.)` / `rsplit('.')` / `.SS` / `.SZ` / `.SH`
  - 日键自写：`86400000` / `28800000` / `DAY_MS` / `CST_OFFSET`
  - 现有归一函数：`def (code_key|day_key|normalize_code)`
- ⚠ **正则较粗**（会命中字符串字面量、非联接用途）⇒ 读数**为上界**，非需改点计数

# 二、读数（上界）

| 类 | 文件数 | 匹配数 | 密度 Top |
| --- | --- | --- | --- |
| **code 后缀** | **261** | **5,515** | `strategies/first_cover_event_daily_quantstudio.py` **2,341**（策略源·禁改）<br>`tests/test_engine_basket_rebalance.py` 293<br>`tests/test_ptrade_contract_compliance.py` 215<br>`tests/test_source_import_qmt.py` 95<br>`quantstudio/strategy_compiler/source_import.py` 55<br>`quantstudio/pipeline/sources/tushare_adapter.py` 43 |
| **日键自写** | **44** | **230** | `tests/test_derive_market_value.py` 33<br>`tests/test_daily_snapshot_cache_key.py` 14<br>`scripts/_verify_front_fix.py` 12<br>`scripts/_verify_minutes_fix.py` 12<br>`quantstudio/backtest/libs/coverage_gate.py` 11<br>**`quantstudio/backtest/providers/time_axis.py` 7（既有权威实现）**<br>`quantstudio/pipeline/writers.py` 6 |

> **口径声明**：5,515 / 230 均为**上界读数**。**批 0 审计脚本产出的存量 WARN 报告**才是权威口径，取代本表。

# 三、关键发现（改变方案落点）

| # | 发现 | 影响 |
| --- | --- | --- |
| 1 | **已存在 `quantstudio/pipeline/aligner.py :: normalize_code`**（签名含 `fmt="identity"`） | 原草案「新增 `code_key()`」**作废**⇒ 改为**复用/提升**，禁造第二套 |
| 2 | **`quantstudio/backtest/providers/time_axis.py` 已含日键函数族**（`start_ms` / `end_ms` / `day_start_ms` / `is_day_start_ms`） | 原草案「新增 `day_key*()`」**作废**⇒ 改为以其为唯一源 + 导公共别名 |
| 3 | code 后缀最大密度在**策略源**（2,341 处） | **禁改**（铁律 07）⇒ 迁移范围必须排除 `strategies/**` |
| 4 | tests 占比极高（293/215/95...） | tests **不迁**，只补新用例 |

# 四、迁移范围（收窄后）

| 批 | 范围 | 门禁 |
| --- | --- | --- |
| **批 0** | 函数提升（复用 `normalize_code` / 以 `time_axis` 为唯一源）+ `scripts/check_key_normalization.py` 审计（**新代码 BLOCK / 存量 WARN / 零迁移**） | 审计脚本可运行、误报可接受 |
| **批 1** | `backtest/libs/` + `backtest/providers/`（含 `coverage_gate.py` 自查） | **黄金 sha256 一致** |
| **批 2** | `pipeline/`（`writers.py`、`tushare_adapter.py`、`aligner.py` 周边） | 同上 |
| **批 3** | `scripts/`（`_verify_*.py`、`profile_*` 等取证脚本） | 同上 |
| **不迁** | `strategies/**`（禁改）、`tests/**`（只补用例） | — |

# 五、前置与结论

- **批 0 开工前置 = 修订稿过 11-04 ②审 + ⑤ 用户确认**（未过不开工）。
- 因 `time_axis.py` 与 `aligner.py` 已有权威实现 ⇒ **实际工作量控制在「导出 + 迁移调用点 + 审计」**，不涉新算法。
- 本轮盘点为**只读扫描**，未改动任何文件。