# 审计分段计时 + A2b 冷/热验证（2026-10-31）

> 用户裁定 A（分段计时）批准，两个附加要求：①双基线（冷/热并排）；②同脚本顺带采 full 域。
> 本文是「300s 去向表」，同时是 C 项（A2b 生产 E2E）的读数，也是**发版三选一的裁决输入**。

## 零、前置：RO 加固（事故后新增）

`DataQualityAuditor.run()` 对**主库**用 `duckdb.connect(read_only=True)`（合规），但对
**aux / batch_audit / quarantine** 用 `sqlite3.connect(...)` —— **默认 RW**：

| 位置 | 目标 | 形态 |
|---|---|---|
| `quality_audit.py:210` | 主库 | ✅ RO |
| `:381` | batch_audit.db | ❌ RW |
| `:420/:435` | quarantine.db | ❌ RW |
| `:801` | **qfq_aux.db（11.6GB）** | ❌ **RW**（drift 段） |
| `:1159` | **qfq_aux.db（11.6GB）** | ❌ **RW**（factor_monotonicity 段） |

**2026-10-31 事故因果链第一环即此**：标称「只读验证」的脚本实际持 aux 库 RW 句柄，与 daemon 的 3A 写锁竞争。

**加固手段（脚本层，不改产品代码）**：monkey-patch `sqlite3.connect`，对已存在的 `.db` 强制 `mode=ro`（URI）。
效果：审计路径若尝试写库会立刻 `OperationalError: attempt to write a readonly database` ——
**把「偷偷 RW」变成「显式失败」**。

## 一、task 域分段（RO 加固后，双基线）

命令：
```powershell
$env:QS_PROFILE_CASES='task-cold,task-warm'
python scripts/audit_section_profile.py
```

### run1 · task-cold（`QS_AUDIT_DRIFT_CACHE=0`）

```
wall = 371.25 s   issues=12   drift_cached=False
_audit_factor_monotonicity   334.25 s   90.0%   (n=1)
_audit_minute_anchor_drift    34.05 s    9.2%   (n=1)
_audit_watermarks              0.92 s    0.2%   (n=1)
_audit_batch_pipeline          0.00 s    0.0%   (n=1)
_audit_quarantine              0.00 s    0.0%   (n=1)
```

### run2 · task-warm（缓存开，命中）

```
wall = 394.89 s   issues=13   drift_cached=True
_audit_factor_monotonicity   390.72 s   98.9%   (n=1)
_audit_watermarks              3.21 s    0.8%   (n=1)
_audit_minute_anchor_drift     0.02 s    0.0%   (n=1)   ← A2b 生效，归零
_audit_batch_pipeline          0.00 s    0.0%   (n=1)
_audit_quarantine              0.00 s    0.0%   (n=1)
```

## 二、三点结论

### 2.1 🔴 真凶是 `_audit_factor_monotonicity`（90%），不是 drift

| 段 | cold | warm | 变化 |
|---|---|---|---|
| **factor_monotonicity** | **334.25 s（90.0%）** | **390.72 s（98.9%）** | 仍在（且本轮更慢） |
| minute_anchor_drift | 34.05 s（9.2%） | **0.02 s（0.0%）** | ✅ **A2b 完全消除** |
| watermarks | 0.92 s | 3.21 s | 噪声级 |
| batch_pipeline / quarantine | 0.00 s | 0.00 s | 免费 |

**用户预测「主体是其余全局段」方向正确**，但精确答案是：**`_audit_factor_monotonicity` 一个段独占 90%**。

### 2.2 ✅ A2b 机制验证通过（C 项达成）

| 判据 | 读数 |
|---|---|
| 冷态不命中 | `drift_cached=False` ✓ |
| 热态命中并留痕 | `drift_cached=True`，issues 12→13 ✓ |
| drift 段归零 | 34.05 s → **0.02 s** ✓（**降 99.94%**） |
| 只读安全 | RO 加固后审计照常完成，daemon 40920 并存无冲突 ✓ |

**但收益受限于 drift 的占比**：34 s / 371 s ≈ **9.2%**（与此前 47 s / 346 s ≈ 13.6% 同量级，两次独立测量一致）。

### 2.3 🎯 真正的杠杆：把同一缓存模式推广到 `_audit_factor_monotonicity`

| 方案 | 单次 task 域 | 相对现状 | 73 任务批量（审计段） |
|---|---|---|---|
| 现状（含 A2b） | ≈ 371 s | — | ≈ 7.5 小时 |
| **缓存 factor_monotonicity**（若与 drift 同构） | ≈ 371 − 334 ≈ **37 s** | **↓90%** | ≈ **45 分钟** |
| 缓存两者 | ≈ 37 s（drift 已归零） | ↓90% | ≈ 45 分钟 |

**⇒ A2b 不是错了，是量错了 scope**（用户原判）：基础设施（水位键 + 跨进程落盘 + 三校验 + 原子写）已付清，推广到 factor_monotonicity 是同一模式的复制。

**前提待验**：factor_monotonicity 的输入是否同样由「aux 库 + 水位」唯一刻划（若它读 aux 的因子序列，则同样可用水位键）。**这是下一步唯一需要确认的技术点**。

## 三、full 域分段（待跑）

`QS_PROFILE_CASES=full-warm python scripts/audit_section_profile.py`（≈1985 s）。
预期：factor_monotonicity 在 full 域同样占大头（若成立，06:00 轮的 33 分钟也能大幅压缩）。

## 四、RO 加固的副作用（如实记录）

- 审计正常完成，`issues` 数与 RO 前一致（12/13）⇒ **加固未改变审计结论**；
- 若未来审计路径需要写（如 checkpoint），RO 下会显式失败 —— 这是**期望行为**（审计不该写）。

## 五、沉淀日期

2026-10-31
