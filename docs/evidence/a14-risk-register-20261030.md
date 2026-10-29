# 风险登记 RSK-DUCKDB-001：duckdb 1.5.x `executemany` 批量写时延劣化（A-14）

- **登记日期**：2026-10-30｜**关联**：spec 002-duckdb-1.5-upgrade（FR-026 / SC-006 风险接受口径）、plan R-3、KB `DB-2026-0001`
- **判型（铁律 18）**：**新增检测型** —— 新增观测能力与风险登记，**不改变任何既有可观察结果**（不动写入路径、不改表结构、不改 PK）

## 一、风险描述

升级到 duckdb 1.5.6 后，**批量写入路径**中走 `executemany` 的落点耗时较 1.4.5 **劣化 4–6 倍**；`INSERT INTO … SELECT`（DataFrame 零拷贝注册路径）不受影响。

## 二、读数（来源：`docs/evidence/a14-executemany-scale-20261029.md`，1.4.5 / 1.5.6 各三次取中位）

| 单批行数 | 1.4.5 中位 | 1.5.6 中位 | 倍数 | 原判据（≤2× 且 ≤5s） |
|---|---|---|---|---|
| 300 | 0.642 s | 3.943 s | **6.14×** | FAIL（倍数超） |
| 1000 | 3.314 s | 13.396 s | **4.04×** | FAIL（倍数与绝对值均超） |
| 5000 | 15.44 s | **>90 s（超时未完成）** | — | FAIL（超时） |

## 三、登记要素

| 字段 | 内容 |
|---|---|
| **风险 ID** | `RSK-DUCKDB-001` |
| **等级** | **中**（影响**时延**，不影响数据正确性；辅助表刷新路径，非主链） |
| **触发条件** | duckdb ≥ 1.5.0 **且** 该路径仍走 `executemany` **且** 单批规模达千行量级 |
| **影响面** | `index_constituents_snapshot_meta`（PK `(index_code, time)`）刷新时延 —— 本项目**唯一** `executemany` 落点：`quantstudio/pipeline/index_constituents_meta.py:125`；**不影响**主表写入、不影响 PIT/复权/取数口径 |
| **数据正确性** | **不受影响**（实测仅耗时变化；无内容差异、无 FATAL） |
| **本期处置口径** | ①**登记**（本件）②**客户通知量化提示**（`docs/handoff/customer-notice-5-duckdb-1.5.6-upgrade-20261029.md` §六 + §七 FAQ）③客户侧建议放宽该任务调度超时或拆批 ④**修复单独立项**（见 `docs/handoff/duckdb-executemany-write-path-refactor-20261030.md`），不在本期改码 |
| **解除条件** | 改写入形态立项完成，且实测三个批次量级均满足「倍数 ≤2× 且绝对值 ≤5s」+ 同数据集逐位一致（列集/行数/sha256） |
| **复审触发** | 升级到 1.5.7+（若上游修复该劣化）／`duckdb#23645` 关闭或新补丁发布／该版本线 EOL 日历到点（见 `docs/duckdb-version-eol-calendar.md`） |
| **责任方** | 主仓维护线（写入形态改动）；客户侧（超时放宽/拆批） |

## 四、局限（如实申报）

- 读数为**本机**实测，单机单次会话的三次中位，**非全客户规模分布**；
- 5000 行档在 1.5.6 上**超时未完成**（未取到完成时间，只有下界 >90s）⇒ 倍数列标「—」而非估算值；
- 劣化机理与 `duckdb#23645`（ART index-delete）**同族但未逐行归因**（该缺陷在 1.5.6 未修复，且**本项目暴露面为 0**：非唯一 ART 索引 = 0，41 个主键三臂验证不丢）—— ⇒ 本风险登记的是**时延**影响，**不是**数据损坏风险，两者不得混谈。

## 五、关联证据

| 用途 | 路径 |
|---|---|
| A-14 计量原始读数 | `docs/evidence/a14-executemany-scale-20261029.md` |
| 缺陷暴露面核验 | `docs/evidence/duckdb-version-gate-compat-audit-20261029.md` |
| 客户通知（量化提示） | `docs/handoff/customer-notice-5-duckdb-1.5.6-upgrade-20261029.md` |
| 知识库条目 | `knowledge/db/DB-2026-0001.md` |
| 修复立项 | `docs/handoff/duckdb-executemany-write-path-refactor-20261030.md` |
