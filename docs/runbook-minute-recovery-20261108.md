# 04:30 后执行手册（2026-11-08 周日）

> 生成：03:4x ｜ 依据：③审周日执行序列
> 前置：40620 处置（预授权：04:30 仍无人认领且日志静默 >1h ⇒ 取证后终止）

---

## 0 处置 40620（**两档触发**）

| 档 | 触发条件 | 动作 |
|---|---|---|
| **A（04:30 档）** | 04:30 仍无人认领 **且 日志静默 >1 小时** | 取证后终止 |
| **B（06:00 硬上限·③审补授）** | **06:00 仍持锁（无论活跃与否）** | **取证后必须终止** |

**B 档理由（③审原话）**：「一个跑了 130 分钟的验证轮，重要性不高于恢复链。活跃只证明它没死，不证明它更重要。」
**杀后无副作用**：系统按设计走 FP-3 回收 / fail-closed（昨晚 once 被收割已实证此路径安全）。



```powershell
# ① 取证（先做，不留证据不许杀）
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$dir = "D:\qs_40620_forensic_$ts"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Get-CimInstance Win32_Process -Filter "ProcessId=40620" |
  Select-Object ProcessId,Name,CreationDate,CommandLine,ParentProcessId |
  ConvertTo-Json -Depth 3 | Out-File "$dir\cmdline.json" -Encoding utf8
Copy-Item data\logs\once_etf_daily_verify_20261108.log "$dir\" -ErrorAction SilentlyContinue
Copy-Item data\logs\daemon.log "$dir\daemon.log.snapshot" -ErrorAction SilentlyContinue
"取证目录: $dir"

# ② 终止（需 UAC）
Stop-Process -Id 40620 -Force
Start-Sleep -Seconds 5

# ③ 确认锁释放
python -c "import duckdb; c=duckdb.connect(r'D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db', read_only=True); c.close(); print('OK 无锁')"
```

---

## 1 回填（QuestDB 桥，先 dry-run 再 --apply）

```powershell
cd 'D:\miniQMT策略实盘\QuantStudio'

# ① dry-run（默认，不写库）— 先看差异
python scripts\backfill_minutes_from_questdb.py --table stock_minutes `
  --days 2026-11-04,2026-11-05,2026-11-06

python scripts\backfill_minutes_from_questdb.py --table etf_minutes `
  --days 2026-11-02,2026-11-03,2026-11-04,2026-11-05,2026-11-06

# ② 若 dry-run 显示还有更早日期的残差（如 10-26/10-27 证券数差 6 只），
#    按「全窗口逐日差集非零日」扩展 --days 列表重跑 dry-run

# ③ 真写（确认后）
python scripts\backfill_minutes_from_questdb.py --table stock_minutes `
  --days 2026-11-04,2026-11-05,2026-11-06 --apply
python scripts\backfill_minutes_from_questdb.py --table etf_minutes `
  --days 2026-11-02,2026-11-03,2026-11-04,2026-11-05,2026-11-06 --apply
```

**脚本特性**：默认 dry-run；`--apply` 才写；出现非预期 diff 立即失败退出（不做部分回滚）。

---

## 2 P1-A 隔离验证 → 开启

```powershell
# ① 隔离验证：用小表跑一次 once，确认异步路径可用
#    （配置先不改，用环境变量或临时 profile 验证；见下）
python -m quantstudio.pipeline.daemon --mode once --task mcp_etf_daily `
  --config-dir config/profiles/mcp_only

# ② 开启 P1-A（改配置）
#    文件: config/profiles/mcp_only/sources_config.json
#    位置: sources.mcp
#    加入: "export_async": true
#    回滚: 删除该键（默认 false）

# ③ 生效：daemon 重启后 adapter 重建
```

**验证判据**：开启后一轮完整取数，观察
- 无 `export_exceeds_time_budget`
- `get_manifest` 轮询正常（异步作业）
- 分钟表分片数正常（≥ 1.5 动态下限）

---

## 3 P0-D（dry-run 三分类 → 送审 → 执行）

```powershell
# ① 三分类清单（只读，不动库）
#    类1 窗口内·可修复 → requeue ≤2 次
#    类2 窗口内·结构性不可修复（退市/长停）→ 只清单化，不动库
#    类3 窗口外 → 归档

# ② 送审（先生逐行审 类2）

# ③ 备份 + 执行
#    备份 qfq_trigger_queue → requeue(类1) + 归档(类3) + FIXTEST 清理
```

---

## 4 forever 启动（09:00 起）

```powershell
cd 'D:\miniQMT策略实盘\QuantStudio'
# 前台观察首轮（13 天窗，约 2h/轮属预期）
python -m quantstudio.pipeline.daemon --mode forever --config-dir config/profiles/mcp_only

# 或后台
# Start-Process python -ArgumentList '-m','quantstudio.pipeline.daemon','--mode','forever','--config-dir','config/profiles/mcp_only'
```

**首轮验证点**（周日下午/傍晚）：reanchor 是否通过、水位是否推进、11-04/05/06 是否被历史批自愈。

---

## 5 代码修复上线决策（不抢工）

| 条件 | 动作 |
|---|---|
| 周日夜间「编码完成 + 全量回归绿」 | **周日夜上线**（周一受守卫保护，最优） |
| 否则 | **周一收盘后上线**，周二观察 |

**红线**：任何情况下不为时刻表牺牲验证完整度。

---

## 6 当前进度快照（03:45）

| 项 | 状态 |
|---|---|
| 1.5+1.4 纯函数 + 27 单测 | ✅ 完成（`b14e124`） |
| P1-A 配置 patch | ✅ 备好（未应用） |
| 回填脚本参数 | ✅ 确认（`--table/--days/--apply`） |
| 40620 | ⏳ 128+ 分钟，03:22 后静默 |
| 接入（1.5 挂到 `_export_batches`） | ⏳ 未开始（下一编码窗口） |
| 1.6 + 腿3 | ⏳ 未开始 |
