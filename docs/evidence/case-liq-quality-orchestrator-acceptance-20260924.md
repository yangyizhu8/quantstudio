# 客户「李梓嘉不会章鱼杀」巡检案 · 实施与验收证据

- **日期**：2026-09-24
- **方案**：`docs/case-liq-quality-orchestrator-four-defects-design.md`（过审，用户四裁定）
- **实施**：commit `42762a6`（单笔；六步③）
- **回退点**：`e4f55a0405a3f48e73c4bb7c7af0c151eb927dca`（`baseline-case-liq-20260924`，已 `git stash store`）
- **取证脚本**：`scripts/acceptance/case_liq_four_defects_probe.py` · `case_liq_rulelib_probe.py` · `probe_high_risk_schema.py` · `gen_quality_baseline.py`（全只读）

---

## 一、改动明细

| 维度 | 文件 | 改动 |
|---|---|---|
| **D1 异常显性化** | `scripts/quality_orchestrator.py` | `run_check` 增 `status` 三态（ok/detected/error）+ `error_kind`（7 类）；兜底 except 改判 error；`generate_report` 判定链：任一 error ⇒ ≥WARN，**L1 error ⇒ FAIL** |
| **D2 判定契约化** | 同上 | 废弃反向包含判定；正则解析 `gap=(\d+)` + returncode 校验 + **退出码/gap 一致性硬校验** |
| **D3 规则库补齐** | 同上 + `config/quality_baseline.json` | 6 表规则（阈值取自 `HIGH_RISK_DATES=3`）+ `threshold` 可机读字段 + `implemented` 状态 + 报告分离（`implemented_rules`/`not_implemented`/`error_summary`）+ `--list-rules` 展示 |
| **D4 时机/通道** | 同上 | 锁探测（含退避）+ 写路径 daemon 活跃期拒执行（退出码 3）+ 外部脚本路径可配（不可达 ⇒ error） |
| **R6 职责边界** | 同上 docstring | 显式声明：本模块=编排/门禁层；`quality_audit.py`=实现层；不重复实现 |
| **回归钉** | `tests/test_quality_orchestrator.py`（新） | 四缺陷各一条「构造故障 ⇒ 必显性」+ 综合 **23 例** |
| 取证 | `scripts/acceptance/*.py`（3 个新） | 四缺陷实验体证 / 规则库清单 / 6 表 schema 探测 / 基线生成 |

---

## 二、验收（用户四判据）

### 判据 1：四缺陷各一条「构造故障 ⇒ 必显性」钉 —— **23 passed**

| 缺陷 | 钉（要点） | 结果 |
|---|---|---|
| D1 | 内部异常 ⇒ `status=error` + L1 error ⇒ **verdict=FAIL**；非 L1 error ⇒ WARN；全 ok ⇒ PASS（回归）；未实现规则 ⇒ `error(not_implemented)` | ✅ |
| D2 | 8 组参数化：`gap=0`→ok / `gap>0`→detected / **空输出、缺库、崩溃、异常码、矛盾组合 ⇒ error（不得检出）**；源码**禁**反向判定字面；一致性校验在场 | ✅ |
| D3 | 6 表规则存在且 `dates_min==3`（权威口径）+ `_source` 标注；每条规则有 `threshold` 字段；`implemented` 齐备；报告 `implemented_rules < total_rules`；`--list-rules` 输出状态与阈值 | ✅ |
| D4 | daemon 活跃 ⇒ `--repair` **退出码 3** + 提示空档窗；锁探测：库缺失⇒`basis_unavailable`、锁冲突串族⇒`lock_conflict`；路径可配；不可达 ⇒ error | ✅ |

### 判据 2：回归全绿 —— **12 套件 146 passed**

`test_quality_orchestrator`(23) · `test_pit_filter` · `test_writer_channel_contract` · `test_mcp_fetch_routing` · `test_mcp_streaming` · `test_validator_behavior` · `test_quality_gate_by_freq` · `test_full_quality_audit_repair` · `test_xtquant_volume_unit` · `test_derive_market_value` · `test_mcp_etf_latest_anchor` · `test_f_series_export_fix`

### 判据 3：**前后对照（同一环境实跑）**

| 版本 | verdict | detected | errors | not_implemented | 说明 |
|---|---|---|---|---|---|
| v0.1 | **PASS** | 0 | 未建模 | 未建模 | 异常静默（如 `检查异常` 仍 PASS） |
| v0.2 | **FAIL** | 2 | 5 | 2 | 检出：`gap_heal OPEN=4` + `index_constituents 水位滞后 24d`；error：3 × `basis_unavailable` + 2 × `not_implemented` |

`--json` 工件：`docs/evidence/quality_orchestration_20260924.json`

### 判据 4：diff 范围

仅 `scripts/quality_orchestrator.py` / `config/quality_baseline.json` / `tests/test_quality_orchestrator.py` / 3 个 `scripts/acceptance/*`；**未触** daemon / 写通道 / 水位 / `backfill_eps_gap.py`（骨架与业务语义零改）。

---

## 三、开发期自纠（测试反证驱动，如实记录）

| # | 问题 | 发现方式 | 处置 |
|---|---|---|---|
| 1 | `_probe_db_lock` 对「库不存在」返回 `blocked=False` ⇒ 调用方会继续跑并撞异常 | `test_d4_lock_probe_propagates_lock_conflict` 失败 | 改为 `blocked=True` + 语义约定写入 docstring |
| 2 | eps「无契约输出」初版分 `tool_error`/`parse_error` 两义 ⇒ 语义含混 | 参数化用例失败 | 统一为 `parse_error`（拿不到可信结果即 parse_error） |
| 3 | 6 表规则**首版假设同构**（都用 `trade_date`）⇒ 实测 `index_constituents` 用 `(time, code)`、3 表不在本机主库 | 基线生成时 DuckDB `BinderException`/`CatalogException` | 每表显式声明 `(db, time_col, code_col)`；不可用 ⇒ `basis_unavailable`，不假设 schema |
| 4 | 退化残留：`issues` 里遗留占位串（v0.1 逻辑残留） | 代码自审 | 清除 |

---

## 四、未竟与待排（如实登记）

| 项 | 说明 |
|---|---|
| **本机 3 表不可巡**（`cyq_chips`/`ths_daily`/`stk_factor_pro` 不在主库） | 该 3 条规则本机报 `error(basis_unavailable)`；**需在具备该库的环境（客户机/运维机）复验**——客户机 `E:\quantstudio` 若有该库，则规则即生效。**用户 2026-09-24 裁定：客户机复验安排在客户拉取更新后的回报窗** |
| **v0.1 `cloud_parity` 一次「检查异常」根因未定** | 当时未用 `--json` ⇒ 完整异常文本未落盘；本次复跑 v0.1 为 `exit=0`（**非必现**）⇒ 不强归因。缺陷①要害（失败即静默成 PASS）已由**代码 + 实跑双证**，不依赖该次个案归因 |
| **R5 论断修正（我的方案件措辞）** | 方案 §四 R5 称「客户机必不存在」——正确表述应为：该路径是**我的机器**的部署路径，客户机不可达是**高可能但未实证**（客户机不可访问）。机制修复不受影响（可配 + 不可达报 error） |
| T4 二期（自动空档窗对接） | 登记待排，不在一期范围 |

---

## 五、修复前置纯增益审计自查（补记 · 铁律「修复前置纯增益审计（三型判定）」2026-09-24 批准落位）

> 说明：本批实施（`42762a6`）**先于**该铁律落位；按新铁律要求**事后补记**，如实标注时点。

| 前置审计项 | 结论 | 依据 |
|---|---|---|
| ① 是否影响项目其他功能 | **不影响** | 改动隔离在 `scripts/quality_orchestrator.py`（CLI 巡检工具，含其基线/测试）；**未触** daemon / 写通道 / 水位 / `backfill_eps_gap.py` / `eps_backfill.py`（`git status` 零触碰已核）；`quality_orchestrator` 在 `tests/`、`quantstudio/`、`skills/` 中**无引用方**，仅 CLI 自身 |
| ② 是否影响回测性能 | **不影响** | 不在回测引擎/数据访问层调用链上；巡检为独立 CLI，由人/排程触发 |
| ③ 是否影响回测精度 | **不影响** | 不涉取数口径/复权/PIT/撮合/策略语义；无策略产出物改动 |

**三型判定：多为「纯恢复型」+ 一处「有意新增能力」**

| 子项 | 类型 | 说明 |
|---|---|---|
| D1 异常显性化 / D2 判定修正 / D4 时机通道 | **纯恢复型** | 恢复文档化的设计意图（状态三态、契约判定、时机适配）；非破坏性纯新增 |
| D3 新增 6 表规则 | **有意新增能力** | 使巡检**新检出**真实的 6 表覆盖/水位/容量问题（本机实测：`index_constituents` 水位滞后 24d）；**副作用＝verdict 可能由 PASS 转非 PASS**，属**预期而非行为漂移** |

**未满足三型严格定义之处（如实披露）**：D3 新增规则使 verdict 可被**新证据**改变，
严格讲不能笼统称「可观察结果不变」——但它**不是既有行为的漂移**，而是**新增检测能力开始报告真相**；
既有 5 条已实现规则的判定结果除异常路径外**不变**（对照见 §二 判据 3）。
⇒ 建议后续同类「新增检测能力」类修复，在方案阶段即单列该形态（避免与「纯恢复型」混办）。

---
| 未实现规则补齐（`timestamp_normalize`/`minute_front_rewrite`） | 本批**只标注未实现**（消除覆盖幻觉）；补齐实现属后续独立项 |

---

## 五、客户侧行为变化（供通知时表述）

| 项 | 修复前 | 修复后 |
|---|---|---|
| 检查执行失败（锁冲突/超时/工具缺失） | 静默成 PASS | **显式 error**（L1 ⇒ verdict FAIL），并在 `error_summary` 列出 `error_kind` |
| 工具运行失败（如缺库） | 被当成「检出缺口」（假阳性） | **error**（与「检出」分流） |
| 规则覆盖 | 未实现规则计入 `total_rules`（覆盖幻觉） | 单列 `not_implemented`，并给 `implemented_rules` |
| 6 表行数/水位 | 无规则、无阈值 | 6 条规则（阈值取自既有对拍口径）+ 可机读 `threshold` |
| 巡检与 daemon | 无时机/通道设计 | 锁探测 + 写路径 daemon 活跃期拒执行（提示空档窗） |

> 即：客户今后可直接区分「**数据有问题**」（detected）与「**巡检没跑成**」（error）。