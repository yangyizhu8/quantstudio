# F-LOCAL-MIN B2 修复设计：分钟链日期契约归一 + 静默消音（v1，2026-09-05）

> 六步流水线主线 B 第 2 步（B1 定谳已复核通过：docs/evidence/f-local-min-b1-verdict-20260905.md）。
> 审核要点①-⑤全部吸收；B3 预警（日线 profile 分钟链路不触发 → 假绿风险）已纳入验收门④设计。

## 1. 问题定义（B1 定谳，证据确凿）
- **根因**：`iter_trading_days_in_range`（providers/intraday_windows.py L83-114）签名声明 `start_date: str, end_date: str`、实现按字符串切片（L98/105/109）；调用方 ptrade_api L1330/L1321 传 `pd.Timestamp`（anchor_date）→ `TypeError: 'Timestamp' object is not subscriptable`。
- **受影响调用点（全库枚举，审核方复核确认）**：duckdb_data_access.py **L860（单只版）+ L934（批量版）** 两处，均直接透传未归一。
- **静默机制**：ptrade_api L1375-1377 `except Exception → logger.debug → return {}`（debug 默认不可见）+ 策略侧防御性 except → 「策略环境内静默异常零输出」。
- 影响面：本地引擎全部分钟 get_history（四组合全灭）；平台侧不受影响；日线不受影响。

## 2. 改动范围与影响面
1. **quantstudio/backtest/providers/intraday_windows.py**（主修复）：新增 `_as_date_str(value)` 归一辅助 + `iter_trading_days_in_range` 入口归一——**一处修复全链覆盖**（L860/L934 两调用点自动受益）。
2. **quantstudio/backtest/providers/duckdb_data_access.py**（双保险）：L860/L934 两处调用前对 start/end 同款归一（防调用方再违约；与主修复互不依赖，双层防护）。
3. **quantstudio/backtest/ptrade_api.py**（辅修复，**共享核心文件**——走共享纪律）：L1375 兜底 catch `logger.debug` → **限频 warning**（首条全量 + 计数聚合，对齐 F-DUCKDB-LOCK P3 消音模式；FrequencyCapabilityError 的 L1371 re-raise 分支**保持不变**——既有先例结构不动）。
4. **tests/test_minute_date_contract.py**（新增契约测试）。
- 策略源码零改动；平台侧零影响（此 Python 层不存在于平台）；纯增益（字符串入参的既有行为字节级不变——归一只对非 str 形态生效）。

## 3. 设计本体

### 3.1 主修复：`_as_date_str` 归一（intraday_windows.py）
```python
def _as_date_str(value) -> str:
    """日期契约归一：str（原样截取前 10 位，兼容带时刻串）/ pd.Timestamp / datetime.date
    / datetime.datetime / int/float epoch-ms → 'YYYY-MM-DD'。不可识别形态原样返回
    （保持既有异常行为，不在本层新造失败）。"""
    if isinstance(value, str):
        return value[:10]
    ts = getattr(value, "strftime", None)          # Timestamp / datetime / date
    if ts is not None:
        return value.strftime("%Y-%m-%d")
    try:                                            # epoch-ms 数值（容错形态）
        return pd.Timestamp(int(value), unit="ms").strftime("%Y-%m-%d")
    except Exception:
        return value                                # 不可识别：原样交回（切片报错归调用方，行为同旧）
```
- `iter_trading_days_in_range` 入口：`start_date, end_date = _as_date_str(start_date), _as_date_str(end_date)`——缓存键/切片/日历调用全部吃归一后字符串（L98/105/109 三处切片点自动消除）。
- **归一效果已由审核方实测**：`str(ts)[:10] → '2026-07-01'`；本设计 strftime 形态等价且不依赖 str() 的表示格式。

### 3.2 双保险（duckdb_data_access.py L860/L934）
两处调用前：`day_strs = iter_trading_days_in_range(_as_date_str(start_date), _as_date_str(end_date), calendar_provider)`（归一辅助从 intraday_windows 导入，单一实现不复制）。

### 3.3 辅修复：ptrade_api L1375 限频告警
- 类级计数器 `_qs_hist_fail_counts`：每类异常文本（截 120 字符作键）首条 `logger.warning("QS_HIST_FAIL %s", ...)` 全量告警 + 后续计数；warning 文本含异常类型+截断消息+调用参数摘要（count/frequency/码数）。
- **FrequencyCapabilityError 分支（L1371-1374 re-raise）零改动**（既有先例：能力错误必须上抛）。
- 不改变返回契约（失败仍 return {} / pd.DataFrame()——行为契约不变，只把「不可见」变「可见」）。

### 3.4 契约测试（tests/test_minute_date_contract.py，新增）
1. **双形态不炸**：Timestamp 入参调用 `iter_trading_days_in_range` 返回与等价字符串入参逐位一致（打板复现场景回归——b1_repro_v2 同构造）；
2. **归一函数补充形态**（审核要点③）：str（含带时刻 '2026-07-01 09:31:00'）/ Timestamp / datetime.date / datetime.datetime / epoch-ms int 五形态 → 一致 'YYYY-MM-DD'；
3. **get_history 端到端**：Timestamp anchor 下分钟调用返回非空（打板四组合场景，本地库真数据）——B1 零输出的反断言；
4. **限频告警**：异常注入 → 首条 warning + 二次零告警 + 计数聚合（对齐 P3 测试模式）；
5. **五分支矩阵沿用**：FrequencyCapabilityError 三分类（TABLE_MISSING/TABLE_EMPTY/FREQ_NOT_IN_TABLE）在 Timestamp 入参下语义不变（原 raise 仍 raise）。

## 4. 验收标准
- 门① 契约测试全绿（上述 5 组）；
- 门② 回归：契约门失败集 ⊆ 钉死清单（duckdb-lock-regression-baseline-20260905.md §4 规则）；白名单 5 项不新增触发；
- 门③ 复现场景反断言：b1_repro_combos 四组合在修复后全返回非空 CodeDict（或按数据实际覆盖返回结构化空——以直调数据层非空为准绳）；
- 门④ **B3 预警落实**：E2E 验证改用**产物本体驱动**（v10.4.1 parity 方式）或分钟驱动形态——日线 profile 下分钟链路不被驱动，日线 profile 的 E2E 不得作为「分钟链路清零」的证据（防假绿）；
- 纯增益：字符串入参既有行为字节级不变（归一只作用于非 str 形态）；6 策略重转 api_portability 不涉及（无模板改动——本修复纯引擎数据层+API 层，产物零重转）。

## 5. 回退条件
- 归一引入任何字符串路径行为变化（既有测试/黄金结果漂移）→ 立即回退；
- 限频告警导致日志量异常或性能回退 → 立即回退；
- 实施前 stash create + **store 持久化** + 精确文件清单 add（intraday_windows.py / duckdb_data_access.py / ptrade_api.py / 新测试文件——**ptrade_api.py 属共享核心文件**，叠加他方在途 D4 缓存键改动（±4 行）须在提交信息显式记录）。

## 6. 全部调用点清单（备查，审核要点⑤）
- `iter_trading_days_in_range` 调用点：duckdb_data_access.py **L860**（query_minute_bars_by_range 单只版）、**L934**（query_minute_bars_by_range_batch 批量版）——全库枚举仅此两处（审核方独立枚举一致）；
- `build_intraday_sql_conditions` 消费 day_strs（L936/L865），入参为归一后字符串链——无直接契约暴露。
