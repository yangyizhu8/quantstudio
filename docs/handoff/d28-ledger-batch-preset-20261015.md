# D-28 簿记补批 · 提交清单预置（preset，≠ 提交）

- **预置时点**：2026-10-15 20:56（Asia/Shanghai）
- **预置时 HEAD**：`8be00aa`｜**ahead=2**（`a5f192f` + `8be00aa`，`origin/main...HEAD` = `0 2`，fetch 时点＝预置时点）
- **批次性质**：簿记补批（D-28 盘后正式归档验收后的台账与证据修订）——**不含代码**，零功能风险
- **纪律**：精确路径 add ＋ 提交前 `git diff --cached --name-status` 核对暂存面（教训 44）；**预置动作本身不产生任何暂存/提交**

---

## 一、精确清单（3 路径＝用户点名项）

| # | 路径 | sha256（预置时点） | 字节 | 变更 | hunk |
| --- | --- | --- | --- | --- | --- |
| 1 | `docs/ISSUES.md` | `457f65981aec3b14212c9cc6b194a1d4caf807641eaeb1527d2b15971e4a3c85` | 53,741 | M（+1/−1） | 1 |
| 2 | `docs/PROJECT_STATE.md` | `58448e42ba330c6035055c6c65e291c9b619b1c1428361cc6f258739d664779f` | 55,452 | M（+2） | 1 |
| 3 | `docs/evidence/d28-e2e-run-20261015.md` | `15178358e83b553568d79156883ad08453e524db402384833762d37ad9259cd2` | 8,603 | M（+43/−1） | 1 |

（合计 +45/−2；三文件 mtime 均为 2026-10-15 19:54，来源＝本轮盘后归档验收落账）

### 可选附 1 路径（建议随批，形成闭环留痕）

| 4 | `docs/handoff/d28-ledger-batch-preset-20261015.md` | —（本文档，新建） | — | A | — |

---

## 二、执行命令（逐条可复制）

### ① 前置核对（fetch 时点必须写明）

```powershell
cd 'D:\miniQMT策略实盘\QuantStudio'
git fetch origin
git rev-list --left-right --count origin/main...HEAD      # 预期 0 N（N≥2，含 a5f192f+8be00aa 及其他同队提交）
git status --porcelain -- docs/ISSUES.md docs/PROJECT_STATE.md docs/evidence/d28-e2e-run-20261015.md
# 预期恰 3 行 M；若出现他线新增 hunk（三文件为共享台账）→ 逐 hunk 目视确认后再继续
```

### ② 精确 add（不含 `-A`/`.`，不碰其他文件）

```powershell
git add -- docs/ISSUES.md docs/PROJECT_STATE.md docs/evidence/d28-e2e-run-20261015.md
# 若采纳可选附项：
# git add -- docs/ISSUES.md docs/PROJECT_STATE.md docs/evidence/d28-e2e-run-20261015.md docs/handoff/d28-ledger-batch-preset-20261015.md
```

### ③ 提交前暂存面核对（教训 44 硬门，必须逐行看）

```powershell
git diff --cached --name-status
# 预期（3 路径版）：恰 3 行且全为 M，且恰为上述 3 路径；出现任何第 4 行即中止并排查
```

### ④ 提交信息（UTF-8 文件走 `-F`，避免 pwsh 中文转码）

拟稿：`chore(ledger D-28): 盘后正式归档落账（判据 1/2/3 + 四轮交叉一致性）＋提交哈希回填`

正文要点：
- ISSUES：D-28 端测行状态改「closed（盘后正式归档已于 2026-10-15 完成）」并录入盘后读数（处理段 554.8s→22.8s ≈24.3×；14/14 逐位零漂移）
- PROJECT_STATE：「盘后复跑（待执行）」改写为**已完成**＋四轮交叉结论（A 对两次 QSFUNDHIST 9,742,023/9,168＋9,716,108/35,083 相同；B 对两次 148,555/3,800＋148,196/4,159 相同）
- 端测证据 §七：盘后正式归档段（判定表＋四轮交叉表＋诚实归因）
- 不含代码改动；不含他线在途文件

### ⑤ 提交后核对

```powershell
git show --stat --format="%h|%s" HEAD | Select-Object -First 6   # 预期恰 3（或 4）文件
git status --porcelain -- docs/                                          # 预期空
```

---

## 三、写前快照（若批次执行时选择建回退点）

```powershell
$paths = @('docs/ISSUES.md','docs/PROJECT_STATE.md','docs/evidence/d28-e2e-run-20261015.md')
git add -A -- $paths
$h = git stash create -m "baseline-<YYYYMMDD-HHmm>-d28-ledger"
git stash store -m "baseline-<YYYYMMDD-HHmm>-d28-ledger" $h
git reset
git stash show --stat $h      # 核对内容（"有回退点"≠"回退点里有东西"）
```
> 台账批次为文档-only，可按需省略；若省略须在会话记录注明「文档批次未建快照」。

---

## 四、同批编排要件（用户裁定，不得遗漏）

1. **推送门**：本批**不单独推**——随 R1-C 批次与 dev 同队（预置时 ahead=2）；推送前按共享核心文件提交纪律核**双远程 HEAD 逐位一致**。
2. **D-29 呈报要件**（用户裁定②）：呈 D-29 候选时必须**一并附**——
   - **判据 4 的具体内容**：条件项＝若 D-29 裁①（补 `profit_ability` 映射）→ 键恒附（§三④）使 `SG-MS-PEG:358` 帧含折叠键 → **ROE 均值前后对照随端测产出**（修正方向＝回归本地口径，要件三「准确度只升不降」）；若未裁/裁② → 本判据由设计 §五-12 **真实函数合成对照代偿**并在端测报告标注；
   - **单测 12 代偿 vs 真实条件的覆盖差异**（下表，已备素材）：
     | 维度 | 单测 12 覆盖 | 真实条件（判据 4）| 差异性质 |
     | --- | --- | --- | --- |
     | 函数 | ✅ importlib 直载**真实** `_roe_mean_from_fin_indicator`（非镜像） | 同 | 一致 |
     | 数学 | ✅ 末 5 报告期均值 + 「折叠前＝最新报告期」错误口径实证 | 同 | 一致 |
     | 折叠语义 | ✅ 同一 helper、同一组合键 | 同 | 一致 |
     | 数据来源 | ❌ **合成帧**（数值/日期自造） | QMT 端 `profit_ability` 真实帧（NaN 行、ffill 形态、真实报告期分布与缺失模式） | **未覆盖** |
     | 装配链 | ❌ 直接喂 helper；用例 10/11 覆盖 `_qs_get_fundamentals` 投影/过滤但**面向 eps 表** | 经 hist 分支 + 完整行过滤 + 投影（profit_ability 路径 D-29 未修前**不可达**） | **未覆盖** |
     | 键恒附触发 | ◐ 手工放键列；字段集含 `profit_ability` 仅静态断言 | 真实键恒附端到端 | **部分未覆盖** |
     | 规模 | ❌ 6 报告期 × 1 码 | 3667 码 × ≈79 期 | **未覆盖** |
     | 引擎链路 | ❌ 不经过 QMT 引擎与策略调仓链 | 端到端 | **未覆盖** |
     > 结论口径：代偿覆盖**函数与数学正确性**，未覆盖 **profit_ability 在 QMT 端的真实数据形态与端到端链路**——该缺口只能由 D-29 修①后的一次真实端测（或回填判据 4）闭合。
3. **trading 同步门**：候四线清理回报齐后**同窗补跑**（D-27b + 本批欠账合并计算）；豁免条件不成立（本批触及共享层台账，且此前推送已触共享层）。

---

## 五、失效条件（何时本预置作废）

1. 三文件中任一在批次执行前被**他线改动**（共享台账）→ 须以执行时点的实际 dirty 面重新核对，sha256 重取；
2. 期间产生**新的 D-28 相关落账**（如后续补充证据）→ 路径清单需扩充；
3. HEAD 前进（他线提交）不影响本批路径，但 `ahead` 数与推送编排须重取（fetch 时点必须刷新）。
