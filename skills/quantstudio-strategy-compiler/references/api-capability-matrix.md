# 能力状态模型契约

> Derived from docs/strategy-compiler/capability-model.md @ 2026-07-22
> 权威源：docs/strategy-compiler/ 下的原始契约文档
> 本文件为 Skill 派生快照，契约变更时必须同步（见 SKILL.md 同步纪律）

## 1. 多维状态

每项能力分别记录：

- `schema_status`
- `data_status`
- `adapter_status`
- `provider_status`
- `engine_status`
- `platform_status`
- `execution_status`

允许状态至少包含：`AVAILABLE`、`READY`、`DATA_MISSING`、`ADAPTER_MISSING`、`PROVIDER_MISSING`、`ENGINE_MISSING`、`PLATFORM_DEPENDENT`、`DEGRADED`、`SCHEMA_ONLY`、`PLANNED`、`UNSUPPORTED`、`BLOCKED`。

## 2. 推导规则

1. `execution_status=READY` 时六个能力维度只能为 `AVAILABLE` 或 `READY`。
2. 任何 required capability 非 READY，整体状态不得为 READY。
3. 所有 required capability 均 READY 时，整体状态必须为 READY。
4. Tick 能力在第一版不得为 READY。
5. 空表、覆盖不足、字段缺失、频率缺失或输出目录不可写均产生 blocker 和 remediation。
6. `DEGRADED` 只描述已知降级能力，不允许绕过策略的 required requirement。

## 3. 数据门禁检查表

数据库可读、表存在、表非空、日期覆盖、股票池覆盖、关键字段、频率、复权列、PIT 字段、状态字段、Provider 路由、Engine Profile、PTrade Profile、输出目录可写。

## 4. 机器契约

Schema：`quantstudio/strategy_compiler/schemas/capability_report.schema.json`

示例：`quantstudio/strategy_compiler/examples/capability_report.example.json`

**能力报告必须提供证据、可读消息和修复建议，不能只给布尔值。**

## Target-aware ETF capabilities

| Capability | Dual target | QuantStudio-only |
|---|---:|---:|
| `get_etf_list()` in backtest | BLOCK | BLOCK |
| Customer-confirmed static ETF whitelist | REQUIRED for ETF strategies | OPTIONAL |
| `get_etf_list_local()` PIT universe | BLOCK | READY when `etf_basic` metadata exists |
| `get_history_batch()` | BLOCK | READY |
| PTrade validation / dual consistency | REQUIRED | NOT_APPLICABLE |

Local dynamic ETF readiness requires `etf_basic` classification/listing metadata and historical availability in `etf_daily`. Missing metadata is `DATA_BLOCKED`, not an implicit all-ETF fallback.

## Backtest execution ownership

| Capability | Agent-managed | User-PyQt |
|---|---:|---:|
| Agent starts R5 | Customer-confirmed window only | BLOCK |
| PyQt candidate after R4 | NOT_APPLICABLE | READY |
| Hash-bound user evidence | NOT_APPLICABLE | REQUIRED |
| Formal publish before R5 PASS | BLOCK | BLOCK |
| Candidate removal after R6 | NOT_APPLICABLE | REQUIRED |

## Framework repair capabilities (2026-07-27, F1-F6)

机器可检能力（capability report `status_detail` 令牌：`API_PROFILE_READY` /
`LOCAL_DATA_READY` / `LOCAL_RUNTIME_READY` / `PTRADE_STATIC_PROFILE_READY` /
`PTRADE_RUNTIME_UNVERIFIED` / `DATA_BLOCKED`）。API 注册 ≠ 数据可用；静态
profile PASS ≠ 真实 PTrade 运行验证；本地 ETF 元数据支持与 PTrade 真实 ETF
支持分开标记；不得把本地数据库覆盖描述为 PTrade 平台保证。

| Capability | Dual target | QuantStudio-only |
|---|---:|---:|
| `security_metadata_stock` (`get_stock_info` 股票，行为与历史一致) | READY（PTRADE_RUNTIME_UNVERIFIED） | READY |
| `security_metadata_etf` (`get_stock_info` ETF 上市/退市) | PTRADE_RUNTIME_UNVERIFIED | READY when `etf_basic` metadata exists |
| `index_constituents_pit` (`get_index_stocks(date)` 严格 as-of，完整性=snapshot_meta 批次契约) | PTRADE_RUNTIME_UNVERIFIED（平台成分历史深度按部署核实） | READY when complete snapshots + meta exist; 无快照/无 meta fail-closed 返回空 |
| `index_constituents_history_coverage` | 按部署核实 | READY（覆盖起点/终点写入能力报告；partial 快照不计完整 PIT） |
| `industry_classification_sw2021` (31 个 SW2021 L1) | PTRADE_RUNTIME_UNVERIFIED（平台分类版本按部署核实） | READY when `industry_classification` 就绪 |
| `industry_membership_pit` (`get_industry` 历史归属；官方契约无冲突裁决，重叠按原始事实保留，歧义日期 fail-closed) | PTRADE_RUNTIME_UNVERIFIED（本地 shape 为直接 `{'sw_l1': ...}`/None，PTrade 真实 shape 未验证） | 存在歧义区间 → **APPROXIMATION_REQUIRES_CONFIRMATION / DATA_BLOCKED**（不得宣称正式 PIT READY）；正式表缺失 → DATA_BLOCKED（绝不回退 legacy `sw_industry`） |
| `sw_l1_index_daily` (31 个行业指数日线，统一 `index_daily`) | BLOCK（PTrade 平台无申万指数日线保证） | READY when coverage 31/31 且 OHLC/金额门控通过 |
| `gui_rebalance_mode` (PyQt rebalance_mode 透出，默认 legacy) | NOT_APPLICABLE | READY |
| `callback_basket_pyqt` (PyQt 激活 `0.4.0-next_open_basket`) | NOT_APPLICABLE | READY 仅限 daily-bar-v1 + next_open；close/open 被 GUI 阻断；`run_daily` 永不进入 basket |

R1 判定规则：数据不存在 → `DATA_BLOCKED`；API 未注册 → `MISSING_REUSABLE_API`；
本地支持但 PTrade 真实能力未验证 → 不得写“PTrade 已验证”；依赖历史指数成分
但没有 PIT 覆盖 → BLOCK；依赖历史行业但只有当前快照 → BLOCK。
