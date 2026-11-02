# 11-02 轮次取数 + 方案② full 域首验（正确方法）证据档

> 五轮守护收官（11:30/12:32/13:33/15:35/17:37）。归属日期：**2026-11-02**（r_84d35ef3）。

---

## 一、轮次结局：interrupted（dev 主动干预，非故障）

| 项 | 读数 |
|---|---|
| 轮次 | `r_84d35ef3`，sched=2026-11-02，started **06:02:38**，finished **17:18:42** |
| 结局 | `status=interrupted`，ok/fail **72/2**，attempted=74/eligible=74 |
| **中断原因** | **17:04:23 daemon 收到停止请求（`reason=dev_stuck_batch_18dd58_zero_progress`）**——dev 判定分钟表批次 `18dd58` 零进展，主动 stop |
| 停止后行为 | 任务边界优雅中断；全库审计**中止**（已完成 0 项、跳过 29 项）；FM 导出照常完成（health=complete）；17:18:42 轮次结束 |

## 二、三读数（九键 / duration / gap_snapshot）

### 2.1 九键（interrupted 覆盖值，归属 11-02）

| 键 | 值 | 分类 |
|---|---|---|
| interrupted | **true** | dev stop 的直接结果（**非审计自身故障**） |
| skipped_after_stop | **29** | 设计内：stop 后跳过未审项 |
| elapsed_s | **0.1** | 审计刚启动即被 stop |
| tables_audited | 23 | — |
| scope | all | — |
| skipped_oom / query_error / timeout | **0 / 0 / 0** | ✅ A 类绿（审计无自身故障） |
| incomplete | **true** | stop 所致 |

**分类判定**：**A 类（审计健康）+ 外部干预语境**——interrupted/incomplete 由 dev stop 造成；若 dev 未 stop，按 10-31 基线全库审计应 ~33 分钟完成。**不判审计失败**。

### 2.2 duration

**676.1 分钟（11.3h）** vs 10-31 的 323.4——差异全部来自**分钟表任务 8.5h 长窗口**（见 §三）。

### 2.3 gap_snapshot

不存在 ⇒ **dev S4 字段未落地**（不判失败，dev 侧待办）。

## 三、🔴 O4 同型的黄金证据（本轮最大收获）

日志明示三线水位延迟提交 + 重放窗口：

| 表 | 日志行（时间） | 解读 |
|---|---|---|
| stock_daily | `06:11:40 待提交窗口 57.0 天（old=1788364800000→candidate=1793289600000）：**提交前每轮将幂等重拉该窗口**` | **框架自己明说：水位不提交 ⇒ 每轮重拉 57 天** |
| etf_minutes | `08:33:39 水位延迟提交 candidate=1793343600000` | 同型 |
| stock_minutes | `17:04:23 待提交窗口 14.0 天（old=1792134000000→candidate=1793343600000）` | 同型；同批 `wrote 34892 rows（**新增 0**）`——**拉了 1145 万行 raw、新增 0**，纯重放 |
| stock_minutes | `08:48:59 own 冲突耗尽但行级校验过（reason=ok_degraded_calendar_…）` | **写冲突也在分钟表出现**（doom-loop 第三要素） |

**结论强化**：doom-loop 三要素（**水位延迟提交 + 每轮重拉窗口 + 写冲突**）在**日线与分钟表三线全部实证**。dev 的 stop 判断（zero_progress）有据：批次拉全量但新增 0。

**归属**：定性仍归 writers 域（O4 条款不变）；本证据档供 dev ⑤呈批件引用。

## 四、方案② full 域首验（正确方法 · 本轮完成）

| 项 | 读数 |
|---|---|
| 方法 | `DataQualityAuditor.from_config(db_path(), config/profiles/mcp_only/alignment_rules.json, ...)`（首次尝试误用 `config/alignment_rules.json` 路径不存在，已修正） |
| **tables_audited** | **23** ✓（10-31 失败版为 0——本次才是有效首验） |
| wall | **1453.6s（24.2 分钟）** vs 10-31 生产 1985.4s（33.1 分钟）——同量级偏快 27%（页缓存效应），**无可比性结论，仅记录** |
| **drift_cached / fm_cached** | **false / false** ✓✅ —— **full 域零缓存命中，方案②「full 不缓存」设计验证通过（正确方法版定案）** |
| issues | 37（checks_run=331） |
| top_checks | SourceTraceability 1.02 亿 / UnitConsistency 33.9 万 / QuarantineBacklog 10577 / FactorMonotonicity 5120 / AdjustmentReturnConsistency 1662 / AnchorDriftTruncated 1291 |

## 五、O4 台账关联

dev ⑤呈批件回执**仍未到达** ⇒ 台账 O4 行维持「待回执」；本档 §三 证据已备好供引用。

## 六、现态（18:00）

daemon 未运行（dev stop 后未重启，守护待机）；3A 锁无；主仓待推若干笔。

2026-11-02
