# Hotfix #4 · `can_incremental` 判据收窄 + 真机首次导入归档

- 实施：2026-11-01 04:20（delivery 会话）
- 触发：用户真机首次完整导入成功（exit 0），但复验报 `can_incremental=False`——
  该包四态为 **MISSING=0 / AHEAD=0 / BEHIND=5 / UNKNOWN-BASIS=5**，触发源是"存在需关注项"。

## 1 缺陷与判据修正

**旧判据**：`can_incremental = not defect_tables(...)`，而 `defect_tables` 收 `MISSING/BEHIND/AHEAD`
⇒ 只要 **BEHIND > 0**（正常待补拉）就判 `False`。

**新判据（用户裁定 2026-11-01）**：只有**有害态**驱动，`HARMFUL_STATES = {MISSING, AHEAD}`

| 态 | 是否影响 can_incremental | 理由 |
|---|---|---|
| `AHEAD` | **是（False）** | 水位领先数据 ⇒ 可能重复/错位 |
| `MISSING` | **是（False）** | 有数据无水位 ⇒ 增量退化为全历史重拉 |
| `BEHIND` | 否 | 只是"落后待补拉"，增量本来就会补 |
| `UNKNOWN-BASIS` | 否 | 配置债（缺基准列声明），不判四态、不该让所有客户永远看到 False |
| `EMPTY` / `NO-WATERMARK-SYSTEM` / `UNKNOWN-SOURCE` / `OK` | 否 | 信息性/排除性标签 |

**新增**：
- `stats.HARMFUL_STATES`、`stats.attention_tables()`（BEHIND/UNKNOWN-BASIS/MISSING/AHEAD 全列，仅供展示）；
- `stats.reason_not_incremental()`：不可增量时给出**只含有害态**的原因（AHEAD n 张/missing n 张 + 前 5 个表名）；
- `import` 复验 `summary` 改为：
  - 可增量：`71 表：可增量（需关注 10 张，不影响增量）`
  - 不可增量：`71 表：不可增量 —— AHEAD 2 张（水位领先数据…）：['x','y']`

## 2 测试（165 passed / 0 failed / 2:45，新增 4 例）

| 用例 | 判据 |
|---|---|
| `test_can_incremental_true_with_unknown_basis_and_behind` | **BEHIND + UNKNOWN-BASIS 存在 ⇒ True**，且进 attention、`reason` 为空 |
| `test_can_incremental_false_on_ahead` | **AHEAD>0 ⇒ False**，reason 含 `AHEAD` |
| `test_can_incremental_false_on_missing` | MISSING>0 ⇒ False，reason 含 `MISSING` |
| `test_can_incremental_true_when_only_exclusionary_states` | 仅排除性标签 ⇒ True |

## 3 真机首次完整导入（性能基线，用户实测归档）

| 项 | 读数 |
|---|---|
| 场景 | 客户机 `import --import-dir 交付包目录\20261101_002051`（69.30 GiB） |
| 结果 | **exit 0**，九步全走通（校验→持锁→备份→覆盖→复验→释放） |
| 总耗时 | **约 5 分钟**（约 13.9 GiB/min ≈ 237 MB/s 端到端） |
| 备份方式 | 同卷 rename（不额外占空间） |
| 复验四态 | MISSING=0 / AHEAD=0 / BEHIND=5 / UNKNOWN-BASIS=5 / OK=11 / NO-WATERMARK-SYSTEM=50 |

> 与 W1 导出基线（69.30 GiB / 237.7 s）对齐：**导入 ≈ 导出同量级**（都是单遍顺序读写）。

## 4 纯增益

SC-013：**PASS**（36 保护路径；唯一变化 `writers.py` 判 `OTHER_LINE`，非本会话）
根因入册：`knowledge/db/DB-2026-0015.md`（判据把"正常待补拉"与"真有害"混为一谈）
