# xtquant 前复权精度特性验证报告（C 方案前置只读验证）— 修正版 v2

> **性质**：只读验证报告。不改引擎、不改生产配置、不写正式库。
> **目的**：检验 `add_dev ≤ k×D` 能否同时做到低误报+低漏检，作为 C 方案硬门禁的可行性判定。
> **判断标准**：真实数据全通过 ≠ 成功；真实数据能通过同时污染数据能被稳定挡住才是。
> **日期**：2026-07-29
> **可复现**：`python scripts/validate_qfq_rebase_precision.py`（默认离线复算冻结 fixture；`--refresh` 重新采集）。
> **证据**：`docs/evidence/qfq_rebase_precision_validation_20260729/`（受版本管理，含 manifest/summary/fault_sensitivity CSV + 冻结 fixture SHA）。

## 0. 结论

**`add_dev ≤ k×D` 不应作为 C 方案的硬门禁。** 拒绝的最强证据：
1. **故障敏感性全漏检**：k×D（k=0.06）对 close_front 1~20 tick 污染全部漏检（§4，`fault_sensitivity.csv`）。
2. **同步偏移结构性盲区**：整段 front 同步偏移无法被 add_dev 类规则检测（§4.2）。
3. **缺乏损坏区分能力**：即使取 k 足够大覆盖所有真实样本，该边界也过宽，无法区分正常精度差异与数据损坏。

**正确表述**（v1 修正）：不存在**兼具足够紧度和数据损坏区分能力的实用统一 k**。
- 注：v1 的"k≥0.145 可覆盖所有样本"基于 max(add_dev)/max(D) 的错误口径（详见 §3 修正）。
- 即使采用正确的逐行口径 row_ratio_max（§3），取其最大值覆盖全样本，故障注入仍全漏检。

**C 方案信任边界**（不声称检测源端 front 污染）：
fresh_authoritative_rebase 将经过来源认证和内容冻结的 xtquant front 作为**权威输入（oracle）**。
引擎验证：源标识、数据完整性、目标 raw 对齐、全量覆盖、字段守恒、原子写入、写后精确匹配。
**框架不独立证明 oracle 自身的复权语义正确性，也不检测 fresh capture 阶段形成的同步 front 污染。**

## 1. 样本清单与可复现证据

8 只证券（完整数据见 `summary.csv`，每证券 fresh_sha256 冻结在 manifest）：

| 证券 | 类型 | 描述 | 行数 | D 范围 | add_dev max | row_ratio max |
|------|------|------|------|--------|-------------|---------------|
| 000012 | STOCK | 低价多次分红 | 2808 | [0, 6.84] | 0.660 | 0.109 |
| 600000 | STOCK | 银行多次分红 | 2808 | [0, 10.06] | 0.472 | 0.053 |
| 600875 | STOCK | 分红 | 2808 | [0, 2.62] | **0.000** | 0 |
| 600039 | STOCK | 分红 | 2808 | [0, 6.60] | 0.351 | 0.063 |
| 002864 | STOCK | 送转/混合 | 2108 | [0, 19.11] | **2.760** | 0.181 |
| 600519 | STOCK | 高价股 | 2808 | [0, 327.88] | 2.267 | 0.007 |
| 510300 | ETF | 沪市 ETF | 2808 | [0, 0.80] | **0.000** | 0 |
| 159919 | ETF | 深市 ETF | 2808 | [0, 0.96] | 0.048 | 0.056 |

> row_ratio = max_i(add_dev_i / |D_i|)，逐行口径（非 v1 的 max(add_dev)/max(D)）。
> D≈0 且 add_dev>0 的行数：全样本为 0（近期无除权数据 D≈0 时 add_dev 也=0）。

- 数据源：xtquant miniQMT（国金 QMT 模拟，sp3），2015-01-01 ~ 2026-07-26。
- 重复下载稳定性：`--refresh` 两次下载 SHA 完全一致（manifest: repeat_download_identical）。
  **限定**：仅证明重复可获得相同数据，不证明数据本身经济正确。

## 2. 多变量分析

### 2.1 精确定义（见脚本 `add_dev_analysis`）
- `D_close(t) = close(t) - close_front(t)`（代数恒等式）。
- `add_dev_X(t) = |(X - X_front) - D_close|`，X=open/high/low。
- `intraday_range(t) = (high - low) / close`。

### 2.2 相关性（逐证券，见 `summary.csv` corr_* 列）
add_dev 与日内振幅相关性最强（002864 达 0.781），与 |D| 中等相关。非 D 单变量函数。

### 2.3 候选假设 H1（降级，不构成门禁）
部分证券（600875/510300）精确成立（add_dev=0），部分（000012/002864）不成立。
未完成跨证券/跨事件/留出样本验证，不构成生产门禁。

## 3. 训练集与留出集 + 逐行 ratio 口径修正

**v1 错误**：用 max(add_dev)/max(D) 估算 k（002864: 2.76/19.11=0.145）。
**正确口径**：硬门禁 `add_dev_i ≤ k×|D_i|` 需逐行 `ratio_i = add_dev_i/|D_i|`，取 max_i。
脚本现已生成 row_ratio_max/p99（见 `summary.csv`）：002864 row_ratio_max=0.181（非 0.145）。

取全样本 row_ratio_max 最大值（0.181）覆盖所有真实样本，但故障注入仍全漏检（§4）。
留出集验证前提不成立（add_dev 非单变量函数 + 故障无区分能力）。

## 4. add_dev 规则故障敏感性分析

> **明确**：本节是对 **add_dev 规则的敏感性分析**，不是对未来 coverage/raw-match/staged-match
> 确定性门禁的实测。真正的确定性门禁故障注入在引擎实现后由 pytest 完成（结构/对齐/写入三类）。
> 完整数据见 `fault_sensitivity.csv`。

### 4.1 信任域分类

| 故障域 | 示例 | 预期检测方 | 确定性条件覆盖 |
|--------|------|-----------|---------------|
| 结构/覆盖 | 缺行/多行/重复/错误时间 | coverage precheck | ✅（实现后 pytest） |
| 对齐 | 错证券/错日期/raw 错位 | raw match precheck | ✅（实现后 pytest） |
| 事务写入 | UPDATE 后 front 被改 | staged-match postcheck | ✅（实现后 pytest） |
| **源端语义** | staged fresh front 同步偏移 | **确定性条件无法检测** | ❌ 需独立 oracle |

### 4.2 close_front 单点污染（000012，k×D 阈值=0.265）

| 污染 tick | add_dev | k×D 检测 | tick(0.01)检测 |
|-----------|---------|---------|---------------|
| 1~20 | 0.02~0.13 | **全漏检** | 大部分检测 |

k×D 对 1~20 tick 全漏检。详见 `fault_sensitivity.csv`。

### 4.3 整段同步偏移（结构性盲区）
整段四 front 同步偏移：open/close 减法偏移同步变化，add_dev 理论上不变。
属源端语义故障，确定性条件不检测。

## 5. 规则对比（总体误报率由脚本自动计算）

| 规则 | 总体误报率 | 1tick漏检 | 20tick漏检 | 源端语义盲区 |
|------|-----------|-----------|------------|-------------|
| 固定 tick(0.01) | **20.95%**¹ | 0% | 0% | ❌ |
| k×D(k=0.06) | 0% | **100%** | **100%** | ❌ |

¹ 由脚本自动计算（manifest `overall_stats`）：
- total_observations=65292，total_over_tick=13676
- weighted_false_positive_pct=**20.95%**（加权总体）
- unweighted_security_mean_pct=**23.14%**（逐证券简单平均）
- 逐证券拆分见 `summary.csv` add_dev_over_0_01_pct（000012=25.3%, 002864=91.3%, 600875=0%）
- **v1 的 8.4% 计算错误，已删除。**

## 6. raw 来源与对齐预检（C 方案可行性前置门）

### 6.1 来源标签预检（完成）
stock_daily/etf_daily/stock_minutes/etf_minutes 的 data_source **100% xtquant**（manifest `raw_source`）。

### 6.2 raw 内容对齐预检（**仅完成 2 只证券 daily 抽样，完整准入预检待完成**）
tracked 证据：`raw_preflight_manifest.json`（`--preflight-raw` 生成，validation_status=PASS）。
- 000012/510300 daily：fully_aligned=True，四列 OHLC（open/high/low/close）mismatch 全 0，
  时间集合完全一致（target==fresh==matched，无缺失/重复）。
- **未完成**：全市场（5202 股票+1605 ETF）raw 对齐、minute raw 对齐、验收证券（000025/000060/600875/600039/002864 等）daily+minute 全量预检。
- **降级表述**：raw 来源标签全表预检完成；raw 内容对齐仅完成 2 只证券 daily 抽样，
  完整准入预检（含验收证券 daily+minute）尚未完成，须在引擎实现前完成。

## 7. 推荐结论
1. add_dev ≤ k×D 不作硬门禁（故障全漏检 + 盲区 + 无区分能力），仅作观察/审计/告警。
2. C 方案信任边界：xtquant front 权威 oracle，框架保证采集/传输/对齐/覆盖/守恒/原子/写后一致；不检测源端语义。
3. 确定性门禁：raw 逐 bar 一致 + 全覆盖 + 守恒 + 写后 front==staged + 原子回滚 + 幂等（含 capture 不可变性）。
4. precheck 移除理想化乘法/加法假设，不替换为 k×D。
5. 因子链分段检查移入未来研究（§8 spec），不列入实现。

## 附录：可复现性
- 脚本：`scripts/validate_qfq_rebase_precision.py`（默认离线，`--refresh` 采集，`--verify-hashes` 校验）
- 冻结 fixture：`tests/fixtures/qfq_rebase_precision/fresh_daily/*.csv.gz`（8 证券 daily）
- 证据：`docs/evidence/qfq_rebase_precision_validation_20260729/`（manifest/summary/fault_sensitivity CSV）
- fail-closed：脚本任一关键步骤失败返回非零（validation_status=FAIL + blocking_errors）
