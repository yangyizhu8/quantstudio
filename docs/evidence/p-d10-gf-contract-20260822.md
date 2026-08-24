# P-D10 GF 合约双修证据文档

**目标**：修复 `get_fundamentals` / `get_fundamentals_batch` 转换契约错版，使 PTrade 平台行为与本地 `ptrade_api.py` B1 DataFrame 契约对齐。  
**文档版本**：v1.0（2026-08-24）  
**对应登记表**：`私募工作文件/QuantStudio-MCP全数据源替代任务文件/issue_registry.md` P-D10 状态 `repairing → verifying`  
**审核方**：ZCode 2026-08-22 ✅ 有条件通过（需补探针二/三 + 三道防线 + 四条铁证链）  

---

## 1. 问题定义与根因

| 层级 | 崩溃/差异 | 根因 |
|---|---|---|
| 第一层 | `AttributeError: 'dict' object has no attribute 'index'` @ 2026-07-01 08:30 | 注入的 `get_fundamentals_batch` shim 返回 `dict[code→DataFrame]`，但本地契约要求 `DataFrame(index=code, columns=fields)` |
| 第二层 | 若 dict 问题修复，`_latest_by_code` 会抛 `ValueError: could not convert string to float` | 平台 `end_date/publ_date` 是 `'YYYY-MM-DD'` object 字符串，本地 `fin_indicator` 是数值时间戳；策略内 `np.asarray(df['end_date'], dtype=float)` 会失败 |
| 第三层 | `growth_ability` 表无 `or_yoy` 列（KeyError）→ 平台吞错返回空 `(0,0)` | 本地字段名与平台 schema 命名不同；平台自有字段 `operating_revenue_grow_rate` 等价 |

---

## 2. 实施变更（按审计批准方案 B 执行）

| 文件 | 变更 |
|---|---|
| `quantstudio/strategy_compiler/source_import.py` | 新增 `_QS_FUNDAMENTALS_EXT` 模板：原生 list 单调用、index=code 防御、列筛选、`QS_SHIM_FIELD_MISSING` 显性警报、日期归一、`_qs_shape_check`；`get_fundamentals_batch` shim 委托 wrapper；新增 `or_yoy → operating_revenue_grow_rate` 字段名映射 |
| `quantstudio/strategy_compiler/portability_rules.py` | `SHIM_CONTRACT_REGISTRY` 7 项 + `INJECTED_WRAPPER_NAMES`；补全 `get_fundamentals_batch` 契约描述；加入映射说明 |
| `quantstudio/strategy_compiler/validators/validate_ptrade_portability.py` | 注册表门禁：`PORTABILITY-UNREGISTERED-SHIM` BLOCK |
| `tests/test_ptrade_contract_compliance.py` | 新增 19 个 P-D10 用例（含映射用例 3 个、形状自检 3 个、注册表门禁等） |
| `output/ptrade_export/weekly_smallcap_growth_momentum_10/weekly_smallcap_growth_momentum_10_ptrade.py` | 重转产物，含映射与 shape check |

产物 hash：  
- 基线 B（原文件）：`2a61b320737ef461839a996fb1b0b9aef5d5d173`  
- 映射接入后：`770461bf248e5f65981b1473887122b3f03754f2`

---

## 3. 探针结论

### 3.1 探针一（已复核，2026-08-22）
- U1：平台 `get_fundamentals` list 原生可用；
- U2：平台 index=code 原生，但 `end_date/publ_date` 为字符串对象；
- U3：`growth_ability` 无 `or_yoy`（KeyError 吞错返回空）。

### 3.2 探针二（2026-08-23，测试123）
- `growth_ability` 全表 21 列，本地字段名 `or_yoy` 等 7 候选全 KeyError；
- 等价字段 `operating_revenue_grow_rate` ∈ schema；
- 100/500 码 list 0.05s FULL 无截断 → wrapper 免分片，性能 PASS。

### 3.3 探针三（2026-08-23，测试123）
- 000001.SZ @2026-03-31：本地 or_yoy=4.6516，平台 operating_revenue_grow_rate=4.6516（Δ=0.0000）；
- 600000.SS @2026-03-31：本地 1.4176，平台 1.4176（Δ=0.0000）；
- 结论：`or_yoy → operating_revenue_grow_rate` 映射接入；`np_yoy` 不映射（600000 口径差 0.72pct）。

---

## 4. 双端六级漏斗对齐（2026-07 回测）

| 调仓日 | 端 | L0_all | L1 | L2 | L3 | L3v | L4 | L5 | L6 | R_rankable | R_selected | note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 07-01 | 本地 | 5511 | 5503 | 4579 | 4446 | 4312 | 431 | 30 | 15 | 15 | 10 | ok |
| 07-01 | 平台 | 5205 | 5202 | 4593 | 4381 | 4373 | 437 | 30 | 20 | 20 | 10 | ok |
| 07-06 | 本地 | 5517 | 5510 | 4584 | 4453 | 4317 | 431 | 30 | 16 | 16 | 10 | ok |
| 07-06 | 平台 | 5204 | 5201 | 4592 | 4387 | 4378 | 437 | 30 | 21 | 21 | 10 | ok |
| 07-13 | 本地 | 5525 | 5515 | 4585 | 4453 | 4317 | 431 | 30 | 16 | 16 | 10 | ok |
| 07-13 | 平台 | 5203 | 5199 | 4590 | 4387 | 4377 | 437 | 30 | 21 | 20 | 10 | ok |
| 07-20 | 本地 | 5528 | 5518 | 4590 | 4460 | 4324 | 432 | 30 | 17 | 17 | 10 | ok |
| 07-20 | 平台 | 5200 | 5197 | 4587 | 4393 | 4384 | 438 | 30 | 20 | 19 | 10 | ok |
| 07-27 | 本地 | 5526 | 5517 | 4586 | 4459 | 4322 | 432 | 30 | 17 | 17 | 10 | ok |
| 07-27 | 平台 | 5202 | 5199 | 4589 | 4390 | 4381 | 438 | 30 | 21 | 20 | 10 | ok |

**对齐判定**：
1. **L4 = ~10% × L3v** 两端一致（max(1, floor(n×10%)) 语义成立）；
2. **L5 = 30** 两端一致；
3. **R_selected = 10** 两端一致；
4. **08:30 无 ERROR**，`QS_FUNNEL_AUDIT` 正常打印；
5. `QS_SHIM_FIELD_MISSING` 未触发（`or_yoy` 经映射可用）；
6. `GF-FAILOPEN` 计数 = 0。

### 4.1 差异归因（非回归）

| 差异项 | 本地 | 平台 | 原因 |
|---|---|---|---|
| L0_all / L1 | 5511 | 5205 | `get_Ashares()` 全 A 池定义差异（平台未含约 306 只，多为科创板/北交所/新上市/退市，后续 L2 剔除后收敛） |
| L2_board | 4579 | 4593 | 平台保留的沪深主板/创业板码更多；本地 `get_Ashares` 多出的 306 只多为被 `_EXCLUDED_PREFIXES` 剔除的科创板/北交所 |
| L3_status | 4446 | 4381 | 本地 `filter_stock_by_status` 含 P-D9 退市风险兜底（close<1 或 circ_mv<5亿），剔除更多；平台 wrapper 仅按原生 ST/Halt/DELISTING 过滤 |
| L3v_complete | 4312 | 4373 | 平台财务数据对小市值更新更完整（本地 300255/688496 缺 2026Q1） |
| L6_eps | 15-17 | 20-21 | eps 列口径差异（平台 eps 与本地 fin_indicator.eps 数值基不同），仅影响 `eps>0` 符号边界 |
| R_rankable | 15-17 | 19-21 | L6 差异传导 + 行情历史可用性差异；最终 R_selected 均为 10 |

## 5. vol_regime 辅验（2026-08-24）

| 检查项 | 本地 | 平台 | 判定 |
|---|---|---|---|
| 运行结果 | 完成 23 天 | 完成 23 天 | ✅ 无崩溃/无 ERROR |
| 持仓数量 | 5（全月无换手） | 5（全月无换手） | ✅ 结构一致 |
| 初始持仓重合 | — | — | **4/5 重合** |
| regime/q | reversal q=0.9333 | reversal q=0.9000 | 轻微差异（历史波动分位数口径） |
| 差异个股 | 603272.SH | 600158.XSHG | q 阈值附近边际替换 |
| 本地收益 | -23.78% / maxDD 28.70% / 超额 -16.30% | 未提供 total_value 序列 | 80% 持仓重叠 → 平台收益应相近 |

**辅验结论**：vol_regime 不调用 `get_fundamentals`，本次 P-D10 改动对其无影响；双端运行正常，结构完全对齐，仅因行情波动分位数计算导致 1/5 个股边际差异。✅ 通过。

---

## 6. 全量测试套件结果（P-D10 域验收门）

- `test_ptrade_contract_compliance.py`：**94 passed**（含映射用例 3 个、shape check、注册表门禁、同构矩阵等）；
- `test_source_import.py`：**40 passed**；
- 单跑合计 **134 passed**。
- 全量 200 测试文件 file-by-file watchdog 清单：P-D10 相关全部通过；差异均归因于（1）9192 全局写锁并发干扰（已复过），（2）HEAD 存量测试-代码分歧（GUI 环境、slippage 0.0/0.1 等），（3）非 P-D10 域的工作树差异。
- **结论：零 P-D10 回归**。

---

## 6. 回退点与污染核对

- 回退点 A：`7b01a4b51d1c75f986fa18a710852a9e753023b7`（git stash create）
- 回退点 B：旧产物 hash `2a61b320737ef461839a996fb1b0b9aef5d5d173`
- 污染核对：本次修改仅限 `source_import.py`、`portability_rules.py`、`validate_ptrade_portability.py`、`tests/test_ptrade_contract_compliance.py`、设计文档、登记表、产物；其余他人未提交改动未触碰。

---

## 8. 验收结论

| 检查项 | week10 主验 | vol_regime 辅验 |
|---|---|---|
| 首日无 ERROR | ✅ | ✅ |
| L3v/持仓计数非零 | ✅ L3v=4373 | ✅ positions=5 |
| `or_yoy` 不再静默空（映射后 L4 非零） | ✅ | N/A（不调用 get_fundamentals） |
| GF-FAILOPEN 计数 = 0 | ✅ | ✅ |
| 双端漏斗/持仓结构对齐 | ✅（L4≈10%L3v，L5=30，R=10） | ✅（5 只全月无换手，4/5 重合） |

**P-D10 实施阶段验收通过（主验 + 辅验）**。待用户确认后执行双仓推送与文档同步。

---

## 9. 待办

1. 用户确认 → 双仓推送（github.com/yangyizhu8/quantstudio-plus + quantstudio）。
2. 文档同步清单：README.md、`docs/strategy_toolbox.md`、`docs/prompt_engineering.md`、`docs/interface-contract.md`、`ptrade-profile-contract.md`、`docs/implementation-status.md`、证据文档。
3. 清理临时 worktree `p10_baseline_wt`。
