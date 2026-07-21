# xtquant 日线权威源切换运维手册

**状态**：待执行（用户凌晨手动操作）
**日期**：2026-07-21
**背景**：stock_daily / etf_daily 权威源从 tushare 切到 xtquant 单源锁定。本文档定义切换的强制操作序列、回滚点、样本比对、Fidelity 门禁验收。

---

## ⚠️ 这是高风险操作

stock_daily / etf_daily 是 **Fidelity 双门禁的直接输入**（ETF 动量 + 小市值）。复权基准跨源差异约 0.01%（实测 600000：tushare ratio=0.95489，xtquant=0.9548），换算到 ETF 门禁 ±1 元容差（87752 × 0.01% ≈ 8.8 元）——**只要策略信号或引擎路径消费 front 列，门禁必漂**。

因此切换**不能只查字段完整性**，必须是**强制验收程序**：备份 → 样本比对 → 全量清空重拉 → Fidelity 门禁。

---

## 强制时序：一个原子操作序列

**禁止在"配置切换"和"清空重拉"之间留窗口**。原因：配置切 xtquant 单源 + DAILY_AUTHORITY 守卫立即生效，但清空重拉若分两次做，中间窗口期会出现：
- 今天 17:00 daemon 用 xtquant 往**还有 tushare 存量的表**里 UPSERT → front/back 列混源
- 或 miniQMT 没开 → 日线停更，但 tushare 写入被 DAILY_AUTHORITY 守卫拒绝

**正确顺序**（凌晨一次性执行，避免 daemon 17:00 干扰）：

```
[1] 停 daemon（确保 17:00 不会触发）
[2] 备份 quantstudio.db（回滚点）
[3] 样本比对（拉 50 只 × 1 月，验证原始 OHLCV 跨源一致）
[4] 全表 DELETE stock_daily / etf_daily（不分源，全清）
[5] 启动 PyQt，手动触发 stock_daily + etf_daily 全量拉取（过夜挂机）
[6] 重拉完成后，跑 Fidelity 门禁
[7] 门禁通过 → 切换完成；门禁漂移 → 按黄金基线变更协议停下报告
```

---

## 第 1 步：停 daemon

```bash
# 确保 ResidentCollector 已停止（PyQt 关闭或 daemon 进程 kill）
tasklist | findstr python   # 确认无 daemon 残留
```

---

## 第 2 步：备份（回滚点）

```bash
# 复制整个 quantstudio.db（最简单可靠的回滚点）
cp D:/miniQMT策略实盘/_runtime/data/quantstudio.db \
   D:/miniQMT策略实盘/_runtime/data/quantstudio.db.pre-xtquant-cutover-20260721

# 或用 DuckDB EXPORT（更轻量，仅两张表）
# python -c "
# import duckdb
# conn = duckdb.connect('D:/miniQMT策略实盘/_runtime/data/quantstudio.db')
# conn.execute(\"EXPORT TABLE stock_daily TO '/tmp/stock_daily_pre_cutover.parquet'\")
# conn.execute(\"EXPORT TABLE etf_daily TO '/tmp/etf_daily_pre_cutover.parquet'\")
# conn.close()
# "
```

**验证备份**：确认备份文件大小合理（stock_daily 9.5M 行 ≈ 1-2GB）。

---

## 第 3 步：样本比对（核心假设验证）

**在清空前**，先拉 50 只 × 1 个月 xtquant 数据到临时表，与 tushare 存量逐行 diff 原始 OHLCV/preClose。验证"原始列跨源一致"这个核心假设。

```python
# scripts/verify_xtquant_daily_sample.py（执行后可删除）
import duckdb
import pandas as pd
from xtquant import xtdata

DB = "D:/miniQMT策略实盘/_runtime/data/quantstudio.db"
SAMPLE_CODES = ["600000.SH", "000001.SZ", "002024.SZ", "300750.SZ", "688981.SH"]  # 扩到 50 只
START, END = "20260601", "20260630"

conn = duckdb.connect(DB)
mismatches = []
for code in SAMPLE_CODES:
    # tushare 存量
    ts = conn.execute(f"""
        SELECT time, open, high, low, close, volume, amount, preClose
        FROM stock_daily WHERE code='{code.split('.')[0]}'
          AND data_source='tushare'
          AND time BETWEEN {pd.Timestamp(START).value//10**6} AND {pd.Timestamp(END).value//10**6}
        ORDER BY time
    """).fetchdf()
    # xtquant 拉取（原始 none）
    xtdata.download_history_data(code, '1d', START, END)
    xt = xtdata.get_market_data_ex(stock_list=[code], period='1d',
                                    start_time=START, end_time=END, dividend_type="none")[code].reset_index()
    # 逐行比对（volume 需 ×100 对齐）
    for _, row in ts.iterrows():
        day = pd.Timestamp(row['time'], unit='ms').strftime('%Y%m%d')
        xt_row = xt[xt['time'].astype(str).str.startswith(day)]
        if len(xt_row) == 0:
            continue
        xt_r = xt_row.iloc[0]
        # close/open/high/low 应一致（元）；volume tushare=股 xtquant=手（×100）；amount 应一致
        for col_ts, col_xt, factor in [
            ("open","open",1), ("high","high",1), ("low","low",1), ("close","close",1),
            ("volume","volume",100),  # xtquant 手 → ×100 对齐 tushare 股
            ("amount","amount",1), ("preClose","preClose",1)
        ]:
            ts_val = row[col_ts]
            xt_val = xt_r[col_xt] * factor
            if pd.notna(ts_val) and pd.notna(xt_val) and abs(ts_val - xt_val) > 0.01:
                mismatches.append({
                    "code": code, "day": day, "field": col_ts,
                    "tushare": ts_val, "xtquant": xt_val, "diff": ts_val - xt_val
                })

print(f"样本比对：{len(SAMPLE_CODES)} 只 × {len(ts)} 日")
print(f"原始列差异：{len(mismatches)} 处")
if mismatches:
    df = pd.DataFrame(mismatches)
    print(df.groupby("field").agg(n=("diff","count"), mean_diff=("diff","mean"), max_diff=("diff","max")))
    # 若原始 OHLCV 差异显著（>0.5%），停下报告，不要清空
    assert len(mismatches) < len(SAMPLE_CODES) * 5, "原始列跨源差异过大，停止切换"
conn.close()
print("样本比对通过：原始 OHLCV/preClose 跨源一致，可继续清空重拉")
```

**停止条件**：若原始 OHLCV/preClose 差异 > 0.5% 或 sample 失败，**停下报告**，不要清空。差异可能来自除权日定义不同（tushare ex_date vs xtquant divid date 差 1 天）—— 这种情况需在 aligner 层用 stock_dividend 表对齐后再切换。

---

## 第 4 步：全表清空（不分源）

```sql
-- 在 PyQt 或 duckdb CLI 执行
DELETE FROM stock_daily;          -- 清空全部（含 tushare 存量 9.5M 行）
DELETE FROM etf_daily;            -- 清空全部（含 NULL 老数据 1.99M + tushare 2405 行）
-- watermark 表也要清掉对应行（让 xtquant 从 start_date 全量起步）
DELETE FROM source_watermark WHERE table_name IN ('stock_daily', 'etf_daily');
```

**验证**：`SELECT COUNT(*) FROM stock_daily` 应为 0。

---

## 第 5 步：PyQt 全量拉取（过夜挂机）

启动 `python main_gui.py`，在采集任务界面手动触发：
- `kline_1d`（stock_daily）：mode=full_range，start_date=2018-01-01
- `etf_daily`：mode=full_range，start_date=2018-01-01

**预期耗时**：xtquant 全市场 per_stock 模式，5000+ 股 × 3 次 get（none/front/back），max_workers=8 并行。预估数小时（受 miniQMT 本地缓存深度限制）。过夜挂机。

**监控点**（日志）：
- `进度: N/5202 (x%)` 持续推进（不应卡 0%）
- `database is locked` 偶现可接受（DuckDB 写锁竞争，单只重试可恢复）
- 不应出现 `IsSTNull` 整表拒（etf_daily adapter 已补 isST=0）
- 不应出现 `UnitCheck` 批量拒（volume ×100 已配）

---

## 第 6 步：Fidelity 门禁强制验收

重拉完成后，**必须**运行 Fidelity 门禁：

```bash
python scripts/run_strategy_fidelity_gates.py
```

**门禁定义**（`docs/strategy-compiler/zcode-handoff-20260721.md` §11 不可变质量门禁）：
1. ETF 动量 3 笔成交黄金序列
2. ETF 最终资金 `87,752.56 ± 1`
3. 小市值 CLOSE 防退化 envelope
4. 每阶段都必须运行真实 Fidelity gate

---

## 第 7 步：结果判定

### 门禁通过（PASS / CLOSE）
切换完成。记录到 `docs/strategy-compiler/implementation-status.md`。

### 门禁漂移（FAIL）
**禁止用"新源数值不同"为由直接更新黄金值**。必须按黄金基线变更协议停下报告（§11）：

```text
变更原因：xtquant 切源导致 front 列复权基准重算
旧/新结果对比：[门禁输出]
真实 PTrade 重新导出或复核依据：[填]
差异归因：[复权基准 / 除权日定义 / 精度差异 / 其他]
用户明确批准记录：[等待用户批准]
```

**五项缺一不可**，批准前回滚到备份（第 2 步）。

---

## 回滚程序（门禁漂移时）

```bash
# 用第 2 步的备份恢复
cp D:/miniQMT策略实盘/_runtime/data/quantstudio.db.pre-xtquant-cutover-20260721 \
   D:/miniQMT策略实盘/_runtime/data/quantstudio.db

# 回滚配置（git）
git checkout config/collector_tasks.json quantstudio/gui/tabs/config_editor_tab.py
```

回滚后 stock_daily/etf_daily 恢复 tushare 权威源 + 原数据。

---

## 切换后常态验证（次日）

1. **字段完整性**：`SELECT COUNT(*), COUNT(peTTM), COUNT(pbMRQ), COUNT(turn), COUNT(isST), COUNT(pctChg) FROM stock_daily WHERE data_source='xtquant'` —— 各字段非空率应合理（peTTM 亏损股 NULL 正常，其余应高覆盖）
2. **复权一致性**：抽 1 只票看 close_front 序列无异常跳变（除权日正常跳变除外）
3. **daemon 17:00 增量**：次日观察 daemon 是否正常拉取 xtquant 增量（无 IsSTNull/UnitCheck 拒绝）
4. **数据适配层**：跑一次回测，确认 `duckdb_data_access.py` 能正常加载行情

---

## 关键决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 存量处理 | 全清重拉 | 单源一致，避免复权基准混源台阶 |
| 缺失字段 | aligner PIT JOIN valuation 补 peTTM/pbMRQ/turn；adapter 补 isST | 保持数据适配层不歧义，用户要求前置依赖 |
| 回退策略 | xtquant 单源锁定（DAILY_AUTHORITY） | 防回退混源，复权一致性优先于可用性 |
| 切换时序 | 原子操作（停daemon→备份→样本比对→清空→重拉→门禁） | 避免窗口期混源 |
| 黄金基线 | 重拉后强制门禁，漂移走变更协议 | ±1 元容差敏感，禁止静默更新黄金值 |

## 相关代码引用

- 配置：`config/collector_tasks.json`（kline_1d/etf_daily）、`config_editor_tab.py:43-61`（DEFAULT_SOURCE_MAP）
- 守卫：`daemon.py` DAILY_AUTHORITY（对称 MINUTE_AUTHORITY）
- adapter 补 isST：`xtquant_adapter.py` fetch_table
- aligner 补估值：`aligner.py` `_derive_valuation_fields` + `daemon.py` `_prepare_valuation_df`
- column_map：`alignment_rules.json` xtquant stock_daily/etf_daily（preClose/suspendFlag 映射）
- 门禁：`scripts/run_strategy_fidelity_gates.py`、`config/strategy_fidelity_gates.json`
- 黄金基线协议：`docs/strategy-compiler/zcode-handoff-20260721.md` §11
