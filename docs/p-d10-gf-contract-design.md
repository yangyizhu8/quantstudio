# P-D10 设计：get_fundamentals 转换契约对齐 + 注入 shim 契约机器级免疫机制

- 状态：**六步流水线 Step 1（方案）+ Step 2（审计）合并完成**；ZCode 终审✅有条件通过（2026-08-22，修订内容不需回审全案）；本文件为 Step 3 实施依据。
- 关联登记：P-D10（issue_registry v1.40，pending）
- 回退点：实施前建（占位：回退点 A = `git stash create -u -m "baseline-pd10-<ts>"`；回退点 B = 目标产物重转前原文件 `git hash-object`；hash 回填本文件与登记表 v1.40）
- 关联证据：`docs/evidence/p-d10-gf-contract-20260822.md`（实施/验收后落盘）
- 上次P-D9同款：P-D9 方案 v3（A/B/C/D/E 五条并入，2026-08-22 已闭环 b000d9e）

---

## 1. 问题定义与证据链

### 1.1 现象
PTrade 平台回测 `weekly_smallcap_growth_momentum_10`（转换产物 `output/ptrade_export/weekly_smallcap_growth_momentum_10/weekly_smallcap_growth_momentum_10_ptrade.py`）：

```
2026-07-01 08:30:00 - ERROR - 用户策略执行异常
AttributeError: 'dict' object has no attribute 'index'
```

首日 08:30（before_trading_start）即抛异常；产物 859 行 `val_df.index` 为故障点。

### 1.2 根因（证据链）
| 环节 | 事实 | 证据 |
|---|---|---|
| 本地 B1 契约 | `get_fundamentals_batch(...)` 返回**合并 DataFrame（index=ptrade_code, columns=fields）** | `quantstudio/backtest/ptrade_api.py:1421-1442`（docstring "返回 DataFrame，index=ptrade_code"）；本地源策略 `..._quantstudio.py:195-199` 按 `.index`/`['float_value']` 消费 |
| PTrade 注入 shim | 逐码循环后返回 **dict[code→DataFrame]** | `source_import.py:1942-1956` 模板 → 产物 663-674 行 |
| 契约错版 | shim 返回形状 ≠ 本地契约 → 产物 859 行炸 | 平台 traceback 与 `val_df.index` 逐字吻合 |
| 编译期盲区 | AST 层只查白名单/签名，不查返回形状 | — |

### 1.3 第二层隐患（修复第一层后必现）
产物 861-864 行**直接 list 调用**平台 `get_fundamentals(stage3, 'growth_ability'/'eps', fields=[...])`（未包装）：
- `_latest_by_code`（产物 799-817）强依赖 `df.index=code` + `end_date/publ_date` 列；平台 index 形态/字段名未验证；
- 若失配 → `stage3c` 空 → 每周 fail-soft 空选股（**不报错但净值全错**，比崩溃更险）；
- 平台对不可得字段抛 `KeyError ... not in index`（pd9 探针实证 circ_mv/total_mv）→ growth/eps 字段组合需实测。

### 1.4 目标
1. 崩溃消除：shim 返回形状与本地 B1 契约对齐（方案 B 双修路径）；
2. 第二层隐患消除：get_fundamentals wrapper（list→逐码循环、强制 index=code、字段映射、fail-open 分类）；
3. 机制免疫：注入 shim 契约从「C 组条文（人读）」升级为「注册表机器门禁 + 同构测试矩阵 + 运行时自检」三道防线（审计强制项）；
4. 性能必实测（审计必改项 P），性能不实测不实施。

## 2. 约束（用户拍板 + 审计终审）

| # | 约束 | 出处 |
|---|---|---|
| ① | 探针先行钉死三未知点（含单码/list 两种调用形态的 index 行为）+ 计时项 | 用户 + 审计 P 条 |
| ② | 登记 P-D10；「注入 shim 必须逐字段对照本地契约返回形状」入 C 组条文（**第四例**：P-D1 history → A 组订单 API → P-D9 filter → P-D10 fundamentals，与登记表 v1.40 一致） | 用户 + 审计小项③ |
| ③ | 验收 = 双端漏斗六级计数一致且各层非零 + 首日无异常 | 用户 |
| ④ | 证据文档落盘 | 用户 |
| P | 性能判据：50 码实测 → 外推单周 ≈15,000 码（valuation≈5,000+growth≈5,000+eps≈5,000）总耗时 ≤ 90s；超限走预案 ①→②→③ | 审计必改项 |
| 防线 | 三道防线全做（4.1 注册表 / 4.2 同构矩阵 / 4.3 运行时自检） | 审计 |
| 铁证 | 四条铁证链验收逐项出示（7.x），不靠声称 | 审计 |

## 3. 探针协议（实施第一步，先行交付）

脚本：`ptrade/probe_gf_contract_ptrade.py`（仿 `probe_pd9_filter_ptrade.py`；回测 2026-07-01~07-03，初始资金 100,000，基准沪深300；用户平台侧执行）。

| 项 | 内容 | 判据 |
|---|---|---|
| U1 调用形态 | 单码 vs list `get_fundamentals(PROBE_CODES, 'valuation', fields=['float_value'], date='20260630', is_dataframe=True)` | list 是否被平台原生接受；各返回 type/shape |
| U2 index 行为 | **单码与 list 两种形态分别记录**：返回 index 为代码/RangeIndex、是否有 code 列；growth/eps 多报告行形态（index 重复码 vs RangeIndex）；**end_date/publ_date dtype 与样例值** | 决定 wrapper 是否需要强制 index=code（基线预设需要） |
| U3 字段可得性与签名 | `growth_ability['or_yoy','publ_date','end_date']`、`eps['eps','publ_date','end_date']` 逐字段+组合；date 格式 `'20260630'` vs `'2026-06-30'` vs `None`；**`is_dataframe` kwarg True/False/缺省，TypeError/KeyError 分类记录**（审计小项②） | 字段映射表、date 归一、is_dataframe 去留 |
| P 计时 | 50 码逐码 `get_fundamentals('valuation', fields=['float_value'])` 单次耗时均值/最小/最大 → 外推 15,000 码单周总耗时 vs 90s 阈值 | 性能验收判据；超限走预案 §6 |

探针基线预设（审计批准）：单码可用 + 平台可能 RangeIndex + 逐码强制 index 的设计在**探针任何结果下成立**；探针只负责微调（如 U1 证实 list 原生可用 → wrapper 降为「index 强制 + 字段映射」单调用）。

### 3.1 探针执行结果与裁定（2026-08-22 测试123 回贴，**待复核**）

| 项 | 平台实测 | 裁定 |
|---|---|---|
| U1 | 单码/list2/list4 均 OK：**list 原生接受**，返回 DataFrame，**index=股票代码（PTrade 后缀）**，shape 与码数一致 | **预案①命中**：wrapper 主路径 = 平台原生 list 单调用（每周 3 次），不逐码循环 |
| U2 | 单码与 list 的 index 均为 code（000001.SZ/600000.SS/…）✓；eps 表 (1,4)/(4,4) idx=code，多码顺序与入参不同（按码排序，消费方 zip(index,values) 不依赖顺序）；**end_date/publ_date = object '2026-03-31' 字符串**；eps=float64；valuation float_value=object 大数 | **新炸点（第二层实锤）**：本地数值时间戳（duckdb fin_indicator）vs 平台 'YYYY-MM-DD' 字符串 → 策略 `_latest_by_code` `dtype=float` 强转 **ValueError** → wrapper 必须做 **end_date/publ_date 归一（'YYYY-MM-DD' → YYYYMMDD 数值，排序语义与本地 epoch 一致）** |
| U3 | **growth_ability 表无 or_yoy：KeyError "['or_yoy'] not in index"（平台吞错返回空 (0,0)）**；eps/valuation 的请求字段可得；**平台忽略 fields 列过滤**（请求 float_value 返回固定列集 trading_day/total_value/float_value；请求 publ_date/end_date 均返回 secu_abbr/publ_date/end_date）；date '20260630'/'2026-06-30'/None 三格式同值可用；is_dataframe True/False/缺省三形态同值可用 | **阻塞项**：L4 营收增长 or_yoy 平台数据不可得（处置见 §3.2）；wrapper 需按请求 fields 做列筛选（本地同构 available 逻辑）；date/is_dataframe 透传无风险（审计小项② closed） |
| P | 50 码逐码：total=1.773s mean=0.0355s → 外推 15,000 码/周=**531.9s > 90s OVER-BUDGET**（逐码路径） | 逐码路径超阈 → **预案①生效（list 原生已证）**：每周 3 次 list 调用，性能问题消除；list 单次时长纳入实施后平台冒烟实测（§7 铁证3） |

**设计微调清单（探针仅微调，审计已预授权）**：
1. wrapper 主路径改「平台原生 list 单调用 + 返回归一」；index 防御性校验保留（自检层 §5.3）；
2. 新增列筛选：平台返回固定列集 → 按请求 fields 选择（本地 `ptrade_api.py:763-768` available 同构）；
3. 新增 **end_date/publ_date 归一**：'YYYY-MM-DD' → YYYYMMDD 数值（数值排序契约与本地 epoch 一致；跨端无直接数值比较）；
4. 请求字段平台不可得（KeyError）→ 平台吞错返回空 DataFrame，wrapper 维持 fail-open 分类记录，空列自然被策略完整性过滤剔除；
5. `is_dataframe`/date 透传原值（三形态均安全）。

### 3.2 or_yoy 阻塞项（需用户/ZCode 裁定，不阻塞 wrapper 通用修复）

- 事实：平台 `growth_ability` 表 schema = [secu_abbr, publ_date, end_date]，**无 or_yoy**（KeyError 实证）；week10 策略 L4（营业收入增长降序前 10%）与 `周频小市值成长动量（三层止损）.py`、vol_regime 等策略均依赖 or_yoy。
- **裁定（2026-08-22 用户复核）**：选 **A. 补探针二枚举等价字段**（理由：降级 B/暂停 C 都应在确认平台确无等价字段之后；v1 只看到 3 列，是"请求返回"还是"全表 schema"尚未钉死，平台可能还有 income_statement/fin_indicator 类表）。
- **解耦原则（用户复核指令）**：框架通用修复与 week10 平台验收解耦——wrapper/shim/注册表/同构矩阵/自检层现在实施，验收用已有策略双端冒烟先行闭环；week10 平台复刻验收挂探针二结论：找到等价字段→映射接入（涉数据契约另走审计）；找不到→B/C 届时裁定。

### 3.3 探针二协议（脚本 `ptrade/probe_gf_contract_v2_ptrade.py`，GF2- 前缀，待用户平台执行）

| 块 | 内容 | 判据 |
|---|---|---|
| ① SCHEMA-<table> | 8 张候选表逐表 `fields=None` 全列返回：growth_ability / eps / valuation / income_statement / profit_ability / cashflow_statement / operating_ability / debt_paying_ability | 钉死各表真实 schema（是否还有增长口径列/其他营收数据） |
| ② FIELD-<tag>-<field> | growth_ability 上 or_yoy/np_yoy/equity_yoy/or_yoy/oper_rev_yoy/revenue_yoy/netprofit_yoy/yoy 逐字段；income_statement 上 operating_revenue/operating_cost（原始值→手动算营收同比证据）；profit_ability 上 roe/roa | 等价字段可得性 + 样例值/end_date（供与本地 fin_indicator 同码同期数值语义比对） |
| ③ LIST-TIMING-100/500 | 100 码与 500 码 valuation list 单调用计时 + **必查项②**：返回唯一 code 数 vs 请求数 | 外推 5000 码单次 list 周成本；截断/批量上限检测 → 决定 wrapper 是否需分片（>上限则改 chunk 分片循环） |

- **必查项①已并入设计微调清单第④条具体化**（wrapper 把"请求字段缺失→空 df"识别为 `QS_SHIM_FIELD_MISSING` 显性失败 + 计数，不当正常空处理，2026-08-22 用户复核指令）。

### 3.4 探针二执行结果与裁定（2026-08-23 测试123 回贴）

| 块 | 平台实测 | 裁定 |
|---|---|---|
| ① SCHEMA 枚举 | **growth_ability 全 21 列**（fields=None）：含 `operating_revenue_grow_rate`（营收增长）、`net_profit_grow_rate`（净利增长）、`np_parent_company_yoy`、`np_parent_company_cut_yoy`、`total_profit_grow_rate`、`net_asset_grow_rate`、`basic_eps_yoy`、`sustainable_grow_rate` 等增长类列 + end_date/publ_date/secu_abbr；eps 24 列（eps/basic_eps/diluted_eps/eps_ttm/…）；valuation 20 列；income_statement 59 列（`operating_revenue`/`operating_cost` 原始值可得）；profit_ability 43 列（roe/roa/roic/…）；其余表齐全 | v1「schema 仅 3 列」结论修正：**全表 schema 远大于请求返回**；平台 fields = 请求列保留 + 附赠标识列（secu_abbr/end_date/publ_date 或 trading_day/total_value），非纯忽略——wrapper 列筛选仍正确 |
| ② 字段可得性 | growth_ability 上 `or_yoy/np_yoy/equity_yoy/oper_rev_yoy/revenue_yoy/netprofit_yoy/yoy` **全部 KeyError**（平台吞错返回空 (0,0)）——本地 fin_indicator 字段名在平台 growth 表全不可得；**平台自有命名等价字段 ∈ schema**：`operating_revenue_grow_rate`（↔ or_yoy）、`net_profit_grow_rate`/`np_parent_company_yoy`（↔ np_yoy）；income_statement.operating_revenue 可得（手动跨期算营收同比备选） | **or_yoy 等价字段 = `operating_revenue_grow_rate`**（字段名直译强对应）；**数值口径对照待探针三**（GF3-） |
| ③ LIST 计时/截断 | POOL=5205；**100 码 0.052s rows=uniq=100 FULL；500 码 0.047s rows=uniq=500 FULL** | **必查项②通过：平台 list 无截断** → wrapper 无需分片；每周 3 次（~5000 码）按线性估算 <0.5s/次，**性能判据 PASS**（vs 90s 阈值） |

数值对照基准（本地 `data/quantstudio.db` fin_indicator，单位=% 数值，ann_date 与平台 publ_date 毫秒一致）：000001.SZ 2026-03-31 or_yoy=4.6516/np_yoy=3.0292/eps=0.67（publ 2026-04-25）；600000.SS 2026-03-31 or_yoy=1.4176/np_yoy=1.4945；300255.SZ 2025-12-31 or_yoy=-15.1025（2025-09-30 -13.1119）；688496.SS 2025-12-31 or_yoy=-11.1582（2025-09-30 -13.6446）。

映射接入判据：探针三（`ptrade/probe_gf_contract_v3_ptrade.py`，GF3-）平台 `operating_revenue_grow_rate` ≈ 本地 or_yoy（同百分点单位、差 ≤0.5pct、符号一致）→ 补映射 `or_yoy → operating_revenue_grow_rate`、`np_yoy → net_profit_grow_rate`（§4.1 预授权「探针发现字段名差异则补条目」）；数值分歧 → 口径分歧，映射存疑另审。

### 3.5 探针三执行结果与映射接入（2026-08-23 测试123 回贴）

| code | 报告期 | 本地 or_yoy（fin_indicator） | 平台 operating_revenue_grow_rate | Δ | 判定 |
|---|---|---|---|---|---|
| 000001.SZ | 2026-03-31 | 4.6516 | **4.6516** | 0.0000 | ✅ 精确一致 |
| 600000.SS | 2026-03-31 | 1.4176 | **1.4176** | 0.0000 | ✅ 精确一致 |
| 300255.SZ | 2026-03-31 | 本地缺 2026Q1（本地 lag，外部缺口） | -1.0047 | n/a | 不阻塞（两精确点 + 平台 schema 实证） |
| 688496.SS | 2026-03-31 | 同上 | -18.5683 | n/a | 同上 |

- **or_yoy 映射成立并已接入**：`or_yoy → operating_revenue_grow_rate`（唯一映射；np_yoy **不映射**——600000 `net_profit_grow_rate=2.2165` vs 本地 `np_yoy=1.4945` 差 0.72pct 口径差，且无策略消费 np_yoy）。请求字段翻译 + 返回列名逆翻译（`df.rename(columns=_QS_GF_FIELD_MAP_REV)`，仅 growth_ability），本地契约/策略代码零改动。
- **eps 口径差观察（记录不阻塞）**：平台 eps 表 `eps` 000001=0.75 / 600000=0.54 vs 本地 0.67 / 0.52（数值差 +12%/+3.8%）；week10 L6 仅判 `eps>0` 符号 → 排序不受影响；属平台/本地每股收益口径基差异（加权 vs 期末），登记为已知口径差，不涉 L4。
- 产物已重转（hash `770461bf…`）→ 六级对齐冒烟待平台执行（预期 L3v/L4/L5/L6/R 全非零）。

## 4. 双修路径设计（架构核心，审计批准）

### 4.1 get_fundamentals wrapper（新增注入模板 `_QS_FUNDAMENTALS_EXT`，已实施）
- 对齐本地契约四要素（type=DataFrame / index=code / columns=fields / 空行为=空 DataFrame 不抛错，出处 `ptrade_api.py:1421-1442`、`1438-1439`）；
- **主路径（探针 U1 → 预案①）**：平台原生 list 单调用（3 次/周），返回 index=code 原样保留；list 调用失败 → 防御路径逐码循环 concat 重建（`get_fundamentals_batch` shim 委托同一 wrapper → 返回合并 DataFrame）；
- **index 防御校验**：非代码形态（RangeIndex 等）→ code 列/行序重建（探针实证平台返回 code index，属防御路径）；判定 `'.' in s or (s.isdigit() and len(s) >= 5)`（回避 RangeIndex 整数误判）；
- **列筛选（探针 U3）**：平台返回固定列集 → 按请求 fields 选择（本地 `ptrade_api.py:763-768` available 同构）；请求字段不在返回列集 → `QS_SHIM_FIELD_MISSING` 显性警报 + 返回空 DataFrame(columns=fields)（必查项①，不静默）；
- **end_date/publ_date 归一（探针 U2 第二炸点）**：'YYYY-MM-DD' object → YYYYMMDD 数值（与本地 fin_indicator 数值排序语义一致；跨端无直接数值比较）；
- **本地→平台字段名映射（已接入，2026-08-23 探针三数值实证）**：映射表 `_QS_GF_FIELD_MAP = {'or_yoy': 'operating_revenue_grow_rate'}`（本地名 → 平台名）——请求阶段把本地字段名翻译为平台名传给 get_fundamentals；返回后列名逆翻译回本地名（`_qs_frame_to_contract` 内 `df.rename(columns=_QS_GF_FIELD_MAP_REV)`，仅 growth_ability），策略按本地列名消费 `df['or_yoy']`。探针三对照：000001/600000 @2026-03-31 平台值 == 本地 or_yoy（4.6516/1.4176，Δ=0.0000）；np_yoy 不映射（600000 口径差 0.72pct，无策略消费）；映射只翻译列名，不改动本地契约与策略代码；平台无等价字段的本地名（映射表缺条目）维持 `QS_SHIM_FIELD_MISSING` 显性失败；
- 缺字段 fail-open：平台吞错返回空 → wrapper 分类记录（`GF-FAILOPEN` / `QS_SHIM_FIELD_MISSING`）后返回空 DataFrame（与本地 `except: return result` fail-open 同语义）；
- 多报告行排序**不**由 wrapper 负责：`_latest_by_code` 自身按 (end_date, publ_date) 排序去重（与本地同构，不越权）；
- 模板铁律（既有三条）：class 属性承载原始 ref（`_QSFundState.orig`）/ 平台真实 API def 前捕获 / 非平台 API 顶层禁引；函数内 `_qs_pd`/`_qs_np` 局部 import。

### 4.2 get_fundamentals_batch shim 改写（已实施）
- `_shim_source("get_fundamentals_batch")`：**委托 `get_fundamentals` wrapper**（原生 list 单调用）→ 返回合并 DataFrame（index=code, columns=fields），docstring 已同步「与本地 B1 契约一致」；不再 dict 拼装、不再逐码 `index=[code]`（探针 U1 证实 list 原生可用，逐码路径仅作 wrapper 内防御退化）；
- `get_history_batch` shim 不动（dict 契约正确，仅补 `_qs_shape_check` 首调自检埋点）；
- 门控：`_source_uses_fundamentals(source)`（AST 调用名匹配 get_fundamentals / get_fundamentals_batch，模式同 `_source_uses_filter_status`）；`_inject_all` 挂接，注入顺序 wrapper 在 shim 之前；
- 既有 wrapper/shim 首调自检埋点（§5.3）已回补：get_history（两模板）、get_history_batch、filter_stock_by_status、get_trade_days、get_stock_info。

### 4.3 其余
- `portability_rules.py`：DENY_SHIM 注释形状描述修正（dict→DataFrame）；集合不变；
- `ptrade_api.py`：**零行为改动**（本地契约已满足；与 P-D9「仅注释行」同纪律，仅当探针发现本地契约与预期不符才评估）；
- `validators/validate_ptrade_portability.py`：注册表门禁（见 §5.1）。

## 5. 第二部分·三道防线（审计强制项）

### 5.1 SHIM_CONTRACT_REGISTRY（机器门禁，已实施）
- 落 `portability_rules.py`（与 DENYLIST 单一来源同址）：`SHIM_CONTRACT_REGISTRY: dict[str, ShimContractSpec]`（frozen dataclass）；
- 字段：`api_name` / `contract_type, contract_index, contract_columns, contract_empty`（四要素）/ `contract_source`（ptrade_api.py 行号）/ `template_location`（source_import 常量名）/ `homology_test`（同构测试名）；
- 首批登记（7 项）：get_history wrapper / get_history_batch / get_fundamentals_batch / filter_stock_by_status wrapper / get_fundamentals / get_trade_days / get_stock_info；`INJECTED_WRAPPER_NAMES` 与 `DENY_SHIM` 并集 == 注册表键集合（测试断言集合相等，防双边漂移）；
- validator：产物中出现的注入 def（`INJECTED_WRAPPER_NAMES ∪ DENY_SHIM`）不在注册表 → **BLOCK**（`PORTABILITY-UNREGISTERED-SHIM`）；
- 契约对照从 C 组条文（人读）升级为机器门禁——防第四例。

### 5.2 全量同构测试矩阵（已实施）
- `test_p10_registry_contract_complete`（注册表完整性 + 四要素非空）+ `test_p10_registry_homology_matrix`（逐条：模板存在于 source_import + 同名同构测试存在于测试文件）+ `test_p10_registry_gate_blocks_unregistered`（门禁 BLOCK 路径实证）；
- 既有 wrapper/shim 逐条契约四要素断言（get_history / get_history_batch / filter_stock_by_status / get_fundamentals / get_fundamentals_batch）随 P-D9/方向B 既有用例持续覆盖，新增 P-D10 用例补齐 fundamentals 双路径（list 原生 / 逐码退化 / 列筛选 / 日期归一 / FIELD_MISSING / 自检）。

### 5.3 运行时首调形状自检（已实施）
- 共享 helper `_qs_shape_check(name, expected, actual)` 落 `_PTRADE_HELPERS`（所有产物注入）：API 首次调用后断言返回形态，不符输出 `QS_SHIM_SHAPE_VIOLATION` 显性警报（log 不抛错不阻断）；expected ∈ dataframe/dict/list/df_or_dict；
- 首调用点已挂接：get_history（两模板）、get_history_batch、get_fundamentals、get_fundamentals_batch、filter_stock_by_status、get_trade_days、get_stock_info；矩阵测试断言埋点存在且触发路径正确（test_p10_shape_check_violation_alarm）。

## 6. 性能判据 P（必改项，性能不实测不实施）

- 判据：探针 P 计时实际测值 → 单周 15,000 码外推总耗时 ≤ 90s；
- 超阈值预案（按优先级）：
  1. **U1 平台原生 list**：wrapper 降为「index 强制 + 字段映射」单调用（3 次/周，性能问题消失）；
  2. **漏斗内廉价预筛收窄**（严格不改变语义为前提：如 L5 收窄后 30 码先做 eps，须先证明与现调用顺序等价——性能铁律禁止为性能改语义）；
  3. **登记性能残余差异 + 用户重估**（继承 P-D9 降级登记纪律）。

## 7. 验收标准（四条铁证链 + 明细，逐个出示不靠声称）

- **铁证 1 本地零漂移**：改动全落转换编译器；目标策略本地回测与基线逐字段一致（单测 + golden 对比，净值逐字段同）。
- **铁证 2 门控保未重转产物逐字节不变**：全库已转换产物扫描——除本方案显式重转的目标产物外，其余产物（含使用 get_fundamentals 未重转者）**diff 为零**显式确认。
- **铁证 3 行为变化仅限平台端「崩溃/静默空池 → 正确执行」**：
  - 双端漏斗六级计数（L3v_complete / L4_growth / L5_smallcap / L6_eps / R_rankable / R_selected）一致且**各层非零**（用户约束③）；
  - 首日无异常（08:30 不再 ERROR）；
  - growth_map / eps_map 非空（第二层隐患消除）；
  - fail-open 警报计数 = 0 或逐条归因（审计小项①）；
  - 性能外推 ≤ 阈值（§6）；
  - 用户平台冒烟回贴通过（真实平台首日跑通 + 漏斗审计行回贴）。
- **铁证 4 可逆性**：撤销注入块即回退；**双回退点 in 案**（A：实施前 `git stash create -u -m "baseline-pd10-<ts>"`；B：目标产物重转前原文件 `git hash-object`）；hash 登记于本设计与登记表 v1.40。
- 明细项：单测全绿（`test_ptrade_contract_compliance.py` + `test_source_import.py`，含新增约 10-12 用例 + 同构矩阵，零回归）；转换产物 py_compile 通过；`validate_ptrade_portability` ok（含新注册表门禁）；白名单风险 ZERO；注入 helper 齐全。

## 8. 回退条件

- 回退 = 撤销 source_import / portability_rules / validator 改动 + 重转产物恢复原文件（回退点 B hash 校验）+ `git reset --hard <hash A>` 兜底；
- 写前快照纪律：实施前 `git status --porcelain` + `git diff --stat` 核对目标文件无其他会话未提交改动；批量/破坏性 git 操作仅在建回退点之后。

## 9. 文档同步（双仓推送清单，缺一不推）

README.md（PTrade 平台差异吸收矩阵增补：⑤ get_fundamentals_batch 返回形状契约、⑥ get_fundamentals list 调用 wrapper、⑦ 注入 shim 契约注册表门禁）、docs/strategy_toolbox.md、docs/prompt_engineering.md、docs/interface-contract.md（P-D10 吸收表行）、docs/strategy-compiler/ptrade-profile-contract.md（C 组条文/注册表）、docs/strategy-compiler/implementation-status.md、docs/handoff/ 收官 skeleton、docs/evidence/p-d10-gf-contract-20260822.md。
推送后核对两远程 HEAD 一致（github.com/yangyizhu8/quantstudio-plus / quantstudio）；hunk 剥离纪律照旧（他人未提交改动不改不推）。

## 10. 实施记录（Step 3，2026-08-22 完成）

1. ✅ 回退点 A = `02b86e91a7f11178a5b4ef55370573413ae24cd9`（`git stash create -u -m "baseline-pd10-*"`）；回退点 B = 目标产物原文件 hash `2a61b320737ef461839a996fb1b0b9aef5d5d173`；污染核对：目标文件零他人未提交改动（仅 ptrade/ 下 untracked 探针）；
2. ✅ `source_import.py`：`_QS_FUNDAMENTALS_EXT`（wrapper 原生 list 单调用 + 列筛选 + date 归一 + `_qs_shape_check` 埋点 + fail-open 分类日志）、`_shim_source("get_fundamentals_batch")` 委托 wrapper、`_source_uses_fundamentals` 门控、`_inject_all` 挂接（wrapper 在 shim 之前）、`_qs_shape_check` 落 `_PTRADE_HELPERS`、存量 wrapper/shim 首调自检回补；
3. ✅ `portability_rules.py`：DENY_SHIM 注释修正 + `SHIM_CONTRACT_REGISTRY`（7 项）+ `INJECTED_WRAPPER_NAMES`；
4. ✅ `validators/validate_ptrade_portability.py`：未登记注入 def → BLOCK（PORTABILITY-UNREGISTERED-SHIM）；
5. ✅ 测试：`test_ptrade_contract_compliance.py` 新增 16 用例（P-D10 全套 + 注册表 + 同构矩阵 + 门禁）；`_wrapper_ns`/_wrapper_ext_ns/_filter_status_ns 注入真实 `_qs_shape_check`；131 passed（两文件）；
6. ✅ 重转目标产物 `output/ptrade_export/weekly_smallcap_growth_momentum_10/weekly_smallcap_growth_momentum_10_ptrade.py`（errors=0 warnings=0，新 hash 见登记表）+ py_compile OK + `validate_ptrade_portability` ok + 产物注入点 grep 核验（_QSFundState/_qs_norm_fund_dates/QS_SHIM_FIELD_MISSING/_qs_shape_check 全就位）；
7. ⏳ 待探针二回贴 → 全量测试套件 → 双端冒烟 → 登记表推进 + C 组条文 + 证据文档 → 用户确认 → 双仓推送。

## 11. 风险与假设

- 假设平台 `get_fundamentals` 单码签名 `(security, table, fields=, date=, is_dataframe=)` 可用（pd9 探针实证，U1 复核）；
- wrapper 在探针任何结果下成立（基线预设已审计批准）；探针仅微调实现细节；
- U3 若 growth/eps 字段不可得（KeyError）→ 字段映射或显式登记残余差异（继承 P-D9 降级登记纪律），**不得静默空选股**；
- 性能外推超阈值 → 预案 ①→②→③，未实测不实施；预案 ② 严禁改变语义（性能铁律）。

## 12. 变更记录

- 2026-08-22 v1：方案初稿（六步 Step 1 产物）。
- 2026-08-22 v2：ZCode 终审意见并入——P 性能必改项（探针计时 + 90s 阈值 + 预案 ①②③）、三道防线（5.1 注册表 / 5.2 同构矩阵 / 5.3 运行时自检）、四条铁证链（§7）、三条小项（fail-open 计数入证据 / is_dataframe 分类记录 / C 组第四例口径）；终审裁定：修订不需回审全案，实施前探针结论交审一次。此版即实施依据。
- 2026-08-22 v3：探针一回贴结论并入（§3.1 裁定：预案①命中 date 归一 QS_SHIM_FIELD_MISSING 必查项①）+ §3.2 用户复核裁定（or_yoy 选 A、解耦原则）+ §3.3 探针二协议（GF2-，含必查项②截断检测）+ §10 实施记录（回退点 A/B hash、16 新用例、产品重转核验）。
- 2026-08-23 v4：探针二回贴结论（§3.4：schema 全表枚举 / growth 表本地字段名全 KeyError / 等价字段 operating_revenue_grow_rate / list 无截断性能 PASS）+ 探针三数值对照（§3.5：两精确点 Δ=0.0000 → or_yoy 映射接入，np_yoy/eps 口径差记录）+ 测试 134 passed（+3 映射用例）+ 产物重转 hash 770461bf、validator/编译通过。

---

**一句话**：修复按方案 B 执行；性能必实测；三道防线 + 四条铁证链逐项验收通过、用户确认后才可双仓推送。