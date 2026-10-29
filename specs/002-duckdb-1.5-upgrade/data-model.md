# Data Model：002-duckdb-1.5-upgrade

**Date**: 2026-10-29 | **说明**：本功能不改数据库表结构（无 DDL），此处建模的是**判定/约束/观测**三类实体，供实现与测试共用同一词表。

## 1. VersionPolicy（放行集合，唯一真相）

| 字段 | 类型 | 约束 |
|---|---|---|
| `ranges` | list[Range] | 至少一项；每项 `(lo_tuple, lo_inclusive, hi_tuple, hi_inclusive)` 或 `(point_tuple)` |
| `parse_rule` | 规则 | 取前三段整数；段数 <3、非数字、空串 ⇒ 解析失败 |
| `on_parse_error` | 枚举 | 恒为 `deny`（fail-closed） |

**取值（本期）**：`[ (1,4,5) ≤ x < (1,5) ] ∪ [ x == (1,5,6) ]`

**不变量**：①集合语义只在此处定义；②任何其他落点（声明/脚本/测试）只能引用，不得复述；③新增放行点必须改此常量（单点变更）。

## 2. VersionDecision（判定结果）

| 字段 | 类型 | 说明 |
|---|---|---|
| `allowed` | bool | 是否放行 |
| `version` | str | 原始版本串（未解析前） |
| `parsed` | tuple[int,int,int] \| None | 解析结果；None 表示解析失败 |
| `reason` | str | `in_range` / `out_of_range` / `unparsable` / `import_failed` |

**状态迁移**：`import_failed → allowed=False`（拒）；`unparsable → allowed=False`；`in_range → allowed=True`；`out_of_range → allowed=False`。**不存在「未知即放行」路径**。

## 3. GateOutcome（启动门禁结果）

| 字段 | 类型 | 说明 |
|---|---|---|
| `component` | str | `GUI (main_gui.py)` / `daemon (quantstudio.pipeline.daemon)` / 子进程形态标识 |
| `exit_code` | int | 放行 = 继续启动（无退出码）；拒绝 = **3** |
| `message` | str | 含当前版本、要求集合、三类出路（换解释器 / 降版 / 显式放行） |
| `escape_hatch` | bool | 是否因显式放行开关而放行（此时必带告警日志） |

## 4. PassthroughTask（配置实体，只读消费）

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | str | 任务名（lint 报错须点名） |
| `table` | str | 目标表；lint 用于与主键表清单求交 |
| `passthrough` | bool | 为真即走全量覆盖链 |
| `enabled` | bool | 现状 88 任务中 67 个 passthrough（53 启用 / 14 禁用） |

**校验规则（新增，ERROR 级）**：`passthrough == True` 且 `table ∈ PKTables` ⇒ 配置非法（前瞻守卫）。

## 5. TableConstraint（约束观测）

| 字段 | 类型 | 说明 |
|---|---|---|
| `table` | str | 表名 |
| `pk_columns` | list[str] \| [] | 来自 `duckdb_constraints()`，空表示无主键 |
| `has_pk` | bool | 派生 |

**关键不变量（本期修复目标）**：覆盖链执行前后 `has_pk` 不变（1→1）；现状缺陷为 1→0 且无告警。

## 6. PKBaseline（巡检基线，已先行）

| 字段 | 说明 |
|---|---|
| `db` / `generated_at` | 基线来源库与生成时间 |
| `pk_tables` | 有主键的表集合（基线：101 表 / 41 有 PK，2026-10-28 23:34:36） |

**告警判据**：基线中 `n ≥ 1` 而当前 `n == 0` ⇒ ALERT（退出码 2）；`0 → ≥1` ⇒ WARN；表消失 ⇒ WARN。

## 7. RiskItem（A-14 观测实体）

| 字段 | 说明 |
|---|---|
| `path` | 唯一落在本引擎上的批量写路径（快照元数据刷新） |
| `rows` / `median_old` / `median_new` / `ratio` | 三档规模的实测中位与倍数 |
| `verdict` | `PASS` / `FAIL`（判据：≤2× 且 ≤5s） |
| `disposition` | `risk_only`（本期：登记 + 通知）/ `fix_tracked`（独立立项） |

**本期取值**：300 行 0.642s→3.943s（6.14×，FAIL）；1000 行 3.314s→13.396s（4.04×，FAIL）；5000 行 15.436s→**>90s 超时**（≥5.83×，FAIL）⇒ `disposition = risk_only`。

## 8. 窗口状态机（执行期，非持久化）

```
正常 ──[声明占用 + 停 daemon + 关 GUI]──▶ 停服
停服 ──[装 1.5.6 + 冒烟 S1–S6]──▶ 验证中
验证中 ──[全 PASS]──▶ 收尾（schtasks /Run + E7 基线）
验证中 ──[任一 FAIL]──▶ 回滚（降版 + 旧版打开 + 门禁复核）
收尾/回滚 ──[读数落证]──▶ 正常
```

**禁止迁移**：`停服 → 收尾`（跳冒烟）、`验证中 → 正常`（跳收尾或回滚）。
