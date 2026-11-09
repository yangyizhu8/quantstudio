# settle（丙方案·历史冻结）实施闸门

> 建立：2026-11-10 03:55 ｜ 会话节点⑳
> **本文件的存在就是提醒**——任何人（含我方）在任何时点准备动手 settle 前，必须先读完本文档并逐条勾验前置条件。
> 关联：`docs/rebase-history-exempt-design-20261109.md`（方案件 v2）、审核方 2026-11-10 裁定（丙+乙组合）

---

## 一、settle 是什么（一句话）

把「早于各 code 源端覆盖边界」的历史区间标记为 **frozen_before**，使 rebase 的 `missing_target` 只统计 `time >= frozen_before` 的行——**解开「判据要求 100% 完美 vs 历史数据必然不完美」的死锁**。

---

## 二、三个前置条件（**全部满足才可动手**，缺一即停）

| # | 条件 | 如何验证 | 当前状态 |
|---|---|---|---|
| **P1** | **06:00 轮双指标已读** | 读 `daemon.log` 的 `放行率` 与 `OHLC mismatch` 计数 | ⏸ 未跑（约 06:00）|
| **P2** | **settle 方案已复核通过** | 总调度或 ZCode 的明确书面批准（引其原话+时点） | ⏸ 仅提纲 |
| **P3** | **用户/总调度批准执行** | 明确指令含「执行/实施/照准」字样 | ⏸ 未获批 |

**⇒ 审核方原话（2026-11-10）**：「settle 是不可逆操作，须加一道外部复核门」「建议 06:00 轮后休息，把 settle 方案留给第二天写」。

---

## 三、触发方式（怎么让它不被遗忘）

| 层 | 机制 | 状态 |
|---|---|---|
| **文件层（本文件）** | 任何人动手前必须读本文档 | ✅ 已建立 |
| **Git 层** | 本文件入库后出现在 `git status` / `docs/` 检索 | ⏸ 待 commit |
| **会话层** | 我方每轮回执在「未完成项」中引用本文件 | ✅ 已在本轮回执申报 |
| **用户层** | **用户/总调度在 06:00 轮后提醒一次** | 建议采纳（双保险）|

**⇒ 我不建议只依赖「我记得」**：本次会话已连续 12+ 小时，上下文随时可能压缩；且 settle 不可逆，靠记忆触发风险过高。

---

## 四、执行清单（P1-P3 全满足后，按序执行）

### 步骤 1：边界数据固化（**只读**，可先做）

```sql
-- 取每 code 的源端首日（已实测可跑，55.8 秒全量）
SELECT ts_code, min(trade_time) AS first_ts
FROM stock_minutes GROUP BY ts_code;
SELECT ts_code, min(trade_date) AS first_date
FROM stock_daily GROUP BY ts_code;
-- etf_minutes / etf_daily 同理
```

**产物**：`docs/evidence/frozen-boundary-<date>.json`（每 code 一行的 `frozen_before_ms`）

**规模实测**：`stock_minutes` 5,286 codes / `stock_daily` 5,857 codes

### 步骤 2：新增 settle 台账（**DDL，属契约变更 ⇒ 走铁律 24 六件套**）

```sql
CREATE TABLE qfq_settle_log (
    settle_id      VARCHAR PRIMARY KEY,
    asset_type     VARCHAR NOT NULL,      -- STOCK / ETF
    code           VARCHAR NOT NULL,
    frozen_before  BIGINT NOT NULL,       -- epoch ms
    settled_at     TIMESTAMP NOT NULL,
    reason         VARCHAR,               -- 'source_coverage_boundary'
    operator       VARCHAR,
    batch_id       VARCHAR
);
```

**⚠ 铁律 24 提醒**：若 `qfq_settle_log` 被纳入 schema 指纹校验，需同步「契约期望列集 + SCHEMA_VERSION + detect_schema_status + writers 安全闸 + 迁移 runner + 测试」六件套。

**替代方案（更轻）**：不建表，改用独立 JSON 文件 `data/qfq_frozen_bounds.json`（不触发 schema 契约）。**建议先用 JSON，稳定后再入表。**

### 步骤 3：改 rebase 判据（**框架层改动 ⇒ 走铁律 01 六步流水线**）

改动点：`qfq_reanchor_engine.py` 的 `_check_minute_cov_raw` / 日线覆盖检查，在计算 `missing_target` 时排除 `time < frozen_before` 的行。

**必须保留**：
- `fresh ⊃ target` 方向的既有容忍（`allow_partial`）
- 「拿不到 `frozen_before` ⇒ 不豁免」（fail-closed）
- 豁免命中必写 WARNING（可审计）

### 步骤 4：验证（审核方指定）

**冻结前后各跑一次标准回测（同策略同窗口），产物哈希逐位比对。**

```bash
# 冻结前
python -m quantstudio.backtest.run --strategy <X> --start <S> --end <E> > before.json
# 冻结后（同一命令）
python -m quantstudio.backtest.run --strategy <X> --start <S> --end <E> > after.json
# 比对
python scripts/compare_backtest_hash.py before.json after.json
```

**通过条件**：产物哈希**逐位一致**（冻结只影响采集侧校验，不应影响回测结果）。

### 步骤 5：观察

- 下一轮 06:00 的 `放行率` 应 > 0%
- `held` 数应下降
- 水位应推进

---

## 五、回退方式

| 层 | 方式 |
|---|---|
| **环境开关** | `QS_QFQ_FROZEN_BEFORE=0`（默认开启）⇒ 一键退回严格模式 |
| **数据层** | `qfq_settle_log` / `qfq_frozen_bounds.json` 可整表/整文件删除，不影响原始数据 |
| **判据层** | git revert 对应 commit |

**⇒ 原始 K 线数据**永不删除、永不修改**，settle 只改「校验口径」。**

---

## 六、本轮（2026-11-10）不做的事（明确边界）

| 不做 | 理由 |
|---|---|
| 不建 `qfq_settle_log` 表 | 待 P1-P3 满足；先用 JSON |
| 不改 `qfq_reanchor_engine.py` | 未经②审 + ⑤确认 |
| 不生成边界 JSON | 边界数据依赖 P1 的双指标（若回填有害，边界策略要重定）|
| 不写 settle 方案全文 | 遵审核方「留给第二天」 |

---

## 七、变更记录

| 时间 | 变更 |
|---|---|
| 2026-11-10 03:55 | 建立本闸门文件（P1-P3 三条件 + 五步执行清单 + 回退方式）|
