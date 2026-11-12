# occupancy.json 解析失败 fail-open 最小修复方案（待独立审）

- 起草：2026-11-12 23:22（节点4）
- 触发：总调度 2026-11-12 23:20 指示「JSON 解析失败按未占用继续判断的 fail-open 已由源码确认。**立即出最小修复方案和负例，独立审后处理；未审不改脚本、阈值或任务**」
- 状态：**②审有条件通过 → 已按条件实施**（2026-11-13 00:00）
  - 条件履行：① 改为**隔离 RepoRoot** 验证（非生产状态/输出）；② N1–N6 全部完成；③ 写前锚已登记；④ 唯一 catch 差异
  - **未获授权项保持不动**：阈值、任务定义、daemon 启停

## 一、缺陷定性

`scripts/check_daemon_alive.ps1` L401-403（F3 占用声明闸的异常分支）：

```powershell
} catch {
    $occDetail = "occupancy.json 解析失败（按未占用处理并告警）: $($_.Exception.Message)"
}
```

- `$occupied` 保持初值 `$false` ⇒ **fail-open**（放行到 F4 冷却 → F11 旁证 → 拉起）
- 对比同函数内**窗口时间解析失败**的处理（L392-394）是 `$inWindow = $true`（**fail-closed**）——**同一闸内两种失败语义不一致**，这是缺陷的核心。

### 1.1 为什么现在才暴露

切 `-ReviveMode active` 之前，F3 被拦也只影响 observe 期记录；切 active 后，**F3 是"计划内停机窗"的唯一声明式防线**（总调度 2026-11-12 裁定的「occupancy 声明即防复活声明」纪律），fail-open 从"记录瑕疵"升级为"可能误拉生产 daemon"。

## 二、风险边界（本轮实测得出，须一并审视）

本方案**不是**在所有场景都必要——F11 旁证闸提供部分兜底。实测（2026-11-12 23:20:40）：

```
verdict=FAIL consecutive=3 gate=skip-suspected_activity action=NONE
  | 旁证命中 WAL活跃(mtime 40.9782615s前, 1.09MB) / 主库被独占(不可 ReadWrite/None 打开)
```

| 场景 | occupancy 损坏后 | F11 旁证 | 会误拉吗 |
|---|---|---|---|
| daemon **在跑**（WAL 活跃、库独占） | fail-open（声明失效） | **命中 ⇒ 拦** | ❌ 不会（本次实测） |
| **停服后**（WAL 静止、锁已释放、无他线占用） | fail-open（声明失效） | **不命中** | ✅ **会** |
| 停服后 + 他线正在操作（WAL 活跃） | fail-open | 命中 | ❌ 不会 |

⇒ **真实危险区 = 「已按声明停 daemon」+「声明文件损坏」+「F11 旁证不命中」三者同时成立**。
这正是运维2 P1 窗（14:30+ 停数小时）的**典型形态**：停服后 daemon 不在跑 ⇒ WAL 静止 ⇒ F11 不拦 ⇒ **全靠 occupancy ⇒ 一坏就误拉**。

⇒ **修复必要，但不夸大**：它是「最后一层声明式防线」的完整性修复，不是"随时会炸"。

## 三、最小修复（唯一改动点，3 行）

### 3.1 改法

```powershell
} catch {
    # ── 2026-11-13 修（fail-open → fail-closed；总调度 2026-11-12 指示）──
    #   文件存在但不可解析 ⇒ 声明可能有效但已损坏，按占用处理（与 L392-394 窗口解析失败同语义）。
    #   防：停服窗内声明损坏 ⇒ F3 失效 ⇒ F11 旁证不命中 ⇒ 误拉正在停机的 daemon。
    $occupied = $true
    $occDetail = "[OCC-FAILCLOSED] occupancy.json 存在但解析失败 ⇒ fail-closed 视为占用（需人工修复）: $($_.Exception.Message)"
}
```

### 3.2 改动特征

| 项 | 值 |
|---|---|
| 文件 | `scripts/check_daemon_alive.ps1`（唯一） |
| 行 | L401-403（catch 分支） |
| 净增 | **+2 行**（`$occupied = $true` 与注释），另 1 行改字符串（加 `[OCC-FAILCLOSED]` 标记） |
| 行为影响 | 仅「文件存在且不可解析」这一条路径：原先放行 → 改为拦截 |
| 不触碰 | 阈值 / 闸序 / `-ReviveMode` 默认值 / 任务定义 / 任何其他闸 |

### 3.3 为什么同时加 `[OCC-FAILCLOSED]` 标记

fail-closed 的代价是「文件永久损坏 ⇒ 守护永久失效（永不拉起）」。故必须在 detail 里留 **grep-able 标记**，让哨兵/巡检能独立发现：

```powershell
Select-String -Path data\logs\daemon_revival.log -Pattern '\[OCC-FAILCLOSED\]'
```

- 命中 > 0 ⇒ 有人工待办（修复 occupancy.json），**不是**正常态
- 该标记只进 detail 字符串，**不改 verdict**（避免与「不自行将 WARN 升 FAIL」冲突）

## 四、负例矩阵（**N1–N6 已在隔离 fixture 全部实测**，2026-11-13 00:00）

> 隔离环境：`%TEMP%\occ_fx_<ts>\`（自带 `data/`、`data/logs/`、`scripts/` 副本），
> 以 `-RepoRoot <fixture>` 运行 ⇒ **不触碰生产 occupancy/state/日志**；`-SimulateVerdict` 强制 observe ⇒ 零拉起。
> N1–N6 每例均先复位隔离 state，再连跑 `SimulateVerdict FAIL ×3` 至 `consecutive=3` 取第 3 次 gate。

| # | 构造 | 修复前 gate | 修复后 gate | 判定 |
|---|---|---|---|---|
| N1 | 文件不存在 | `all-pass` | `all-pass` | ✅ 行为不变 |
| N2 | `occupied=false`（合法） | `all-pass` | `all-pass` | ✅ 不变 |
| N3 | `occupied=true` + 窗口内 | `skip-occupancy` | `skip-occupancy` | ✅ 不变 |
| N4a | `occupied=true` + **窗口全在过去**（隔离 fixture，不改时钟） | `all-pass` | `all-pass` | ✅ 不变 |
| N4b | `occupied=true` + **窗口全在未来** | `all-pass` | `all-pass` | ✅ 不变 |
| N5 | `window` 时间格式非法 | `skip-occupancy` | `skip-occupancy` | ✅ 不变（既有 fail-closed） |
| **N6** | **JSON 语法损坏** | **`all-pass` + `action=REVIVE_ARMED`** | **`skip-occupancy` + `action=NONE` + `[OCC-FAILCLOSED]`** | ✅ **修复生效** |

**N6 修复前原文（隔离环境，无 WAL 干扰）**：
```
23:58:16 verdict=FAIL consecutive=3 gate=all-pass action=REVIVE_ARMED target_pid=-
         | observe 模式：本应拉起，仅记录（切 active 需 5 组负向对照全过 + 观察 ≥24h）
```
⇒ 隔离环境里 F11 不命中，**干净地暴露了 fail-open 的尽头就是 `REVIVE_ARMED`**（active 下即 `REVIVE_CALLED`）。

**N6 修复后原文**：
```
23:59:12 verdict=FAIL consecutive=3 gate=skip-occupancy action=NONE target_pid=-
         | [OCC-FAILCLOSED] occupancy.json 存在但解析失败 ⇒ fail-closed 视为占用（需人工修复）: Invalid object passed in, ':' or '}' expected.
```

### 4.0 N7 撤销（依据被总调度推翻，实测确认）

我原判「`occupied` 为字符串 `"true"` 会被 `-eq $true` 静默忽略」——**实测不成立**：

| 值 | 类型 | `-eq $true` |
|---|---|---|
| `"true"` | String | **True** ✅ 声明生效 |
| `"True"` | String | **True** ✅ |
| `"1"` | String | False（但无人会写） |
| `1` | Int32 | **True** |
| `"false"` / `0` | — | False（正确） |

⇒ PowerShell 比较存在**类型转换**，`"true"` 会被正确识别。**N7 撤销，不立缺陷。**

### 4.1 N6 残留取证（生产，历史留档，不再重复）

### 4.1 N6 实测原文（fail-open 证据）

```powershell
# 写入（故意缺右花括号）
{ "occupied": true, "owner": "...", "window": ["2026-11-12T23:00:00+08:00", "2026-11-13T01:00:00+08:00" ]
# 解析确认非法：Invalid object passed in, ':' or '}' expected. (124)

# SimulateVerdict FAIL ×3 结果
23:20:34 verdict=FAIL consecutive=1 gate=skip-threshold(1/3)      action=NONE
23:20:37 verdict=FAIL consecutive=2 gate=skip-threshold(2/3)      action=NONE
23:20:40 verdict=FAIL consecutive=3 gate=skip-suspected_activity  action=NONE   ← 期望 skip-occupancy，实得 F11
```

⇒ **F3 完全未参与判决**（fail-open 确证），拉起被 **F11 旁证**而非 F3 拦下。
⇒ 若场景换成「已停服 + WAL 静止」（F11 不命中），则此路径会走到 `REVIVE_ARMED`→（active 下）`REVIVE_CALLED`。

### 4.2 N7 说明（不在本方案范围）

`$occ.occupied -eq $true` 对字符串 `"true"` 不成立 ⇒ 声明被静默忽略。
- 属**另一类** fail-open（类型错误），但触发概率低（本文件由脚本生成，非手写）
- **建议单列**，不与本方案捆绑（避免扩大改动面）

## 五、验证方法（审后实施时用）

```powershell
# ① 语法与编码
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command `
  "$null = [System.Management.Automation.Language.Parser]::ParseFile('scripts\check_daemon_alive.ps1',[ref]$null,[ref]$e); if($e){$e;exit 1}else{'PARSE OK'}"
# ② 编码核对（PS 5.1 铁律：改 .ps1 后必须 UTF-8 BOM）
$b = [System.IO.File]::ReadAllBytes('scripts\check_daemon_alive.ps1'); "首3字节=$($b[0..2] -join ',')（应为 239,187,191）"

# ③ 负例复跑（N6 修复后须 skip-occupancy）
#    - 写损坏 JSON → SimulateVerdict FAIL ×3 → 断言 gate=skip-occupancy 且含 [OCC-FAILCLOSED]
#    - 恢复 → SimulateVerdict FAIL ×3 → 断言仍 skip-suspected_activity（F11 未受影响）
# ④ 回归：N2/N3/N5 三条既有路径行为不变
```

**验收判据**：N6 由「落到 F11」变为「`gate=skip-occupancy` + detail 含 `[OCC-FAILCLOSED]`」，且 N1/N2/N3/N5 行为逐条不变。

## 六、风险与回滚

| 风险 | 说明 | 缓解 |
|---|---|---|
| **守护永久失效** | 若 occupancy.json 长期损坏 ⇒ 永远 fail-closed ⇒ 永不拉起 | `[OCC-FAILCLOSED]` 标记 + 哨兵每日 grep（命中即人工待办） |
| 与既有 fail-closed 语义混淆 | 读者可能以为解析失败=停服中 | detail 字符串写明「存在但解析失败」「需人工修复」 |
| 判级未变 | 本方案不动 verdict（WARN 仍 WARN） | 符合「不自行将 WARN 升 FAIL」 |

**回滚**：单点回退（3 行）——把 `$occupied = $true` 删去、detail 串还原即可；或 `git revert <本笔>`。

## 七、明确不在本方案范围（总调度已裁定）

| 项 | 裁定 | 本轮动作 |
|---|---|---|
| H3 WARN 升 FAIL | **不自行升** | 不做 |
| 重启存活进程 | **不自行做** | 不做 |
| 「高 CPU ⇒ 未挂死」推断 | 总调度指出**不充分**（可能忙循环） | 已在 R12 自纠：降级为「未观察到完全停滞」，不构成未挂死证明 |
| H3 能力边界（真挂死不自愈） | **入 24h 汇总件** | 11-13 汇总写入 |
| 多分离窗口 | 单一声明限制 | 写入手册：多段停机须分次声明 / 交接 |

## 八、待审事项

1. 是否批准本方案（fail-closed + `[OCC-FAILCLOSED]` 标记）？
2. N7（`occupied` 类型错误）是否单独立项？
3. 是否要求同时补「多分离窗口」的显式报错（当前静默只认第一对）？——**建议单列**，不捆绑。

> 未获批准前**不实施任何改动**。


## 四.2 本方案**未覆盖**的 fail-open（如实列，不扩权）

| # | 形态 | 为何不在 catch 覆盖内 | 处置 |
|---|---|---|---|
| U1 | 合法 JSON 但**缺 `occupied` 字段**（如 `{}`、`{"owner":"x"}`） | 解析成功，不抛异常 ⇒ 不经过 catch；`$occ.occupied` 为 `$null` ⇒ `-eq $true` 为 False ⇒ **放行** | **未覆盖**，若要处理需另定语义（缺字段=未占用 or 缺字段=非法？）；**建议单列议题** |
| U2 | 合法 JSON 但 `window` **只有 1 个元素** | `L387` 判 `<2` ⇒ `$inWindow` 默认 true ⇒ **视为占用**（fail-closed）✅ 已正确 | 无需改 |
| U3 | 合法 JSON 但 `occupied` 为 `"1"` 字符串 | 转换后 False ⇒ 放行 | 概率极低（文件由脚本生成）；**不单列** |
| U4 | 文件被**其他进程写坏中途读取**（竞态） | 可能读到半截 ⇒ 抛异常 ⇒ 本次修复覆盖 ✅ | 已覆盖 |

> **明确**：本次 catch 修复只解决「**文件存在且 JSON 解析抛异常**」这一条路径，**不声称**覆盖 U1/U3。

## 四.3 验证结果汇总（隔离 fixture）

| 轮次 | 用例 | 结果 |
|---|---|---|
| 修复前 | N1,N2,N4a,N4b,N5,N6 | 6/6 符合预期；N6 复现 fail-open（`all-pass`） |
| **修复后** | **N1,N2,N3,N4a,N4b,N5,N6** | **7/7 全部 PASS**；N6 转为 `skip-occupancy` + `[OCC-FAILCLOSED]` |
| 语法定门 | `Parser::ParseFile` | `PARSE OK` |
| 编码定门 | 首 3 字节 | `239,187,191`（UTF-8 **BOM** 保持） |
| 差异面 | `git diff --stat` | **仅 1 文件 +5/−1**（唯一 catch 段） |
| 生产侧影响 | `occupancy=false` 合法 | **零影响**（catch 分支不触发） |

## 四.4 回退（2026-11-13 00:57 纠正）

> **纠正记录**：原文写 `git checkout HEAD -- scripts/check_daemon_alive.ps1` —— **错误**。
> 修复提交（`2eff4d9`）之后 HEAD 已含新版本，该命令取回的是**修复后**文件（sha256 `26D762D5…`），
> **不构成回退**。审核方 2026-11-13 指出，现改为「固定 blob / 父提交 + 原 SHA 对拍」。

| 项 | 值 |
|---|---|
| 写前 HEAD | `38d8202`（= 修复提交的父提交 `db613e03` 之后、`2eff4d9` 之前） |
| **原 blob** | `c784e6bda919901fa07c65fbd016cb1c413b789a` |
| 原 sha256 | `4D1434F2383F6CFBE6AC2E365A025DA76596FB6020D381713C31822D336C39C3` |
| 原大小 | 31419 B / 577 行 |

**三条正确配方（均按路径定向）**：
```powershell
# A) 按固定提交取回（推荐，最直观）
git checkout 38d8202 -- scripts/check_daemon_alive.ps1
# B) 按 blob 直取（不受提交图变化影响，最稳）
git cat-file blob c784e6bda919901fa07c65fbd016cb1c413b789a > scripts/check_daemon_alive.ps1
# C) 组合式
git restore --source=38d8202 --worktree -- scripts/check_daemon_alive.ps1
```

**建后立验（三项，缺一即视同无回退点）**：
```powershell
(Get-FileHash scripts\check_daemon_alive.ps1 -Algorithm SHA256).Hash
#   == 4D1434F2383F6CFBE6AC2E365A025DA76596FB6020D381713C31822D336C39C3
$null=[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path scripts\check_daemon_alive.ps1).Path,[ref]$null,[ref]$e); $e  # 须为空
$b=[System.IO.File]::ReadAllBytes('scripts\check_daemon_alive.ps1'); $b[0..2] -join ','   # 239,187,191
```

> **本轮实测**：按 B 配方导出 `38d8202:file` 副本 → sha256 **与锚内值逐位一致**；未对生产执行回退（未经授权）。

## 四.5 H3 归因边界（2026-11-13 收回过度推断）

我在 20261113-R1 上报中写「WARN 自愈 ⇒ **进一步支持**『长阶段静默』归因，但**仍不构成未挂死充分证明**」——
**前半句属过度推断，现收回**。

**正确的表述**：
- `00:00:37 verdict=OK` + `daemon.log` mtime 更新 ⇒ **只证明「该时刻之后 daemon 重新推进」**；
- 它**不能倒推** 22:28–00:00 期间先前到底发生了什么（长阶段计算 / 忙循环 / 部分卡顿后自恢复，三者观测等价）；
- 因此对 H3 WARN 的归因应保持「**未知**」，不得以事后恢复为据回填根因。

**推论**：H3 的「假阳性/假阴性双重风险」结论不变，且**无法用事后现象区分**——这正是它必须作为「能力边界」入 24h 汇总、而非「已证实无害」的理由。
