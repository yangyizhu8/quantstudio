# P-D11b 前置取证：context.portfolio.positions 容器契约探针（P-POS-2，2026-08-27）

- 探针：`ptrade/probe_portfolio_positions_ptrade.py`（双标的：600000.SS 股票 + 510300.SS 沪 ETF）
- 平台回贴：2026-08-27 02:02（探针测试账号，4 交易日 D1-D4）
- 关联：范围外发现①（P-D11b 候选）、master-plan WP-A §6.1

## 1. 探针结果原文（关键行）

```
PPOS2-D1 KEYS n=2 raw=['510300.SS', '600000.SS']                      ← 容器键 = .SS 两位！
PPOS2-D1 ITEM '510300.SS' FIELDS {amount:100, cost_basis:5.048,
    last_price:4.998, market_value:499.8, sid:510300.SS, enable_amount:0}
PPOS2-D2 ITEM '600000.SS' FIELDS {..., enable_amount:200}             ← T+1 解锁
PPOS2-D2 MEMBERSHIP {'600000.SS':True,'600000.XSHG':False,
    '600000.SH':False,'600000':False, ...}                            ← 精确匹配（非 alias-aware）
PPOS2-D2 DIFF wrapped_n=2 only_wrapped=['510300.XSHG','600000.XSHG']
             container_n=2 only_container=['510300.SS','600000.SS']  ← 双体系并存确证
PPOS2-D3 KEYS n=2（amount=0 残影保留）                                  ← 与 F4 同款
PPOS2-D4 KEYS n=0（次日清理）                                          ← 同款
```

## 2. 契约事实表（容器 vs get_positions vs 本地）

| 维度 | get_positions()（P-POS F1-F5） | context.portfolio.positions（本探针） | 本地 ptrade_api |
|---|---|---|---|
| 键后缀 | XSHG/XSHE 四位（F1） | **.SS/.SZ 两位**（D1 KEYS） | .SS/.SZ（_get_ptrade_positions） |
| Position 字段 | amount/cost_basis/last_sale_price/market_value/sid/enable_amount（F3） | **同构**（D1 FIELDS 全有） | 同构（ptrade_api.Position） |
| enable_amount | DIR 有（F3） | **T+1 语义正确**（D1=0→D2=100/200） | 引擎 can_sell 驱动 |
| 残影行 | 当日 amt=0 保留、次日清（F4） | **同款**（D3 amt=0 保留→D4 清） | 过滤 volume>0（恒净） |
| membership | — | **精确匹配**（.SS True / XSHG·SH·裸码 False） | 精确匹配（test_alignment L93） |
| .SH 崩溃 | get_position('.SH') AttributeError（F5） | 容器 .SH 键 False（无崩溃） | — |

## 3. 判定（P-D11b 必要性证伪）

**核心发现：容器的键本来就是 .SS/.SZ 两位后缀——与 P-D11 归一后的 get_positions() 及本地完全一致，双体系问题在容器路径不存在。**

| 原范围外发现①假设 | 探针实证 | P-D11b 处置 |
|---|---|---|
| 容器键格式未实证（可能 XSHG）→ 需 AST 改写归一 | **容器键 = .SS/.SZ 已与本地一致** | **无需键归一 AST 改写** |
| membership 可能 alias-aware 分叉 | **精确匹配，与本地一致** | 无分叉，无需处理 |
| 残影行为未实证 | **与 get_positions() 同款**（当日 amt=0→次日清），P-D11 已对 get_positions 做 amt>0 过滤 | 容器路径策略（mixed 消费）已有 get_positions() 二次确认兜底 |
| .SH 崩溃风险 | 容器 .SH 键 False（无崩溃） | 无需规避 |

**结论：P-D11b 关闭**——探针证伪了"容器键体系需修复"的假设，避免了不必要的 AST 改写实施（高风险大改动）。剩余登记项：
1. 容器残影在 audit 计数中的虚增（weekly 调仓日 positions=12 vs 实际 10）——已观察现象，影响仅审计口径，非交易行为；mixed 消费策略（weekly L344 用 get_positions() 二次确认）已有兜底；
2. fall_reversal 自维护 g 与容器无关（已重归因 DS4/WP-F）。

## 4. 附带确认

- enable_amount T+1 语义在容器路径正确（D1 当日买 0 → D2 100/200）——与 get_positions() 及本地一致；
- ETF（510300）与股票（600000）键格式一致（均 .SS/SZ）——无 ETF/股票键差异。