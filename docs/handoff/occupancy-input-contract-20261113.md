# occupancy.json 输入契约小方案 + `[OCC-FAILCLOSED]` 人工待办规程

- 起草：2026-11-13 00:59（节点4）
- 依据：总调度 2026-11-13 R1 审核「U1、多个分离窗口可合成输入契约小方案，明确缺字段、非法类型和窗口结构，**不直接扩实现**；`[OCC-FAILCLOSED]` 接入人工待办**先明确触发、去重及恢复条件**」
- 状态：**契约草案 · 不实施**（零代码改动）

---

## 一、目的与边界

本文件把「`data/daemon_occupancy.json` 到底是什么契约」写清，用于：
1. 让**声明方**（运维2 / 各实施窗）知道什么能写、什么无效；
2. 让**巡检方**（哨兵）知道什么该报、什么不该报；
3. 为后续是否扩实现提供**判据**，而不是直接改代码。

**明确边界**：本方案**不含任何代码改动**；凡与现有实现不符处，一律标注「契约 vs 实现的差」，交由审核裁定后才谈实现。

---

## 二、字段契约（草案）

| 字段 | 类型 | 必填 | 语义 | 缺失/非法时的期望行为 |
|---|---|---|---|---|
| `occupied` | **boolean**（`true`/`false`） | **必填** | 是否处于声明占用 | 缺失 ⇒ **视为未占用**（当前实现）；**待裁定**是否改为「非法」 |
| `owner` | string | 必填 | 声明方标识（会话名/节点号） | 缺失 ⇒ 告警但仍按 `occupied` 生效（当前实现不校验） |
| `window` | **array[2] of ISO8601 字符串** | 条件必填（`occupied=true` 时必填） | 单区间起止 | 缺失或 `<2` 元素 ⇒ **全时段占用**（`L386` 默认 `inWindow=true`） |
| `operation` | string | 建议 | 操作类型描述 | 缺失 ⇒ 无影响 |
| `declared_at` | ISO8601 字符串 | 建议 | 声明时刻 | 缺失 ⇒ 无影响 |
| `note` / `short_window_note` | string | 可选 | 备注 | 缺失 ⇒ 无影响 |

### 2.1 关于 `occupied` 的类型边界（已实测）

```powershell
"true"  -eq $true   # True    ✅ 生效
"True"  -eq $true   # True    ✅ 生效
1       -eq $true   # True    ✅ 生效
"false" -eq $true   # False   ✅ 正确
"1"     -eq $true   # False   ⚠️ 静默视为未占用
$null   -eq $true   # False   ⚠️ 缺失即未占用
```

⇒ **契约应写明**：`occupied` 必须是 **JSON 布尔字面量**（不加引号）。字符串 `"true"` 虽能生效，**不属契约允许形态**，不应依赖。
⇒ `"1"` 与 `$null` 目前**静默放行**，属契约外行为（见 §五 U1/U3）。

---

## 三、窗口结构契约（草案）

### 3.1 允许
- **单区间**：`["<start>", "<end>"]`，ISO 8601 含时区（`2026-11-13T14:30:00+08:00`）。
- **跨午夜**：起止跨自然日**合法**（`DateTime` 比较天然支持），如 `["2026-11-13T23:00:00+08:00", "2026-11-14T06:00:00+08:00"]`。
- **过去/未来区间**：合法，只影响窗口命中判定。

### 3.2 不允许
- **多个分离窗口**：当前实现**只读 `[0]`/`[1]`**，第 3 个及之后元素被**静默忽略**。
  ```json
  { "occupied": true, "window": ["A","B","C","D"] }   // C/D 被丢弃
  ```
  ⇒ 若要停两段（14:30–16:00 与 17:00–18:30），**必须分两次写声明**，或停机中途由声明方续写。
- **非字符串元素**、**元素 <2**：见 §二 表格。

### 3.3 硬约束（运维纪律，非实现）

> **`window[1]`（end）必须覆盖实际恢复时点**。
> 若实际停机超出声明窗 ⇒ 窗口过期后 `inWindow=false` ⇒ F3 失效 ⇒ 若此时 daemon 确未运行则可能被拉起。
> 超窗时必须**续写声明**或**交接给下一个声明方**。

---

## 四、当前实现 ↔ 契约 差异矩阵

| # | 契约要求 | 当前实现 | 差异 |
|---|---|---|---|
| C1 | `occupied` 必填且为布尔 | `-eq $true`（含类型转换） | 宽于契约（`"true"`/`1` 也生效） |
| C2 | 缺 `occupied` 应判非法 | 静默视为 `false` | **偏放行**（U1） |
| C3 | `window` 非法应 fail-closed | 解析失败 ⇒ `$inWindow=$true` | ✅ 符合 |
| C4 | `window` 缺失 ⇒ 全时段占用 | `$inWindow=$true` | ✅ 符合 |
| C5 | JSON 损坏应 fail-closed | **已于 `2eff4d9` 修复** | ✅ 符合 |
| C6 | 多窗口应报错或支持 | 静默只认前两个 | **偏差**（§3.2） |
| C7 | `owner` 必填 | 不校验 | 仅记录缺失 |

---

## 五、三个待裁定语义（不扩实现）

| # | 议题 | 现状 | 选项 |
|---|---|---|---|
| **U1** | 合法 JSON 但**缺 `occupied` 字段**（`{}` 或 `{"owner":"x"}`） | 静默视为未占用 ⇒ **放行** | **甲** 维持（文件由脚本生成，缺字段=未声明）｜**乙** 改为 fail-closed（缺字段=非法声明）｜**丙** 仅告警不改变行为 |
| **U3** | `occupied` 为字符串 `"1"` 或 `$null` | 静默未占用 ⇒ 放行 | 同 U1 三选项；概率低（脚本生成） |
| **U5** | **多分离窗口**（`window` 有 >2 元素） | 静默忽略多余元素 | **甲** 显式告警（detail 记「多余元素被忽略」）｜**乙** 报错 fail-closed｜**丙** 支持多区间（扩实现，**不建议**） |

> **建议**：U1/U3 取「丙（仅告警）」，U5 取「甲（显式告警）」——三者均**不改判定结果**，只提升可观测性，属最小增量。

---



## 五.1 U1/U3/U5 隔离负例实测（2026-11-13 02:24，隔离 fixture）

| 用例 | 构造 | 实测 gate | 实测 action | 结论 |
|---|---|---|---|---|
| U1-a | `{}`（空对象） | **`all-pass`** | `REVIVE_ARMED` | 缺 `occupied` ⇒ **放行** |
| U1-b | `{"owner":"x"}` | **`all-pass`** | `REVIVE_ARMED` | 同上 |
| U3 | `{"occupied":"1"}` | **`all-pass`** | `REVIVE_ARMED` | 字符串 `"1"` ⇒ **放行** |
| U5-a | 4 元素，**前两在窗口内** | `skip-occupancy` | `NONE` | 只读前两元素 |
| **U5-b** | 4 元素，**前两在未来 / 后两在窗口内** | **`all-pass`** | `REVIVE_ARMED` | **后两元素被静默忽略** |

### 5.1.1 风险正面评估（不以「概率低」代替证明）

审核指出：**仅告警但继续放行，是否仍让声明方误以为停机保护已生效**——必须正面回答。

**回答：会，而且 U5-b 已经实测证明。**

| 场景 | 声明方的心智模型 | 系统实际行为 | 差距 |
|---|---|---|---|
| U5-b：写 4 个时间戳意图表达两段停机 | 「两段都受保护」 | **只有第一对生效**；第二段窗口内**F3 完全不拦** | **声明→保护 断链** |
| U1：手写 `{"owner":"运维2","operation":"P1窗"}`，忘了 `occupied` | 「已声明占用」 | **放行**（等同未声明） | 同上 |
| U3：`"occupied":"1"`（其它系统习惯） | 「已声明」 | **放行** | 同上 |

**共同性质**：三者都是**静默失败**（silent failure）——声明方无法从任何界面/日志得知「我的声明没生效」。

**为什么不能以「概率低」放过**：
- 停机窗恰好是**风险最高的时段**（daemon 停、F11 旁证不命中、指望 F3 兜底）；
- U5-b 的触发不需要"手写错误"——**任何想表达多段停机的自然写法都会掉进去**；
- 单次失效的后果是**误拉 daemon 撞他线操作**（尾部风险，不可逆）。

**结论**：U1/U3/U5 的「仅告警」方案**不足以消除风险**——因为告警若不进人眼（日志），等于没有。故建议：
- **至少**：detail 里写明确切原因（`[OCC-IGNORED] window 有 N 个元素，仅前两生效` / `[OCC-MISSING] occupied 字段缺失，视为未占用`），使哨兵可 grep；
- **更稳**：若审核采纳，U5 与 U1 改为 **fail-closed**（与本次 catch 修复同语义：宁可少拉不多拉）。
- 本条**不实施**，等裁定。

## 五.2 `[OCC-FAILCLOSED]` 事件计数：区分「坏文件事件」与「重复日志」

审核指出：**不以字符串条数直接作为事件数**。修正规程如下：

| 概念 | 定义 | 计量 |
|---|---|---|
| **日志行** | `daemon_revival.log` 中每轮心跳一行 | 每 10 分钟 +1（同一坏文件会连续产生多行） |
| **坏文件事件** | (文件内容 sha256) 的一个取值 | 唯一 sha256 计数 |
| **事件数** | `|{sha256(f) : f 期间出现过 [OCC-FAILCLOSED]}|` | 上者 |

**示例**：同一份坏 JSON 持续 6 小时 ⇒ 日志行 **36 行**，事件 **1 个**（sha256 相同）。
**内容被改坏两次**（不同 sha256）⇒ 事件 **2 个**，即使日志行更多。

⇒ 哨兵判据须按 **sha256 去重后的事件数**，不得用 `Select-String -SimpleMatch` 的命中行数（后者是日志行数，会放大 36 倍）。

## 六、`[OCC-FAILCLOSED]` 人工待办规程（草案）

### 6.1 触发条件（何时产生待办）

| 条件 | 产生 |
|---|---|
| `daemon_revival.log` 出现 `[OCC-FAILCLOSED]` | ✅ 一条待办 |
| `daemon_occupancy.json` **不存在** | ❌ 不产生（正常态） |
| `occupancy` 合法但 `occupied=false` | ❌ 不产生 |

**语义**：该标记表示「声明文件存在但不可解析 ⇒ 守护**转 fail-closed 拒绝拉起**」，即**守护暂时失效**，必须人工修复文件。

### 6.2 去重规则

| 情形 | 处理 |
|---|---|
| 同一 `occupancy.json` 内容（按 **sha256**）连续多轮命中 | **只算一条待办**；日志每轮仍写（不改日志行为），但待办计数取「唯一 sha256 数」 |
| 内容变化后再命中（如修复未成功、又改坏） | 新 sha256 ⇒ **新待办** |
| 文件被删除后重建再坏 | 新 sha256 ⇒ 新待办 |

⇒ 哨兵判定口径：`待办数 = |{sha256(occupancy.json) : 出现 [OCC-FAILCLOSED] 的轮次}|`

### 6.3 恢复条件（何时关闭待办）

| 步骤 | 判据 |
|---|---|
| ① 修复文件 | `Get-Content ... | ConvertFrom-Json` 成功（不抛异常） |
| ② 声明语义正确 | `occupied` 存在且为布尔；`occupied=true` 时 `window` 为 2 元素 ISO 字符串 |
| ③ **观察一次真实心跳** | 下一轮 `daemon_revival.log` **不再**出现 `[OCC-FAILCLOSED]`，且 `gate` 回到 `skip-threshold` / `skip-occupancy`（取决于声明内容） |
| ④ 关闭 | 上述三项同时成立 ⇒ 待办关闭；仅①成立不得关闭 |

### 6.4 告警面

- **不新增日志文件**（避免多载体漂移），复用 `daemon_revival.log`（已有 `[OCC-FAILCLOSED]` 标记）。
- 哨兵 v5 第 2 步「四判据」拟增一条：`[OCC-FAILCLOSED]` 命中数（按 §6.2 去重），**待审后**接入。

---

## 七、不实施声明

本文件为**契约草案**，不含任何代码/阈值/任务改动。
待裁定项：U1/U3 取何选项、U5 取何选项、哨兵是否接入 §6.4 判据。
**未获裁定前不实施。**

---

# 八、v2 方案（2026-11-13 08:56 起草 · 依 R3 审核裁定方向 · 待独立②审）

## 8.1 裁定方向（审核原文要点）

- 「**非法声明 fail-closed ＋ 明确告警**」的最小方案方向
- **不支持多区间**、**不静默截断**
- 先核**现存声明写方兼容**
- 补隔离用例：**缺字段、非法类型、多元素、反向区间、有效 false**
- 再**独立②审实施**

## 8.2 写方兼容核查（新增，实测）

**仓内代码面结论：未发现程序化写入方（2 处命中均为只读）。该结论不外推到外部工具/他线会话/手工脚本。**

| 检索面 | 命中 | 性质 |
|---|---|---|
| `scripts/check_duckdb_firstweek_obs.py:164` | `occ_path = os.path.join(REPO,"data","daemon_occupancy.json")` | **只读** |
| `scripts/check_daemon_alive.ps1:278` | `$occupancyPath = Join-Path $RepoRoot 'data\daemon_occupancy.json'` | **只读** |
| 全仓 `scripts/**` + `quantstudio/**` | 无 writer | — |

⇒ **本扫描仅覆盖仓内代码面**，**不能排除外部工具／其它会话／手工脚本写入**（总调度 R6 明确撤回
「全仓未见 writer ⇒ 只有人工写 ⇒ 只需兼容一个实例」这一推断）。
⇒ 因此 v2 兼容性取**保守口径**：只校验必需 3 项、未知字段一律忽略，且**任何非法形态一律 fail-closed（阻拉）**——
即便未来出现新写方，也只会落入「阻拉待修」而不会「静默放行」。

**现存生产实例实态（6 字段，含 2 个非契约字段）**：
```json
{ "occupied": false,
  "window": ["2026-11-11T05:55:42+08:00","2026-11-11T05:56:42+08:00"],
  "operation": "RELEASED（第五死恢复完成，daemon PID 36372）",
  "owner": "会话节点⑳",
  "short_window_note": "仅覆盖操作本身...",
  "daemon_pid": 36372 }
```
⇒ 方案**必须容忍未知字段**（`short_window_note`/`daemon_pid`），**不得因多字段报错**。

## 8.3 隔离用例全表（2026-11-13 08:54 实测，隔离 fixture）

| # | 构造 | 现状 gate | 现状 action | 应然（v2 目标） | 差异 |
|---|---|---|---|---|---|
| **A1** | `{}` 空对象 | `all-pass` | `REVIVE_ARMED` | **fail-closed**（缺 occupied ⇒ 非法） | ❌ 需修 |
| A2 | `{"occupied":true}` 缺 window/owner | `skip-occupancy` | `NONE` | `skip-occupancy`（缺 window ⇒ 全时段占用） | ✅ 已正确 |
| A3 | `{"occupied":false}` | `all-pass` | `REVIVE_ARMED` | 放行 | ✅ |
| **B1** | `occupied:"yes"` | `all-pass` | `REVIVE_ARMED` | **fail-closed**（非法类型） | ❌ 需修 |
| B2 | `occupied:1`（数字） | `skip-occupancy` | `NONE` | 视为占用（数字 1 → True） | ⚠️ 可接受（不误放行） |
| **B3** | `window:"<单个字符串>"` | `skip-occupancy` | `NONE` | 视为占用（fail-closed） | ✅ 已正确（`[string].Count=1 <2`） |
| C1 | `window` 4 元素 | `skip-occupancy` | `NONE` | **不得静默截断** ⇒ 告警/拒认 | ❌ 需修（多元素静默） |
| **D1** | `window` **反向**（start > end） | **`all-pass`** | `REVIVE_ARMED` | **fail-closed**（区间非法） | ❌ **新发现** |
| E1 | `occupied:false` + 完整字段 | `all-pass` | `REVIVE_ARMED` | 放行（**有效 false 不受影响**） | ✅ |
| **F1** | **现存生产实例原样**（6 字段） | `all-pass` | `REVIVE_ARMED` | 放行（**兼容**） | ✅ **兼容性通过** |

### 8.3.1 D1 反向区间（本轮新发现，fail-open 第 3 类）

`window: ["<end>", "<start>"]`（start > end）⇒ 判定式 `now >= ws AND now <= we` **恒为 false** ⇒ `inWindow=false` ⇒ **放行**。

声明方**把起止写反**（极易发生：复制粘贴/手填顺序错）⇒ 声明静默失效 ⇒ 与 JSON 损坏同族风险。
**现状无任何告警**。

### 8.3.2 C1 多元素（静默截断）

4 元素时只读 `[0]`/`[1]`，**第 3+ 元素被静默丢弃**（U5-b 已证：前两在未来、后两在窗口内 ⇒ `all-pass`）。
⇒ 违反审核「**不静默截断**」要求。

## 8.4 三类状态分离定义（依审核「不能混判」）

| 状态 | 定义 | v2 行为 | 告警标记 |
|---|---|---|---|
| **S1 文件不存在** | `Test-Path` false | **放行**（未声明，正常态） | 无 |
| **S2 文件存在但内容非法** | JSON 解析失败／`occupied` 缺失／类型非法／`window` 结构非法（非 2 元素字符串、反向、时间不可解析） | **fail-closed ⇒ 视为占用**（不拉起） | `[OCC-INVALID] <具体原因>` |
| **S3 声明合法** | `occupied` 为布尔 + （`true` 时 `window` 为 2 元素合法 ISO、start ≤ end） | 按语义判定 | 无（`true` 时 `[OCC-ACTIVE]` 可选） |
| **S3a `owner` 缺失** | 其余合法但无 `owner` | **仍按 S3 判定**（owner 仅用于溯源） | `[OCC-NO-OWNER]` 附加提示 |

⇒ **S1 / S2 / S3a 三者互斥且可区分**，不再混判为「未占用」。

## 8.5 事件模型（episode，修正 R3 意见）

审核指出：**同一内容修好后再次损坏可能是新 episode，事件数不能机械等于全历史唯一 SHA 数**。

| 概念 | 定义 |
|---|---|
| **日志行** | 每轮心跳一行（同一坏文件持续 6h ⇒ 36 行） |
| **episode** | 一段**连续的** `[OCC-INVALID]` 轮次序列；**中间出现「成���轮（无标记」即闭合** |
| **事件数** | **episode 数**（不是唯一 SHA 数，也不是日志行数） |

**判定规则**：
1. 扫描按时间序；
2. 遇到带 `[OCC-INVALID]` 的轮 ⇒ 若上一轮无标记 ⇒ **新 episode +1**；
3. 若上一轮有标记且 **sha256 相同** ⇒ 同一 episode 续；
4. 若上一轮有标记但 **sha256 不同** ⇒ **新 episode +1**（内容变了）；
5. **「无标记轮」不等于修好**——无标记可能只是 F2 阈值未达而**提前退出**（`consecutive < 3` 时根本不进 F3），
   此时文件仍是坏的。故 **episode 闭合须满足其一**：
   - (a) 该轮**实际进入 F3 且成功校验**（即 gate 为 `skip-occupancy`（合法占用）或走到 `all-pass`/后续闸，且 detail 无 `[OCC-INVALID]`/`[OCC-FAILCLOSED]`）；
   - (b) 收到**明确修复回执**（人工确认文件已修好）。
   ⇒ 仅凭"某轮日志无 OCC 标记"**不得关闭 episode**。

⇒ 同一个坏内容「坏→修好→再坏」= **2 个 episode**（旧规则会算 1）。

## 8.6 最小实施方案（**待②审，不实施**）

### 8.6.1 改动点（唯一文件 `scripts/check_daemon_alive.ps1`，F3 段）

在 L382 `Test-Path` 之后、L384 `ConvertFrom-Json` 周围增加**声明合法性校验**，伪代码：

```powershell
if (Test-Path $occupancyPath) {          # S1 不存在 ⇒ 走原来的"未占用"
    try {
        $occ = Get-Content ... | ConvertFrom-Json
        $raw = Get-Content ... -Raw
        $sha = (Get-FileHash $occupancyPath -Algorithm SHA256).Hash

        # —— S2 合法性校验（任一不过 ⇒ fail-closed + [OCC-INVALID]）——
        $invalid = $null
        if (-not ($occ.PSObject.Properties.Name -contains 'occupied')) {
            $invalid = 'occupied 字段缺失'
        } elseif ($occ.occupied -isnot [bool]) {          # B1/B3 同类
            $invalid = "occupied 类型非法（$($occ.occupied.GetType().Name)）"
        } elseif ($occ.occupied -eq $true) {
            if (-not $occ.window -or $occ.window.Count -ne 2) {
                $invalid = "window 元素数=$($occ.window.Count)（须恰好 2）"    # A2/C1
            } else {
                try {
                    $ws = [DateTimeOffset]::Parse($occ.window[0]).LocalDateTime
                    $we = [DateTimeOffset]::Parse($occ.window[1]).LocalDateTime
                    if ($ws -gt $we) { $invalid = "window 反向（start > end）" }  # D1
                } catch { $invalid = 'window 时间不可解析' }
            }
        }
        if ($invalid) { $occupied = $true; $occDetail = "[OCC-INVALID] $invalid ⇒ fail-closed 视为占用" }
        else { <原有 window 判定逻辑> }
    } catch { $occupied = $true; $occDetail = "[OCC-FAILCLOSED] JSON 解析失败 ⇒ ..." }
}
```

### 8.6.2 与既有 `2eff4d9` 的关系

- `2eff4d9` 已覆盖 **S2 的「JSON 解析失败」** 子情形（`[OCC-FAILCLOSED]`）
- 本方案把 S2 **扩展**到「解析成功但语义非法」（`[OCC-INVALID]`）
- 两者**同一语义方向**（非法 ⇒ 视为占用），只是标记名区分**可诊断性**

### 8.6.3 兼容性保证

| 约束 | 保证 |
|---|---|
| 现存活实例（6 字段含非标准字段） | ✅ **F1 实测放行**；新校验**只查必需 3 项**，未知字段一律忽略 |
| `occupied=false` 合法声明 | ✅ 不受影响（E1 实测） |
| S1 文件不存在 | ✅ 行为不变 |
| 新标记可 grep | `[OCC-INVALID]`（语义非法）/ `[OCC-FAILCLOSED]`（解析失败）/ `[OCC-NO-OWNER]`（缺 owner 提示） |

### 8.6.4 验收判据（实施时用）

| # | 用例 | 期望 |
|---|---|---|
| V1 | `{}` | `gate=skip-occupancy` + `[OCC-INVALID] occupied 字段缺失` |
| V2 | `occupied:"yes"` | `skip-occupancy` + `[OCC-INVALID] 类型非法` |
| V3 | `window` 4 元素 | `skip-occupancy` + `[OCC-INVALID] 元素数=4` |
| V4 | `window` 反向 | `skip-occupancy` + `[OCC-INVALID] 反向` |
| V5 | `occupied:false` 合法 | `all-pass`（不变） |
| V6 | 现存活实例原样 | `all-pass`（不变，兼容） |
| V7 | 文件不存在 | `all-pass`（不变） |
| V8 | 合法 true + 窗口内 | `skip-occupancy`（不变） |
| V9 | JSON 损坏 | `skip-occupancy` + `[OCC-FAILCLOSED]`（不变） |

### 8.6.5 状态：**已实施**（2026-11-13 10:24，R6 ②审有条件通过后）

**实施摘要**：`scripts/check_daemon_alive.ps1` F3 段整块替换（582 → **674** 行），新 sha256 `87BF5207548B1EB8A9B65155B4BF65D79B38ABDD2C9236F8EF6F7EA4DD0CD480`；
写前锚 `docs/handoff/anchor-daemon-alive-F3v2-20261113.txt`（原 blob `4136d0f6…`）。`PARSE OK` + BOM 保持。

**R6 六项条件落实**：
| # | 条件 | 落实 |
|---|---|---|
| 1 | 同一次读取字节用于 parse 与 hash；null 诊断安全 | ✅ `[System.IO.File]::ReadAllBytes()` **一次读取** → 同一 bytes 算 sha256 + UTF8 解码 parse；`$shaShort` 缺省 `'n/a'`，`$null -eq $occVal` 单独判 |
| 2 | 非法 occupied/多元素/反向 fail-closed；有效 false/不存在/合法窗内外/未来/跨午夜/true 缺 window 不退化 | ✅ V1–V4 新行为、V5–V13 全 PASS |
| 3 | 数字 1/字符串 true 偏保护，改判 invalid 也须续阻拉 | ✅ 二者仍判 `isTrue`（不误放行）；V14/V15 PASS |
| 4 | 撤回「全仓无 writer ⇒ 只需兼容一个实例」 | ✅ 已改写为「仓内扫描不能排除外部写方」+ 保守口径 |
| 5 | 无标记轮不得当修好关闭 episode | ✅ episode 闭合改为「实际进 F3 成功校验」或「明确修复回执」 |
| 6 | 不改阈值/闸序/任务/原 active 观察 | ✅ 仅替换 F3 内部实现，闸序与阈值零改动 |

**V1–V16 隔离实测（全部 PASS）**：
| 组 | 用例 | 结果 |
|---|---|---|
| 新行为 | V1 `{}` / V2 `"yes"` / V3 4 元素 / V4 反向 | 全部 `skip-occupancy` + `[OCC-INVALID]` 可诊断原因 |
| 回归不变量 | V5 `false` / V6 现存活实例 / V7 不存在 / V8 窗内 / V9 JSON 坏 | V5/V6/V7 `all-pass`；V8 `skip-occupancy`；V9 `[OCC-FAILCLOSED]` |
| 边界保护 | V10 true 缺 window / V11 跨午夜 / V12 未来 / V13 过去 | V10/V11 `skip-occupancy`；V12/V13 `all-pass`（窗外） |
| 不误放行 | V14 数字 1 / V15 字符串 "true" | 均 `skip-occupancy`（不误放行） |
| 兼容 | V16 owner 缺失 + 额外字段 | `skip-occupancy`（额外字段被忽略） |

**首轮 5 例 FAIL 的归因（如实记录，非被测脚本缺陷）**：
- `[string]` 型参数传 `$null` ⇒ **变空字符串** ⇒ V7「文件不存在」实际写入了 0 字节文件 ⇒ 落到解析失败分支；
- PowerShell 无括号的多段拼接会被**拆成多个实参** ⇒ V15 的 JSON 被截断在第 30 字符；
- V5/V12/V13 的判据过严（要求 detail 含特定串，而 `all-pass` 路径 detail 被后续覆盖）——gate 本身正确。
⇒ 修正测试脚本后**5 例全部复验 PASS**。

## 8.7 不变项（审核明确）

- 原 active 观察窗（20:50 → 11-13 20:50）**不变**
- H3 根因**继续未知**，不倒推
- `LastResult=2` **保留 WARN 分级**
- 本轮**不改任务、阈值或启停进程**
