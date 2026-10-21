# 统推执行预置件（明晨专轮齐备后执行）· 2026-10-17 21:2x

> **性质**：**预置≠执行**。本件为 2026-10-18 明晨呈批专轮齐备后的**一步执行**依据。
> **依据**：用户 2026-10-17 21:2x 裁定——采 **(b) 候他线先推** ＋ **明晨专轮后统推整合**；
> 执行人＝**我方（QMT 线）**；裁定标识＝**专轮批准记录**（`QS_PUSH_BUNDLE_RULING` 引用之）；
> `-AllowAlso` 用**逗号拼接**（教训 24）；推后**双远程逐位核验＋回报**；随后线调度跑**下一道同步门**（ps1 口径＋读数归档新制）。

## 一、裁定理由（采信，留档）

| 他线提交 | ⑤ 状态（用户核） | 判定 |
| --- | --- | --- |
| `1b09264`／`f4d9d80`（件3） | **⑤未齐**（候明晨 -2+⑦ 补完） | 不可随推 |
| `301daa7`／`3359789`（D1-补1／通知） | **⑤＝通知呈审未过** | 不可随推 |
| 其余（`0f43718`／`47d6927`／`486882d`／`0ea425a`） | — | 同批待专轮 |

⇒ **(a) 捆绑放行会把未过⑤的提交推出，门拦得正确**（C7 纪律）；本线「不自行断言捆绑放行」的处置获采信。

## 二、执行前置（齐备判据，缺一不执行）

| # | 前置 | 齐备标志 |
| --- | --- | --- |
| 1 | **件3 ⑤** | `-2+⑦` 补完并经用户确认 |
| 2 | **出库首推** | 专轮批准 |
| 3 | **QMCH** | 专轮批准（立项＋probe-first 排期） |
| 4 | **D-29 ⑤** | ✅ **已直批**（提交 `7c2cf40` 已在位） |
| 5 | **通知审定（D1-补1 的⑤）** | 审定通过 |
| 6 | **专轮批准记录** | 取得可作为 `QS_PUSH_BUNDLE_RULING` 引用的标识 |

## 三、执行序列（一步到位）

```powershell
# ① 基线读数（fetch 后注明时点）
git fetch origin; git rev-parse --short HEAD; git rev-list --left-right --count origin/main...HEAD

# ② 清单实测重取（★ 禁照抄本件；件3 补完/通知审定后会新增提交）
$list = git rev-list origin/main..HEAD
$list | ForEach-Object { git log -1 --format='%h %s' $_ }   # 逐笔目检：须全部⑤齐备

# ③ 逗号拼接（教训 24：必须逗号，禁空格分隔——空格 >4 sha 会假通过）
$allow = $list -join ','            # PowerShell 口径
# Git Bash 等价：git rev-list origin/main..HEAD | paste -sd, -

# ④ 门预检（ReportOnly，先看不推）
& 'scripts\prepush-gate.ps1' -Commit 7c2cf40 -AllowAlso $allow -ReportOnly

# ⑤ 推送（唯一放行通道：裁定引用 + 逐一覆盖；门强制留痕）
# ★★ 勾子坑（2026-10-18 实证，务必先读）：两个环境变量**都必须显式导出**
#   · 只设 QS_PUSH_BUNDLE_RULING 时，勾子报「有裁定引用（<标识>）但未用 -AllowAlso 列明允许捆绑的提交」⇒ 拒推；
#   · 勾子是从 **QS_PUSH_BUNDLE_ALLOWALSO**（环境变量）取清单，**不是**从命令行 -AllowAlso 取；
#     -AllowAlso 只用于手工调用 prepush-gate.ps1（含 -ReportOnly 预检）的场合。
$env:QS_PUSH_BUNDLE_RULING    = '<专轮批准记录标识>'
$env:QS_PUSH_BUNDLE_ALLOWALSO = (git rev-list origin/main..HEAD) -join ','   # 逗号拼接（教训 24）
git push origin main                # .githooks/pre-push 自动调门（读上面两个变量）

# ⑥ 双远程逐位核验（quantstudio-plus / quantstudio）
git ls-remote origin refs/heads/main
git remote -v                       # 确认双 push URL 指向一致 sha
git rev-list --left-right --count origin/main...HEAD   # 期望 0 0

# ⑦ 回报读数 → 线调度跑下一道同步门（ps1 口径 + 读数归档新制）
```

## 四、失效条件（执行前须重核）

1. **清单变动**：件3 补完（-2+⑦）与通知审定必然新增/变更提交 ⇒ **`-AllowAlso` 必须按执行时实测重取**，本件 §二 的 9 笔仅为时点快照；
2. **专轮结果**：任一前置未过 ⇒ 对应提交**不得**进入 `-AllowAlso`；若无法排除则**不推**、回报；
3. **门判据**：ReportOnly 若报「未逐一覆盖」⇒ 补全清单后重跑，**禁止 `--no-verify`**（若万不得已使用须在回报中声明）。

## 五、纪律与留痕

| 项 | 要求 |
| --- | --- |
| 放行通道 | 唯一：`QS_PUSH_BUNDLE_RULING` ＋ **`QS_PUSH_BUNDLE_ALLOWALSO`** 两个环境变量**都须显式导出**（实测只设前者 ⇒ 勾子拒推「未用 -AllowAlso 列明」）；手工 ReportOnly 调用才用 `-AllowAlso` 参数 |
| 留痕 | 门强制写 `docs/handoff/push-bundle-rulings.log`（放行事实） |
| 教训 24 | `-AllowAlso` **逗号拼接**；禁空格分隔（>4 sha 假通过） |
| C7 三查 | 本门是**叠加**非替代——人工三查照旧执行 |
| 推后 | **双远程逐位核验＋回报**；随后**线调度**跑同步门（ps1 口径＋读数归档新制） |

## 六、时点快照（2026-10-17 21:24，仅供对照，**禁照抄**）

领先清单 **9 笔**：`7c2cf40`（本件 D-29，⑤已批）｜`3359789`｜`301daa7`｜`0f43718`｜`1b09264`｜`f4d9d80`｜`47d6927`｜`486882d`｜`0ea425a`
HEAD＝`7c2cf40`｜ahead＝**9**｜`origin/main` vs HEAD ＝ **0/9**
