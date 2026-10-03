# 验收证据：get_index_stocks 日期归一（F3）

> 六步流水线**第 4 步（验收）** ｜ 日期：2026-10-03 ｜ 实施方：策略线会话（DSH）
> 方案件：`docs/get-index-stocks-date-normalization-design.md`（②审计 PASS + V2 实施形态补强）
> 落点：`quantstudio/backtest/ptrade_api.py`（`get_index_stocks` 日期契约 + 新增 `_qs_normalize_date_arg`）
> 回退点：`git stash` → `91f59d379be6d9093c72219b8116de89d4778967`（写前快照，操作前建立）
> 实施前 HEAD：`1e919ac`｜实施前该文件工作区**干净**（已核无他人未提交改动）

## 1. 改动内容

| 位置 | 改动 |
| --- | --- |
| `class PtradeAPI` 前（模块级） | 新增 `_qs_normalize_date_arg(value)`：返回 `YYYY-MM-DD`；无法识别返回 `None` |
| `get_index_stocks` 日期分支 | `effective_date = str(date)[:10]` → `_qs_normalize_date_arg(date)`；返回 `None` 时 **fail-closed** |
| `get_index_stocks` docstring | 增补「**返空可能系非法日期形态**」声明（审计要求） |

**diff 规模**：`1 file changed, 54 insertions(+), 2 deletions(-)`｜**AST 解析 OK**｜**仅动 `ptrade_api.py`**

### 1.1 V2 实施形态（审计补强，已照办）

非法形态 → `logger.warning` + **返空列表**（**不 raise**——raise 会中断策略 `handle_data` 当日执行链）。
实测：非法入参 `'abc'` → 返回 `[]`、**Provider 调用次数 0**、产出 warning，与「未传 date」的兼容路径**可区分**。

### 1.2 归一覆盖形态（审计要求 + 一处显式声明）

| 形态 | 结果 |
| --- | --- |
| `8 位串 'YYYYMMDD'` | `YYYY-MM-DD`（经 `pd.Timestamp` 校验，非法日历日如 `20261301` → `None`） |
| `横线串 'YYYY-MM-DD'` | 原样（同样校验） |
| `datetime.datetime`（含 `pd.Timestamp`） | `strftime('%Y-%m-%d')` |
| `datetime.date` | `strftime('%Y-%m-%d')` |
| **其它字符串** | 先试 `pd.Timestamp` 解析；**成功则用其日期**，失败才判不可识别 |
| 非上述类型 / `None` / 空串 | `None` |

> **显式声明的实现取舍（请审核方知悉）**：审计意见的字面要求是「不可识别仅限非上述形态的字符串」——
> 若严格照此，则 `'2026-01-15 09:31:00'` 等**修复前可用**的字符串形态会变为返空，**直接违反方案件 V4**
>（「既有调用形态的返回值逐位不变；出现任何取值差异即验收失败」）。**两条要求不可兼得，我按 V4（更硬的门）取舍**：
> 保留 `pd.Timestamp` 回落，仅在**连 pandas 也无法解析**时才判不可识别（如 `'abc'` / `''` / `'2026-13-01'`）。
> 副作用示例：`'2026'` → `2026-01-01`（**与修复前逐位一致**，非新增行为）。

## 2. 验收结果（V1-V4 全 PASS）

| # | 判据 | 结果 | 证据 |
| --- | --- | --- | --- |
| **V1** | 形态矩阵：5 种形态 → 同一成分列表 | **PASS** | 5 形态 `_qs_normalize_date_arg` 全等 `'2026-01-15'`；端到端（stub provider）**4 形态传给 Provider 的 `effective_date` 全等** |
| **V1b** | 边界与回归保护 | **PASS** | `'2026-01-15 09:31:00'`→`2026-01-15`（**回归保护生效**）；带空白串归一；`datetime` 带时分秒只取日期；`'20261301'`/`'2026-13-01'`/`'abc'`/`''`/`None` → `None`；`'2026'`→`2026-01-01`（与修复前一致） |
| **V2** | 非法形态 fail-closed，且不静默取当前快照、可与「未传 date」区分 | **PASS** | 非法入参 → 返回 `[]` + **Provider 调用 0 次** + warning（实测 `WARNING:get_index_stocks(000300.SS, date='abc'): 日期形态无法识别 -> 返空列表（fail-closed）`） |
| **V3** | PIT 不变量：显式 date 路径永不回退未来快照 | **PASS** | 4 形态端到端 `effective_date` 恒为 `'2026-01-15'`（**绝非 `None`**）；`status='complete'` as-of SQL 未触及（`duckdb_data_access.py` 零改动） |
| **V4** | 回归：单测 + 契约门禁 + 既有策略结果不变 | **PASS** | `run_contract_gate.py --strategies` → **CONTRACT GATE : PASS**（契约矩阵门禁 + pytest 契约套件 + **6 策略 api_portability 冒烟**，既有白名单无触发） |

## 3. 影响面声明（复核方案件 §3）

| 维度 | 实测确认 |
| --- | --- |
| 已能正确解析的形态 | **结果不变**（V1/V1c/V4 实证） |
| 修复前靠容错勉强解析的形态 | **结果不变**（`pd.Timestamp` 回落保留） |
| 当前静默容忍的垃圾输入 | 由「透传下游报错」变为「**本层早失败** + 明确日志」（fail-closed 前移） |
| 策略源码 | **零改动**（仅框架 `get_index_stocks`） |
| 波及 | 所有调用 `get_index_stocks(..., date=...)` 的策略与测试（6 策略 + 4 测试件，V4 已覆盖） |

## 4. 待办（⑤用户确认后）

| # | 项 |
| --- | --- |
| 1 | ⑤用户确认 → ⑥双仓库推送（精确清单；推送后双远程 HEAD 逐位核对 + 留痕行） |
| 2 | 推送后 QuantStudio-trading 副本同步门（触及 `quantstudio/` 共享层，**不豁免**） |
| 3 | 与「换算价件」的分立声明：两件同文件不同函数（`get_index_stocks` vs `_qs_wire_order_target_value`），各自独立回退 |

## 5. 结论

**六步①方案 →②审计（PASS+补强）→③实施 →④验收（V1-V4 全 PASS）已完成。**
契约文档与实现已一致（「标准化为 YYYY-MM-DD」的承诺由实现兑现）；非法形态 fail-closed 且**不中断策略执行链**；
既有调用形态**零回归**（含 `pd.Timestamp` 回落保护），契约门禁全绿。**待用户确认后进入⑥双仓库推送。**
