# 治理方案实施第 1 步交付物 — 取数调用链梳理 / 快照清单 / 基线消费范围清单

- 状态：完成（2026-08-17，实地核对代码，非文档转述）
- 依据：`docs/project-stabilization-plan.md` §6 实施顺序第 1 步
- 方法：Grep/Read 逐文件核对回测取数真实调用链（性能铁律第 1 条），所有结论附文件:行号

---

## 1. 回测取数调用链（实地核对）

```
策略文件（strategies/*.py，经 ptrade_api 注入 API）
  → run_ptrade_strategy.py / strategy_runner.py
    → EngineConfig.db_path（默认 = _paths.db_path()，run_ptrade_strategy.py:20,44-46,79-81）
      → _paths.py:53-55：DATA_ROOT / "quantstudio.db"
        ← DATA_ROOT 解析链（_paths.py:30-50）：环境变量 QUANTSTUDIO_DATA_ROOT
          > config/data_config.json 的 path 字段（相对路径锚定项目根）> 默认 data/
  → DuckDBProvider / DuckDBDataAccess（providers/duckdb_data_access.py:85-134）
    → 单一只读连接：duckdb.connect(quantstudio.db, read_only=True)（L134）
```

**关键结论**：
- 回测路径的数据库访问**单点收口**于 `DuckDBDataAccess` 一处只读连接（`_get_conn`），入口路径 `db_path` 已由 `config/data_config.json` 集中解析——**快照替换挂点唯一且干净**（改 data_config.json 的 path 或传 db_path 参数即可切到快照物理副本，零代码改动）。
- **qfq_aux.db 不在回测消费链上**（重要，修正审计 B4 的预设）：前复权以 `*_front` 物化列形式存在主库（duckdb_data_access.py:395-397 `close_front AS close`），qfq_aux 仅是管线侧生成/暂存库（引用全部位于 `pipeline/`）。因此**回测可复现性快照的最小集不含 qfq_aux**；但建议仍纳入快照（治理溯源用途：管线重跑的输入状态），标注为"非回测必需"。

## 2. 快照清单（最终）

| # | 文件 | 必要性 | 说明 |
|---|---|---|---|
| 1 | `data/quantstudio.db` | **回测必需** | 全部行情/参考数据（14.3GB 级，主库） |
| 2 | `config/data_config.json` | **回测必需** | db 路径解析输入（_paths.py:37-44） |
| 3 | `data/qfq_aux.db` | 溯源推荐（非回测必需） | 复权因子管线生成态；回测读主库 `*_front` 列 |
| 4 | `config/profiles/mcp_only/*.json` | 观察 | 未发现回测路径读取（Grep providers/backtest 无命中）；若实施第 2 步实测发现引擎读取某配置，追加进清单 |

逻辑内容 hash：对上表库文件按 concat_sha256 方案（导出关键表聚合后拼接 hash，先例 `mcp_protocol_probe.md`）；"逐字节一致"验收仅适用于回测产物。

## 3. 回测消费表全集（主库；v3 修正：18 张实体表）

> **v3 修正（2026-08-17，DSH 终审阻塞项闭合）**：初版列 20 张中 `valuation_pit` / `latest_share` 经代码行级证据证实为 **SQL CTE**（duckdb_data_access.py:1292 / :1239，分别派生自 stock_daily_valuation / stock_float_share，二者均在集合内），主库 Catalog 无此表；`stock_minutes`/`etf_minutes` 为动态表名（L622-635）。三集合（消费全集 ∩ sort_keys.json ∩ 快照 hash 覆盖）一致性机器验证相等（18 张），报告：`output/golden_baseline/table_set_consistency_report.json`。

stock_daily（含 `*_front` 前复权列）、etf_daily、stock_minutes、etf_minutes、etf_basic、etf_dividend、stock_dividend、stock_basic、stock_float_share、stock_daily_valuation、fin_indicator、index_daily、index_constituents、index_constituents_snapshot_meta、industry_classification、industry_membership、sw_industry、strategy_events。
（`m` 为 SQL 内部别名非表；`information_schema` 系统表不计。quarantine.db 不在回测链上。）

## 4. 基线消费范围清单（三策略 × API → 表映射，供两级预筛求交）

| 策略 | 注入 API（框架提供，ptrade_api.py） | 策略内自定义函数（非注入） | 映射到表 |
|---|---|---|---|
| etf_theme_rotation_quantstudio.py | get_etf_list_local ×2、get_history_batch ×1、set_benchmark('000300')、get_value | — | etf_basic、etf_daily(+front)、etf_dividend（池/公司行为）、index_daily（基准） |
| 小市值策略ptrade.py | get_fundamentals ×2、get_index_stocks ×2、get_positions ×2、set_benchmark("000300.XSHG")、get_value、**filter_stock_by_status（L44）**、**check_limit（L81）** | get_trade_stocks ×2（L76，策略内自定义，非注入 API） | stock_daily_valuation / stock_float_share、index_constituents(+snapshot_meta)、stock_daily、index_daily（valuation_pit/latest_share 为其派生 CTE，非表） |
| smallcap_overnight_scalp_7_quantstudio.py（minute-bar-v1） | get_history ×2、get_fundamentals ×1、get_stock_info ×1、get_stock_status ×3、get_trade_days ×1、get_position ×4、get_open_orders ×1、get_order ×1、set_benchmark("000300.SS") | — | stock_minutes（引擎路径）、stock_daily、stock_daily_valuation、stock_basic、index_daily |

> **修正记录（2026-08-17，审计修正项 A）**：初版将 `get_trade_stocks` 误列为注入 API（实为策略内自定义函数），漏列 `filter_stock_by_status` / `check_limit`。已修正；**表级映射不变、以其为准**（两级预筛按表级映射求交）。

**S1 覆盖核验（拍板附条件②，结论：无缺口）**：三策略合集覆盖 S1 清单全部类别——股票日线✅ ETF 日线✅ 分钟表✅ 复权因子✅（主库 front 列，随 stock_daily/etf_daily 覆盖）公司行为✅（etf_dividend + stock_dividend）ETF 池✅（etf_basic）**基准✅（三策略均 set_benchmark 000300 → index_daily）**。无需第四策略。

**核验条件①（小市值策略ptrade.py 本地可跑性）**：其调用的 get_fundamentals/get_index_stocks 等均为本地注入 API（ptrade_api），非 PTrade 独有，初步判定本地可跑；**最终确认推迟到实施第 4 步基线产出时实测**（跑不出确定性结果才触发顶替）。

## 5. 交付给后续步骤的输入

- → 第 2 步（D2/D3 门槛）：门槛检查脚本按 §3 表全集 + §4 消费范围设计；分钟策略候选实测二选一在此步定。
- → 第 3 步（快照机制）：快照清单 = §2 表；快照切换挂点 = `config/data_config.json` path 字段（零代码改动路径）或 `db_path` 参数。
- → 两级预筛：快照 hash diff 的"变化表集合"与 §3/§4 求交判定。

## 6. 审计记录项（登记排期，不阻塞后续步骤）

| # | 记录项 | 排期 |
|---|---|---|
| B | `qfq_invariant.py:480` 黄金行自检硬编码 `DATA_ROOT/"quantstudio.db"`（只读）——真实主库上无碍，快照副本上必须参数化 `main_db_path` | 第 3 步（快照机制）前完成 |
| C | `events.py:107`（import_strategy_events）read-write 直连写入口（DELETE+INSERT strategy_events）——纳入唯一写入会话治理 | 第 5 步（读写隔离） |
| D | `_paths.py:50` DATA_ROOT 模块加载即解析——data_config.json 切换仅对新进程生效，驻留进程（daemon/GUI）需重启或走 db_path 参数 | 第 3 步切换设计需明确此策略 |
| E | D2 行数对账需 QuestDB 云端源侧可达 | 第 2 步前置确认 |

