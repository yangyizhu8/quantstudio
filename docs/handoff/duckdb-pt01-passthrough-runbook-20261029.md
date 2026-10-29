# Runbook：passthrough 覆盖链主键保护（PT-01a）· 处置与回滚

- **归属**：duckdb 数据库升级执行会话（图节点⑲）｜spec 002-duckdb-1.5-upgrade · FR-021/FR-022/FR-025
- **状态**：**备而不用**（本期不演练；文本保留供窗口内按需使用）
- **适用对象**：运维值班、客服升级窗口执行人

## 一、背景（为什么需要这份 runbook）

passthrough（类别 B 同名表）全量覆盖的旧实现为：

```
CREATE TABLE _pt_tmp_<t> AS SELECT * FROM <t> LIMIT 0   -- CTAS：不继承任何约束
INSERT INTO _pt_tmp_<t> SELECT * FROM _pt_src
DROP TABLE <t>                                            -- ← 主键在此消失
ALTER TABLE _pt_tmp_<t> RENAME TO <t>                     -- ← 新表无主键
```

`CTAS` 建出的临时表**不继承主键**，而 `DROP` + `RENAME` 只换表名不换约束 ⇒
**目标表主键被静默清空（1→0）**：不报错、不告警、采集照常"成功"。
读数：`docs/evidence/passthrough-drop-repro-20261028.md`（1.4.5/1.5.5/1.5.6 三版本复现）、
`docs/evidence/pt01-f2-truncate-20261029.md`（四组对照）。

**本期已改道**（`quantstudio/pipeline/writers.py`）：
F2 主路径 = `TRUNCATE` + `INSERT`（同一事务）⇒ 保留表对象与主键；
F1 备选 = 列集不一致时按 df 列集重建 + **显式重建同构主键**（命中指纹表则拒绝）。

## 二、判定：怎么知道命中了这个问题

| 现象 | 判定命令 | 期望 |
|---|---|---|
| 主键消失 | `python scripts/check_passthrough_pk.py --check` | 若报 `PK 从 ≥1 变 0` ⇒ 命中（退出码 2） |
| 配置层预防 | 启动时 `config_lint` | passthrough 指向带主键表 ⇒ **启动即报错**（新增前瞻守卫） |
| 日常巡检 | `python scripts/check_passthrough_pk.py` | 打印当前 PK 清单 |

> 注意：④实测（2026-10-30）—— `read_only` 连接可与 daemon 写连接**共存** ⇒ daemon 运行期 `--check` 可正常返回 **exit 0**（三次实测一致）；
> 仅在库被真正独占时出现 `SKIP: 库被占用`（退出码 3，**不是失败**，不计失败数）。
> （本 runbook 初稿「daemon 运行期间必然 SKIP」的表述据此作废。）

## 三、处置步骤（窗口内）

1. **停止写者**：关闭 GUI、停 daemon（先按铁律 19 声明占用窗）。
2. **取证**：`python scripts/check_passthrough_pk.py --snapshot` 存基线，再 `--check` 取当前态；
   记录哪些表 PK 归零、归零前的基线值来自 `docs/evidence/pk_baseline.json`。
3. **判定影响面**：PK 归零的表若参与 `ON CONFLICT` 去重/upsert ⇒ 去重能力已失效（可能重复写入）；
   若不参与（纯追加表）⇒ 仅缺索引，写入正确性未受损（但查询性能下降）。
4. **修复（两选一）**：
   - **恢复约束**（首选，保留数据）：
     ```sql
     -- 先确认无重复键，有重复先处理
     ALTER TABLE "<t>" ADD PRIMARY KEY (<pk cols>);
     ```
   - **重建表**（约束缺失且列集/类型需同时校正时）：导出 → 新表加约束 → 回灌 → 换名。
     **注意**：重建走 `CREATE TABLE AS SELECT` 会再次丢约束 ⇒ 必须在建表后显式 `ADD PRIMARY KEY`。
5. **复验**：`--check` 应输出无 ALERT；抽查 `ON CONFLICT` 仍可用（插入重复键应被拦下）。
6. **复启**：先起 GUI/daemon 单实例，观察首轮采集无重复写入、无 FATAL。
7. **回填主键基线**：修复完成后重新 `--snapshot` 更新 `docs/evidence/pk_baseline.json`。

## 四、回滚条件与回滚动作

| 触发 | 回滚动作 |
|---|---|
| 修复后出现重复写入 / 采集报错 | 立即停写者 → 用 §3.4 的重建表路径从备份恢复 → 复验 PK 与行数 |
| 需要撤销本次代码改道 | `git checkout -- quantstudio/pipeline/writers.py`（**精确路径**，勿整仓回退）→ 重启 daemon |
| 新增 `config_lint` 守卫误伤正常配置 | `git checkout -- quantstudio/pipeline/config_lint.py` → 重启；并在 config 侧把该任务改回非 passthrough |

## 五、监控指标（接入哨兵时用）

| 指标 | 判据 |
|---|---|
| `pk_zero_delta` | PK 由 ≥1 变 0 的表数，期望恒为 0 |
| `lint_pt_pk_conflicts` | config_lint 报出的「passthrough 指向带主键表」条目数，期望恒为 0 |
| `storage_version` | 文件头读数（当前 64，上限 68） |

## 六、边界与已知限制

- 本 runbook **备而不用**：本期不改库结构、不动主库数据，仅改代码路径 + 加守卫。
- 指纹校验表（qfq 契约表）**禁止**在无六件套的情况下做列集变更；本期代码对这类表直接拒绝（F1 分支抛错）。
- 日常巡检挂载点（总调度 2026-10-29 裁定）：**总运维每日哨兵 `schedule-b59df0be` 第 6 步**；本会话已交付接入说明
  `docs/handoff/duckdb-f4-sentinel-onboarding-20261029.md`（命令 / 退出码语义 / SKIP 非失败 / 告警文案），由④运维会话照 F4 onboarding 同款执行。
