# 明晨（2026-11-10）换装卡 · PD-0019 期 1 隔离验证版（完整隔离形态）

> **用途**：下交易日以**完整隔离形态**取实机证据（今日 11-09 数据已作「修复前侧」对照落册）。
> **贴装动作**：上传并替换模拟盘上的四象限策略代码（**仅 `.py` 一件**）。

## 一、贴装要素

| 项 | 值 |
|---|---|
| 产物路径（本机） | `Demo_account_Real_account/四象限ETF轮动策略-ISO-P1/ETF_f926a7_trade.py` |
| **sha256[:16]** | **`2870d298c5148016`**（贴装前请核此值，不符则勿贴） |
| 体积 | 102,619 B |
| 部署门禁 | P0–P6 **全 PASS**（`deploy` EXIT=0） |
| 隔离配置 | `enabled=true`／`capital=100000.0`／`policy=strict`／`market_split=true` |
| 认货行（首启必现） | `QS-ISO-PHASE1 capital=100000.0 policy=strict market_split=True build=20260911-p1`（以产物内实际串为准） |

## 二、需一并上传吗？——**不需要**

- `.gen.json`（`<sid>_trade.py.gen.json`）是**部署侧闸门认证件**（供 `qs-compile deploy` 的
  IMPORT-FRESHNESS 判「假重转」用），**产物运行时不读它** ⇒ **模拟盘只贴 `.py`**。
- 留档：同目录 `report.md`／`runbook.md`／`.gen.json` 供我方追溯。

## 三、盘中取证四键（沿用）

| 键 | 期望 | 含义 |
|---|---|---|
| `QS_ISO_BANNER` | **首启即现** | 认货：确认跑的是隔离版（今日无此行＝非隔离版） |
| `QS_ISO_FILL` | 有成交时现 | 乐观记账：`code/amount/px/cash_base/reserved/pos` |
| `QS_ISO_TARGET_ORIG_BLOCKED` | **可能出现（预期）** | strict 拒减仓／超限（**是隔离在工作的证据，非缺陷**） |
| `QS_ISO_RECONCILE` | 收盘现 | 收盘核对行：epoch/cash_base/reserved/cash_avail/pos/**rejects 合计** |

## 四、判读纪律（两条，防误判）

1. **策略自报「提交状态=成功」≠ 成交事实**——今日实测自报成功但实有 `[260200]` 失败与挂单残留
   ⇒ **以收盘台账为准**。
2. **`policy=strict` 下「策略没能卖出」是预期**（`target_orig` 全禁的必然结果）⇒ 不判为缺陷。

## 五、若隔离层未生效的判读分支

- 无 `QS_ISO_BANNER` ⇒ **未进入隔离路径**（不是隔离版，或未走到下单链路）⇒ 先核贴装 sha，
  再核是否当日无调仓。
- 有 `QS_ISO_BANNER` 但无 `QS_ISO_FILL` ⇒ 未成交（拒单或无调仓）⇒ 看 `rejects` 与
  `QS_ISO_TARGET_ORIG_BLOCKED`。

## 六、留痕

取证后请回传：①启动首行（含 banner）②14:50 调仓段 ③收盘 `QS_ISO_RECONCILE` 行 ④收盘持仓/资金台账
⑤委托表全量。⇒ 我方出「修复前（11-09 旧产物）vs 修复后＋隔离（11-10）」对照分析。
