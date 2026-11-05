---
title: 批 0 实施前置取证（合并判据）——两处「复用」不成立，须修订实施口径
date: 2026-11-05
status: 只读取证结论（不改代码）；供批 0 开工时直接采用，防开工即踩坑
knowledge_refill: [命中] QD-2026-0011（qd/D1/knowledge/qmt/QD-2026-0011.md，2026-10-28）——日键口径铁律
---

# 一、取证背景

合并判据修订稿 R1 主张「**复用** `normalize_code`（pipeline/aligner.py）+ **以** `time_axis.py` 为日键唯一源」。批 0 开工前核实这两个「复用」的真实可用性——**两处均不完全成立**。

# 二、坑 1：`normalize_code` 不能直接用（默认值拒绝带后缀码，且缺 `.SS`）

> 源码实测：`quantstudio/pipeline/aligner.py:29-66`

| 口径 | 行为 | 对 `600556.SH` | 对 `600556.SS` |
| --- | --- | --- | --- |
| `fmt="identity"`（**默认**） | `return code if re.match(r'^\d{6}$', code) else None` | **None** ❌ | **None** ❌ |
| `fmt="tushare_to_raw"` | 正则 `^(\d{6})\.(SH\|SZ\|BJ\|SI)$` | `600556` ✅ | **None** ❌（**无 SS 分支**） |
| `fmt="baostock_to_raw"` | `^(sh\|sz\|bj)\.(\d{6})$` | **None** ❌ | **None** ❌ |

**结论**：
1. **绝不可用默认 `fmt="identity"`** —— 带后缀码一律返回 `None`（静默丢数据，且下游 `.apply` 场景会把行变 None）。
2. **`tushare_to_raw` 覆盖不足**：项目内 `.SS` 后缀真实存在（B2′ 审计文件 `600452.SS`/`600968.SS`、`trades.csv` `600556.SS`）。
3. 生产既有调用点**全用 `tushare_to_raw`**（`daemon.py:1869/2058/3603/3879/3914`），说明现状已默认「无 SS」。

## 批 0 实施口径（据此定）

| 项 | 决定 |
| --- | --- |
| 落点 | **新增薄封装** `code_key(code)`（`backtest/libs/` 或 `pipeline/` 就近），内部调 `normalize_code(code, "tushare_to_raw")`，**补 SS 后再试**：失败则走 `re.search(r'(\d{6})')` 兜底 |
| **不改 `normalize_code` 本体** | 改本体=改既有生产行为（daemon 全线受影响）⇒ **跨线风险**，批 0 零迁移要求下禁止 |
| 判型 | **新增能力型**（新封装开始正确处理 `.SS`，既有调用路径不变） |
| 验收 | `.SH/.SZ/.BJ/.SS/.SI/裸码/6 位非码` 七形态单测；既有 `tushare_to_raw` 调用点结果**逐位不变** |

# 三、坑 2：`time_axis.py` **没有整数日键函数**（须新增，非复用）

> 源码实测：`quantstudio/backtest/providers/time_axis.py`（全文仅 4 个导出）

| 已有 | 签名 | 语义 |
| --- | --- | --- |
| `DAY_MS` | 常量 `86_400_000` | 一天的毫秒数 |
| `start_ms(date)` | `str -> int` | 某**日期串** → 当日起点 ms |
| `end_ms(date)` | `str -> int` | 某**日期串** → 当日终点 ms |
| `day_start_ms(ms)` | `int -> int` | 某 ms → **该日起点 ms** |
| `is_day_start_ms(ms)` | `int -> bool` | 是否日界 |

**缺失**：项目内广用的**整数日键** `(ms + 28800000) // 86400000`（覆盖闸 `coverage_gate._DAY_KEY_EXPR`、审计脚本、A2/A3 分析均用它做「同日」判定与跨表联接）—— **time_axis 中无对应函数**。

## 为什么不能直接拿 `day_start_ms` 顶替

- `day_start_ms` 返回 **ms 时间点**（可与 `start_ms/end_ms` 比较）；整数日键返回 **整数序号**（可与 DuckDB `(time+28800000)//86400000` 直接等值联接）。
- 两者可互转但**不等价替换**：SQL 侧日键表达式无法调用 Python 函数 ⇒ 必须保持**同一算术式**（`+28800000` / `//86400000`）在 Python 与 SQL 两侧一致，否则又是「口径两处各写」。

## 批 0 实施口径（据此定）

| 项 | 决定 |
| --- | --- |
| 落点 | 在 `time_axis.py` **新增** `day_key(ms) -> int` = `(ms + 28_800_000) // DAY_MS` + `day_key_now()` 若需要；并在模块 docstring 声明「**这是 Python↔SQL 共用日键的唯一算术定义**」 |
| SQL 侧 | `coverage_gate._DAY_KEY_EXPR` 保持 `((time + 28800000) // 86400000)`，并在注释中**指向** `day_key` 同一算术（交叉引用，不复制实现——SQL 无法 import，只能靠注释 + 审计脚本比对） |
| 审计脚本 | `check_key_normalization.py` 增加一条规则：凡生产代码出现 `86400000`/`28800000` 字面量且**非** `time_axis.py` 内部 ⇒ WARN 并建议改用 `day_key` |
| 判型 | **新增能力型**（新增函数，既有行为零改变） |
| 验收 | `day_key` 与既有各点算术**逐值一致**（取 10 个跨年/跨时区边界 ms 对拍）；既有测试全绿 |

# 四、对合并判据修订稿的补正（须在②审前或②审时同步）

| 修订稿原表述 | 补正 |
| --- | --- |
| 「复用 `normalize_code`」 | **改为**「复用 + 补 SS 的薄封装 `code_key`，**不改本体**」 |
| 「以 `time_axis.py` 为唯一源（start_ms/end_ms/day_start_ms/is_day_start_ms）」 | **改为**「以 `time_axis.py` 为唯一源，**并新增 `day_key(ms)`**（整数日键此前不存在；SQL 侧同一算术靠注释+审计比对）」 |

# 五、边界

- 本件为**只读取证**：未改任何代码、未跑回测、未提交代码。
- 两处判型均为**新增能力型**候选，最终判型仍以②审为准。
- 本件结论若与②审意见冲突，以②审为准。