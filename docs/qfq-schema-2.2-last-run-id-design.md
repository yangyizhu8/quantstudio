# QFQ schema 2.1 → 2.2：`source_watermark.last_run_id`（FP-5 重立项·呈批件）

- 日期：2026-10-21
- 上游：总调度 2026-10-21 裁定「FP-5 重新立项照准（契约 2.1→2.2 + 期望列集 + 版本常量 + 迁移 runner + 测试同 commit）」
- 判型：**纯恢复型 + schema 契约变更**（不是元数据操作）
- 状态：待 ②审计 → 待 ⑤终审（含本轮事故复盘）

## 0. 知识反哺检索留痕

```
知识反哺：[命中] 已知错误-GUI启动卡死-WAL生命周期（12-客户运维/已知错误/已知错误-GUI启动卡死-WAL生命周期.md，2026-10-21）→ 采纳要点：共享库结构类改动必须"先探测现状、再动手"，探测优先于阈值
知识反哺：[无命中] 12-客户运维/已知错误（schema 契约版本升级 / 加列即契约变更主题），2026-10-21
```

## 1. 问题定义

**需求**：`source_watermark.last_batch_id` 被两条语义共用——采集批次号 与 运行标识（run_id）。
`daemon.py:699` 生成 `manual_<task>_<uuid12>` 的 run_id，经 gate 提交写入 `last_batch_id`，
导致：① 批次审计链在该类行上断（batch_audit 无此 ID）；② 被误读为"人工 pin"（firefly 报告 §4.1 由此误判）。
需独立列 `last_run_id` 承载 run 语义。

**为什么加不了（2026-10-20 实弹）**：`source_watermark` 结构由
`qfq_schema_contracts.py` 的 `TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT`（8 列）与
`TARGET_MAIN_DB_2_1_FINGERPRINT` 定义，`detect_schema_status()` 用
`verify_fingerprint(..., reject_extra=True)` **逐字比对**——**多一列即判 `PARTIAL_OR_MIXED`**，
`DuckDBWriter._init_tables` 安全闸随即拒绝启动（实测：ALTER 后 partial；DROP 后 complete_2_1）。

**结论**：`last_run_id` 属 **schema 2.2 契约变更**，必须契约/版本/迁移/测试同 commit。

## 2. 改动范围（6 件，全部框架层）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `quantstudio/pipeline/qfq_schema_contracts.py` | 新增 `SOURCE_WATERMARK_2_2_DDL`（8 列 + `last_run_id VARCHAR` 可空）；新增 `TARGET_SOURCE_WATERMARK_2_2_FINGERPRINT`、`TARGET_MAIN_DB_2_2_FINGERPRINT`；**保留** 2.1 常量（向后兼容读） |
| 2 | `quantstudio/pipeline/qfq_reanchor_schema.py:54` | `SCHEMA_VERSION` → `"reanchor-2.2"`（版本标记随迁移写入） |
| 3 | `quantstudio/pipeline/qfq_schema_status.py` | `SchemaStatus` 增 `COMPLETE_2_2`；`detect_schema_status` 先测 2.2 再测 2.1；**2.1 仍视为可写**（混合机队不阻断） |
| 4 | `quantstudio/pipeline/writers.py:259` | 安全闸放行集 `{EMPTY_OR_NEW, COMPLETE_2_1, COMPLETE_2_2}` |
| 5 | `scripts/migrate_source_watermark_last_run_id.py` | 改为契约级迁移：预检状态必须为 `COMPLETE_2_1`；`ALTER ADD COLUMN last_run_id VARCHAR`；复检必须为 `COMPLETE_2_2`；幂等；`--dry-run`；失败回退（DROP COLUMN）并**同时**复检回 2.1 |
| 6 | `quantstudio/pipeline/qfq_resident_orchestrator.py:1121` 附近 | 已有 `_has_column` 双兼容写入（本批已实现），无需改动；2.2 下写 `last_run_id = run_id`，2.1 下走旧 SQL |

**不改**：`last_batch_id` 语义与格式；`PRIMARY KEY(source, table_name, freq)`；`source_generation/cutover_id` 的 NOT NULL 语义；不加任何 CHECK/拒绝性约束（客户报告方案 D 的约束仍否决）。

## 3. 影响面

| 项 | 影响 |
|---|---|
| 主库结构 | `source_watermark` +1 可空列；24 行不动；无回填（历史 run_id 不可知，留 NULL） |
| 启动安全闸 | 2.1 与 2.2 **同时放行**（关键：避免升级窗口内"新代码 + 旧库"或"旧代码 + 新库"任一侧停摆） |
| 共享层 | `qfq_schema_contracts.py` 属共享层 → 按铁律**只修主仓**，QuantStudio-trading 副本跟随，不在副本本地改 |
| 数据正确性 | 无变化（不解锁任何 gate、不推进水位） |
| 回测精度/性能 | 无 |

## 4. 验收判据

| 判据 | 内容 | 形态 |
|---|---|---|
| A29 | 8 列库 → `COMPLETE_2_1`；9 列库 → `COMPLETE_2_2`；缺列/多列错配 → `PARTIAL_OR_MIXED` | 三例 |
| A30 | 安全闸对 `COMPLETE_2_1` 与 `COMPLETE_2_2` 均放行；`PARTIAL_OR_MIXED` 仍拒 | 正反各一 |
| A31 | 迁移幂等：二次执行零变更；行数 24→24；状态 2.1→2.2 | 正例 |
| A32 | 迁移失败（人为注入异常）→ 回退后状态回 `COMPLETE_2_1` 且列不存在 | 负向对照 |
| A33 | 2.2 下提交写 `last_run_id`；2.1 下旧路径逐字兼容（既有 A24 两例复跑） | 正例 ×2 |
| **A33-b** | **2.1 模式下写入不得产生 `last_run_id` 值**：在未迁移库（8 列）执行 `_advance_watermark`／`writer.advance_watermark`，事后校验 `last_run_id` 列**不存在**（或若已存在则值为 NULL 之外**不得被旧路径置为新值**）——防旧路径漏写列或误置态（2026-10-21 ②审追加） | 负向对照 |
| A34 | 全量回归：qfq 套件 + daemon 集成 + GUI（含本批 43 例）全绿 | 回归门 |
| A35 | 生产迁移窗口内：迁移前 `COMPLETE_2_1` → 迁移后 `COMPLETE_2_2` → **daemon 可启动**（`launch_daemon` 成功拉起，状态文件生成） | 生产取证 |

## 5. 回退

1. 任判据红 → 停止，按 A32 路径 DROP 列并复检回 2.1（已实证可回退）。
2. 生产窗口内 daemon 拒启 → 立即 DROP + 复检 + 重启，事件入卷宗（形态：契约变更未同步）。
3. 代码回退：`SCHEMA_VERSION` 与契约常量同 commit 回滚；2.1 数据不受影响。

## 6. 风险与前置（这次先测再做）

1. **先测再做**：迁移前必须先跑 `detect_schema_status()`（本轮教训：dry-run 只验"能加列"，未验"加列后状态"）。
2. **同 commit 纪律**：契约 + 版本常量 + status 判定 + 安全闸放行集 + 迁移脚本 + 测试，**缺一不得执行**。
3. **窗口**：迁移期间 daemon 停（否则写锁冲突）；迁移后必须立刻验证 `launch_daemon` 可启（A35）。
   **迁移窗口纪律（2026-10-21 ②审追加）**：窗口期内不仅 daemon 禁跑，**ops 类 CLI 的 `once`/单任务执行同样禁跑**
   （`python -m quantstudio.pipeline.daemon --mode once`、GUI「跑任务」、`qfq_orchestrator_cli` 等
   任何会 `create_writer`/`init_schema` 的入口）——它们在 2.1→2.2 迁移中间态会触发 `_WriterSchemaMigrationRequired`
   或写入半态数据。窗口内只允许：只读探测（`detect_schema_status`）、迁移脚本、迁移后验证。
4. **UI 与运维可见**：GUI「水位状态」列在 2.2 库上工作不变（只读聚合，不感知列集）。

## 7. 实施记录（2026-10-22 ③④）与设计增补

### 7.1 实施前发现的设计缺口（必须解决，否则部署即停摆）

`SCHEMA_VERSION` 不只用于标记，它被 **bootstrap 版本校验**消费：
`qfq_resident_orchestrator._require_bootstrap` 内 `schema_v != SCHEMA_VERSION → bootstrap 未完成 →
fail-closed（本轮不推进水位、不处理 trigger）`；而生产 `qfq_bootstrap_run.schema_version`
落的是 `reanchor-2.1`。**若仅按设计件改常量**，部署瞬间即 fail-closed —— 与 FP-5 加列事故同族。

### 7.2 增补（第 7 件，与六件同 commit）

| # | 文件 | 增补内容 |
|---|---|---|
| 7 | `qfq_reanchor_schema.py` 新增 `SCHEMA_VERSION_COMPAT = ("reanchor-2.1","reanchor-2.2")`；`qfq_resident_orchestrator._require_bootstrap` 改为**双接受**；迁移 runner 同步把 `qfq_bootstrap_run.schema_version` 由 2.1 升到 2.2（失败回退时一并回滚） | 升级窗口内"新代码 + 旧 bootstrap 行"不得 fail-closed；迁移完成后版本行直接命中 2.2，双接受仅覆盖窗口而非永久放宽 |

### 7.3 实施结果（本机实测）

| 判据 | 结果 |
|---|---|
| A29（8 列 2.1 / 9 列 2.2 / 错配 partial） | ✅ 3 例通过 |
| A30（安全闸双放行 / partial 仍拒） | ✅ 正反各一通过 |
| A31（迁移成功 + 行数不变 + 二次幂等；dry-run 零变更） | ✅ |
| A32（注入首次 UPDATE 异常 → 回退后 `complete_2_1` 且列不存在，rc=7） | ✅ 负向对照 |
| A33（2.2 写 `last_run_id`；`last_batch_id` 语义不变） | ✅ |
| A33-b（**2.1 下写入不得产生 `last_run_id` 列/值**） | ✅ 负向对照 |
| 契约自检（2.2 DDL 与 2.2 指纹逐字一致） | ✅ |
| A34 全量回归 | 见回报 |
| A35 生产窗口 | 待今夜窗（联合声明后执行） |

### 7.4 遗留（不在本批，登记备查）

- **新建空库仍按 2.1 DDL 建表**（`writers.DDL_DUCKDB` 引用 `SOURCE_WATERMARK_2_1_DDL` 未改）：
  fresh 安装后需跑一次迁移才是 2.2。是否让空库直接生 2.2，需在空库实跑验证后另行裁定
  （涉及 `assert_code_ddl_matches_target_2_1` 与指纹目标同步，超本批范围）。

## 8. ⑤退回③修复记录（2026-10-22 晚，生产窗失败后）

### 8.1 ④生产窗失败根因（已定位）

`init_duckdb_schema` 只处理 `COMPLETE_2_1`（`:757`），**没有 `COMPLETE_2_2` 分支**：

```
2.2 库 → assert_init_allowed 放行
      → 落空分支 → 进入"空库建表"路径（CREATE IF NOT EXISTS 无害）
      → 用 TARGET_MAIN_DB_2_1_FINGERPRINT（8 列）回读校验，库实为 9 列
      → RuntimeError: 空库建表后物理契约回读校验失败
      ⇒ 迁移后 daemon 启动失败
```

**教训**：新增状态值时，**所有消费该状态的判定分支**都要同步——本批在 `detect/assert/writers`
三处接了 2.2，却漏了 `init_duckdb_schema` 这个最关键的建表状态机；「四态放行」不等于「四态都有实现路径」。

### 8.2 修复（三处，最小改动）

| 处 | 改动 |
|---|---|
| `init_duckdb_schema` | 新增 `COMPLETE_2_2` 分支：`TARGET_MAIN_DB_2_2_FINGERPRINT` 校验后**严格只读 no-op**（与 2.1 同语义） |
| `qfq_schema_contracts.new_db_source_watermark_ddl()` | 空库建表**版本选择器**：env `QS_QFQ_NEW_DB_SCHEMA`（默认 `2.1`=零回归；可选 `2.2`=直生）；DDL 与回读指纹取自**同一返回项**，杜绝混用 |
| `assert_code_ddl_matches_target_2_1` | 按所选版本取材校验（2.1/2.2 各自自洽） |

### 8.3 三场景补验（实测，全绿）

| 场景 | 结果 |
|---|---|
| S1 2.2 库重启 | `complete_2_2`（**修复目标**）+ `writers._assert_qfq_schema_init_safe` PASS |
| S2 2.1 库重启（现生产态） | `complete_2_1`（幂等 no-op） |
| S3a fresh 空库（默认） | `complete_2_1` / 8 列 / 末列 `cutover_id` |
| S3b fresh 空库（`QS_QFQ_NEW_DB_SCHEMA=2.2`） | `complete_2_2` / 9 列 / 末列 `last_run_id` |
| A34 回归 | **112 passed**（schema/状态/迁移/bootstrap/本批） |

**空库版本裁定建议**：维持默认 `2.1`（零回归；fresh 安装跑一次迁移），2.2 直生保留为 env 可选——
切换默认会变更所有空库建表路径（GUI/once/测试 fixture）行为，超本批范围。

### 8.4 附加产出（ρ 取证工具，随批入库以备复现）

- `scripts/forensics_minute_rho.py`：在线 MCP 路径（**当前被 `factor snapshot synchronization` 闸拒绝**）
- `scripts/forensics_minute_rho_shards.py`：离线分片路径（可用；含时间基换算 `trade_time(CST)→UTC ms` 与库内 `time` 对齐的关键实现）
- 取证结论（2026-10-22 夜）：`ρ` 四列 median=1、`|ρ_high/ρ_open−1|` median=0、
  p95 在 1e-5~1e-4、max ≤2.9e-3；000563 尾部约 5% 行有 +0.113% 系统性偏移。
  即 B 判据的 `cross_column_inconsistent(high)` 主因是 **E1 容差 1e-9 对分钟精度过严**（H2 型），
  另有 000563 局部锚差异（H3 型）需单独立项——**本批不改 B 判据**（按总调度令）。
