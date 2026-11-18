# A4 轻方案送审材料 · 仅新增一个字符串 provenance 键（2026-11-17）

> **送审对象**：DRIFT-N7-r10「A4 仅新增一个字符串 provenance 键。轻方案列精确 JSON 路径与 hash、旧八键逐值不变、新键逐字值、读取链、消费者、判定不翻转的核法、失败回退、三审计及知识留痕」。
> **性质**：**方案与独审准备**。**未改任何文件**（JSON／signatures／Validator 均未动）；**未平台探测、未 D5、未订单、未新测试、未 Git**。
> **配套规范**：[`a-track-is-trade-doc-basis-spec-20251117.md`](a-track-is-trade-doc-basis-spec-20251117.md)（r9，**12,555 B**／`e514239286fc365a200054b9880ce884e10d2d7ed4bde235773da7787044ed74`）。
> **采样时刻**：**2026-11-17T07:16:20Z**
> **R11 修订与送审时刻**：**2026-11-17T13:25:39Z**（S-6 前置闸补丁版；行数 +12 −5）

---

## 一、精确 JSON 路径与 hash（三件套同刻）

| 项 | 值 |
|---|---|
| **相对路径** | `skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json` |
| **绝对路径** | `D:\miniQMT策略实盘\QuantStudio\skills\quantstudio-strategy-compiler\references\ptrade-api-signatures.json` |
| **字节** | **33,586 B** |
| **sha256（改前基线）** | **`1b4b11ff4ac6e033432ef8e1508076030a8b7bac88c6c08b872f1497edff3b37`** |
| mtime | 2026-09-08 15:50:05 |

⚠️ **本 hash 为「改前基线」**；A4 实施后须以新 hash 复核，并**同刻**重新采 size／sha256／mtime。

## 二、旧八键**逐值不变**（核法可机检）

现行 `runtime_corrections` **8 键**（JSON 原文键序），逐值 sha256 基线：

| # | 键 | 值长度 | 值 sha256（前 32） |
|---|---|---|---|
| 1 | `get_open_orders` | 135 | `4a2ff6102a1d38780ccbd30ee9eb7dc4` |
| 2 | `source_checked_on` | 10 | `915dfa5e6c67744847d4166e3dc977a4` |
| 3 | **`set_backtest_is_trade`** | 168 | `4a001569302a2557d0e8e095e7eae822` |
| 4 | `log_warn` | 167 | `d47a0bdac29d6baff43bb9ce9567ee5a` |
| 5 | `numpy_pandas_injection` | 201 | `57dec40b83d8303380072b60e01620a6` |
| 6 | `core_stock_api_profile` | 208 | `7349bd03d60e7a39381bbf7d97fd054a` |
| 7 | `get_stock_status_delisting` | 144 | `9f18467bf73a5900edfd938943f0c904` |
| 8 | `get_history_return_shape` | 423 | `1aa2af540803d8c6703d508e8f8a9c54` |

**核法（机检，二选一或并用）**：

1. **逐值核**：A4 后重新计算上表 8 行「值 sha256」，**必须逐行相同**（尤其第 3 行 `set_backtest_is_trade`）。
2. **整块归一化核**：对 `runtime_corrections` 取 `json.dumps(…, sort_keys=True, separators=(",",":"))` 后 sha256
   —— **改前基线 = `ece4e22d532047a53621e11b75f63b39b61923fc60e0bac4c5fc3ad1e5ec8e39`**；
   ⚠️ A4 后该值**必然变化**（新增 1 键）⇒ 该核法**只能用于「改前记录」与「改后仅差 1 键」的差量证明**，
   **不能**用作「八键不变」的直接判据。**八键不变须用第 1 法（逐值 sha256）**。

⚠️ **旧 T3 保留**：`set_backtest_is_trade` **原字符串一字不改**（第 3 行 sha256 不变即为证）。

## 三、新键**逐字值**（定稿）

| 项 | 值 |
|---|---|
| 键路径 | `runtime_corrections.is_trade_doc_basis` |
| 值类型 | **`str`** |
| 成员数变化 | `runtime_corrections` **8 → 9** |
| **逐字值** | 见 [r9 规范 §二](a-track-is-trade-doc-basis-spec-20251117.md) **原样引用，不在此处另存副本**（避免两处漂移） |

⚠️ 该值**必须**包含且不得替代的三段（依 r9 R-3）：
①`CONVERTER BEHAVIOUR BASIS (primary)` —— 既有 KEEP ＋ 本批删除不可达死映射；
②`DOCUMENTATION BASIS` —— 四锚点；
③`REGISTRY BASIS SUPERSESSION … FOR is_trade ONLY` —— 仅登记依据。

## 四、读取链（实测，采样 2026-11-17T07:16:20Z）

| # | 环节 | 实测 |
|---|---|---|
| 1 | 文件引用 | `quantstudio/strategy_compiler/portability_rules.py` **L62**（引用文件名）；`quantstudio/strategy_compiler/source_import.py` **L4660**；`skills/…/scripts/validate_agent_strategy.py` **L240** |
| 2 | 加载函数 | `validate_agent_strategy.py` **L239 `_load_ptrade_profile() -> dict[str, Any]`**（唯一显式加载器） |
| 3 | 取用 `runtime_corrections` | **上述三文件「读取 runtime_corrections 的行：无」** ⇒ **读取链上无人取该键** |
| 4 | 结论 | `runtime_corrections` 在本仓**读取链为「仅被解析、无人取用」**；新增 1 键**不进入任何判定路径** |

## 五、消费者（五面覆盖，实测）

| 面 | `runtime_corrections` 命中 |
|---|---|
| `quantstudio/` | **0** |
| `skills/` | **1**（仅 JSON 自身） |
| `scripts/` | **0** |
| `tests/` | **0** |
| skill 脚本 `validate_agent_strategy.py` | **不读**（仅读 `local_only_symbols`，见 L762/L988/L995/L1005） |
| validator `validate_ptrade_portability.py` | **0**（不直接读该键） |

⇒ **A4 的消费者集合在已核范围内为空集**。
⚠️ **边界**：**范围内 grep 无命中不证明仓外兼容**（不覆盖仓外消费者、未来消费者、运行时动态读取）。

### 5.1 schema 结论（**准确限定**）

| 项 | 结论 |
|---|---|
| 实测 | JSON 内 `$schema` **0 次**、`additionalProperties` **0 次**；引用该 JSON 的 **7 个 `.py` 均无 jsonschema／schema 关键字命中** |
| **可声称的（本版统一措辞）** | **「已读消费者段未见额外键拒绝」** |
| ⚠️ **不可声称的** | ❌「本仓绝无手写校验」——关键词零命中**不能**证明不存在**非关键词形式**的手写校验（如硬编码键白名单、`len()` 断言、显式 `keys()` 比对） |
| ❌ **更不可声称** | ❌「必然不会拒绝」——**无拒绝处理 ≠ 已被证明不会拒绝** |
| **已读范围（据此声称）** | ①`validate_agent_strategy.py` **L239-240** `_load_ptrade_profile()` 及 **L762／L988／L995／L1005** 四处 `local_only_symbols` 消费段；②`portability_rules.py` **L62** 引用行；③`source_import.py` **L4660** 引用行。**上述已读段内未见『额外键拒绝』处理** |
| **未读部分不作断言** | 其余引用文件的**非已读段**、仓外消费者、运行时动态读取**均未核** ⇒ **不据此推断「不会拒绝」** |

## 六、**判定不翻转的核法**（可机检）

A4 只新增 `runtime_corrections` 的 1 个键，**判定面不得变化**。核法＝比对下列三项基线：

| # | 判据 | 改前基线（实测） | A4 后要求 |
|---|---|---|---|
| **J-1** | `len(local_only_symbols)` | **27** | **= 27**（不变） |
| **J-2** | `"is_trade" in local_only_symbols` | **True** | **= True**（不变） |
| **J-3** | `"is_trade" in signatures` | **False** | **= False**（不变） |

**补充机检**：

- **J-4** `validate_agent_strategy.py` 三规则（`PTRADE-LOCAL-SYMBOL` L762／`PTRADE-DESIGN-LOCAL-API` L995／`TARGET-LOCAL-EXTENSION-BAN` L1005）**对同一输入仍 BLOCK** —— 核法：对含 `is_trade()` 的最小样本跑该脚本，**判据输出与改前逐字一致**。
- **J-5** `runtime_corrections` 键集合 = 改前 8 键 **∪** {`is_trade_doc_basis`}，**无其他增删**。
- **J-4／失败出口／临时写域并入同一实施范围**（§七），**随本方案同版送审**，**不逐样本索批**。
  - **J-4 核法（实施轮执行；四项同时满足才算过）**：
  0. **先落盘两个固定样本**：按 A-3 所列绝对路径写入 `a4-j4-fixed-sample/{design.json, source.py}`，内容严格取下方第 1 条所列文本；落盘后核 sha256 并登记。
1. **同一固定样本**——实施轮必须使用下列两个文本，内容、键值和调用形态不得临时更换：
     - design JSON：
       {"design_version":"2.0","strategy_id":"a4_is_trade_fixed_sample","targets":["quantstudio","ptrade"],"engine_profile":{"profile_id":"minute-bar-v1","bar_frequency":"1m","match_price_mode":"close"},"timing":{"signal_data_cutoff":"current completed 09:31 minute bar","holding_semantics":"fixed","decision_events":[{"name":"rebalance","lifecycle":"run_daily","time":"09:31"}]},"components":{"lifecycle_hooks":["initialize"],"required_apis":["is_trade"]},"constraints":{"portable_source_required":true}}
     - source Python：
       def initialize(context):
           if not is_trade():
               set_backtest()
  2. 对上述固定 design/source 运行 skills/quantstudio-strategy-compiler/scripts/validate_agent_strategy.py，三规则 PTRADE-LOCAL-SYMBOL／PTRADE-DESIGN-LOCAL-API／TARGET-LOCAL-EXTENSION-BAN 三项均 BLOCK（缺一项即不算过）。
  3. **判据输出改为完整报告 JSON 的 SHA-256**：改前与改后各保存完整 out JSON 字节文件，分别计算 SHA-256；两次 SHA 必须相等，并同时核三规则逐项 BLOCK。
  4. ⚠️ **未到达判据也不算过**——若脚本未实际走到某规则（样本未触发该路径／规则被跳过／该规则输出缺失），该项记为**未验证**，**不得记 PASS**。
  - **C-1 只读 Git 采集清单（实施前/写前）**：固定 CWD 为 D:/miniQMT策略实盘/QuantStudio；仅运行 git diff HEAD -- 目标JSON、git log -1 --format=%H、git status --porcelain -- 目标JSON，并将 HEAD、status、diff 摘要与 S-1 原字节 SHA 同刻登记；目标 JSON 为已锁定的 ptrade-api-signatures.json。若 status 出现 ?? 未跟踪文件，git diff 为空不得视为无改动；状态不符合预期即停手。禁止 add、commit、push、stash和任何回退命令。
  - **C-3 原位编辑前后核对（X-3）**：原位写入前重新读取并核对 S-1 完整原字节 SHA；禁止用 S-1 快照整文件覆盖现文件，恢复只能反向应用本批唯一差量；写入后立即核当前字节为 S-1 原件加本批唯一差量，不一致即停核，不进入 S-4。改前/改后完整报告 JSON 必须与 S-1 同刻登记链绑定，时间或锚不一致即停核。S-5 整件覆盖路径在本版明确禁止，需另案才可讨论。**【D-1 时序与 run 前核验】**：before run 于 **S-2 立验通过后**、A4 写入前执行，run 后至写入前无其他动作；**run 紧前**实测目标 JSON sha256 == `1b4b11ff4ac6e033432ef8e1508076030a8b7bac88c6c08b872f1497edff3b37`（S-1 基线），不符即停手。
  - **A-3/X-2 固定执行环境与命令字面**：CWD、样本和报告路径固定，不依赖自动发现链。执行轮固定使用 CWD D:/miniQMT策略实盘/QuantStudio，并使用以下绝对路径和相同参数形态：
    - 设计样本：D:/miniQMT策略实盘/QuantStudio/docs/evidence/pd0020-wt-baseline-20261113/a4-j4-fixed-sample/design.json
    - 源码样本：D:/miniQMT策略实盘/QuantStudio/docs/evidence/pd0020-wt-baseline-20261113/a4-j4-fixed-sample/source.py
    - 改前报告：D:/miniQMT策略实盘/QuantStudio/docs/evidence/pd0020-wt-baseline-20261113/a4-j4-results/before.json
    - 改后报告：D:/miniQMT策略实盘/QuantStudio/docs/evidence/pd0020-wt-baseline-20261113/a4-j4-results/after.json
    - 固定命令：python -B D:/miniQMT策略实盘/QuantStudio/skills/quantstudio-strategy-compiler/scripts/validate_agent_strategy.py D:/miniQMT策略实盘/QuantStudio/docs/evidence/pd0020-wt-baseline-20261113/a4-j4-fixed-sample/source.py --design D:/miniQMT策略实盘/QuantStudio/docs/evidence/pd0020-wt-baseline-20261113/a4-j4-fixed-sample/design.json --target-profile ptrade --project-root D:/miniQMT策略实盘/QuantStudio --out D:/miniQMT策略实盘/QuantStudio/docs/evidence/pd0020-wt-baseline-20261113/a4-j4-results/before.json；改后仅将输出换为 after.json。
    - **E-2 实施窗口稳定硬约束**：从 S-1 到报告 SHA 核验完成期间，目标 JSON、固定样本、报告目录和项目树不得被第三方增删；**【D-2 判定方法＝四点小指纹】**每次 run **紧前、紧后**各取一次：①目标 JSON sha256；②`<root>/output/generated_strategies/source/alignment/report.md` 的**不存在性**；③`<root>/quantstudio/backtest/strategies/source/alignment/report.md` 的**不存在性**；④两个固定样本 sha256。两次四点须逐项相同；另起始/结束记录 `status` 和 HEAD。⚠️ 仅取起止两点 status/HEAD **检不出窗口内瞬态增删**（第三方在两次 run 之间创建又删除 alignment 候选文件时，端点双净而 issues 已变）。发现外部变化即停核，不把报告差异归因给A4。
  - **J-4 失败出口**：若任一规则不再 BLOCK ⇒ **立即回退本次 A4 改动并停核上呈**（说明 BLOCK 已被意外解除，属计划外行为变化）。
  - **临时写域**：`.git/a4-pre-write/20261117/`（`.git/` **不被 git 跟踪** ⇒ 不污染工作树、不入库），用于保存写前原字节；**留存至 A4 验收通过后再清理**。

## 七、失败回退（**本版重写 · 修正真实缺口**）

> ⚠️ **上版缺口**：原写「回退动作＝`git checkout -- <该 JSON>`」——**这是回到 HEAD，不是回到写前原件**；
> 且以「`git diff` 为空」冒充「已恢复写前状态」。**本版作废该写法**，改为**原字节保全 ＋ 精确差量恢复**。

### 7.1 回退链（六步，缺一不可）

> **执行顺序：S-1 → S-2 → S-3 → S-6 → S-4**。S-5 整件覆盖路径本版禁止。
> ⚠️ **S-6 是 S-4 的前置闸**：必须先确认「当前原字节 == 写前原件 ⊕ 本次差量」，不符即先停；不得先动手、再靠写后哈希才发现误动他线。

| 步 | 动作 | 判据 |
|---|---|---|
| **S-1 原字节保全（写前）** | 改动**前**把该 JSON **完整原字节**复制到**临时写域**：`.git/a4-pre-write/20261117/ptrade-api-signatures.pre.json`；同刻记录 `bytes`／`sha256`／`mtime` | 三件套**同刻**采集（禁跨命令拼接） |
| **S-2 立验（必做）** | 读回临时副本，核 **`sha256(副本) == sha256(源文件当刻)`**，且副本可 `json.loads` | **不通过即停手**——**不建「用不了的回退点」** |
| **S-3 差量可解释** | A4 仅「新增 1 个 `str` 键」；`git diff --stat` 须**仅 1 文件**，`+` 行须**仅该键**（可完整解释） | 出现其他改动 ⇒ **停核** |
| **S-4 回退＝只恢复本次精确差量** | ⚠️ **前置：S-6 已通过**（未通过**不得动手**）。把「新增的那 1 个键」**从现文件中删除**（等价于反向应用本批精确差量），**不触碰其他任何字节** | 恢复后文件 `sha256` **须等于 S-1 原件 sha256** |
| **S-5 定向恢复写前原件（本版禁止）** | **不得用 S-1 原件整文件覆盖现文件**；如需该路径必须另立方案、独审和用户批准 | 本版不执行、不作为回退出口 |
| **S-6 他线叠加判定（S-4前置闸）** | 同时满足两条才可进入 S-4：①`git diff HEAD -- 目标JSON` 与 `git status --porcelain -- 目标JSON`（**status 须限定路径**）均只含本批差量；`a4-j4-fixed-sample/`、`a4-j4-results/` 下的 `??` 产物属**本批白名单**，不触发误停；②当前字节 == 写前原件 ⊕ 本批差量 | 任一不满足即停，不覆盖、不合并、不stash |

### 7.2 **禁止事项（本版明确）**

| # | 禁止 | 理由 |
|---|---|---|
| **X-1** | ❌ 以「`git diff` **为空**」冒充「**已恢复写前状态**」 | `diff` 空只说明**等于 HEAD**；**HEAD 可能本就不等于写前原件**（多线共享下 HEAD 也可能在期间移动）⇒ 「恢复写前状态」的唯一判据是 **sha256 == S-1 原件** |
| **X-2** | ❌ 用 `git checkout -- <该JSON>` **回 HEAD** 冒充恢复 | 同上；且会**静默丢弃**写前工作树中可能存在的他线改动 |
| **X-3** | ❌ `git stash` | 多线共享工作树下 `stash` 会**吞他线未提交改动**（项目已实测事故） |
| **X-4** | ❌ `git reset --hard` / `git checkout .` / `git clean` 等**批量回滚** | 同上，波及他线 |
| **X-5** | ❌ 回退时**顺手**改其他文件 | 回退范围**仅限该 JSON** |

### 7.3 回退判据（触发即回退并停核）

| # | 判据 |
|---|---|
| T-1 | **J-1**（`len(local_only_symbols)` = 27）变化 |
| T-2 | **J-2**（`"is_trade" in local_only_symbols` = True）变化 |
| T-3 | **J-3**（`"is_trade" in signatures` = False）变化 |
| T-4 | 旧 **8 键**任一值 sha256 变化（尤以 `set_backtest_is_trade` 为准） |
| T-5 | `json.loads` 失败 |
| T-6 | `runtime_corrections` 键集合出现**计划外**增删（非「8 ∪ {is_trade_doc_basis}」） |
| T-7 | **J-4 失败出口**：validator 三规则任一**不再 BLOCK** |

⇒ 任一命中：先过 S-6；通过后只按 S-4 反向应用本批精确差量并核 sha，随后停核上呈；S-6不通过不得回退。

### 7.4 临时写域（并入实施范围）

| 项 | 内容 |
|---|---|
| 位置 | `.git/a4-pre-write/20261117/` |
| 是否入库 | **否**（`.git/` 不被 git 跟踪）⇒ **不污染工作树、不进 22 件、不需提交** |
| 内容 | `ptrade-api-signatures.pre.json`（**完整原字节**）＋ `pre-fingerprint.json`（bytes／sha256／mtime／采样时刻） |
| 立验记录 | S-2 结果写入 `pre-fingerprint.json`（`verify_ok: true/false`） |
| 清理时机 | **A4 验收通过后**再清理；未通过前**保留** |

## 八、三审计（铁律 18）

| # | 审计项 | 判定 | 依据 |
|---|---|---|---|
| **①** | 影响其他功能 | **否** | 新增键**无消费者读取**（§四／§五）；`local_only_symbols`／`signatures`／Validator／skill 脚本**均未改动** ⇒ J-1～J-3 不变 |
| **②** | 影响回测性能 | **否** | 该 JSON 在加载期解析一次，文件增约 1 KB；**不在回测热路径**；不进入任何计算 |
| **③** | 影响回测精度 | **否** | 纯 provenance 文本，**不参与任何数值/信号/撮合/费用计算** |
| **结论** | 三项全否 ⇒ 可走**轻通道** | | **分型**：**元数据增量型**（不改变任何 verdict）——⚠️ 与「新增检测型」**不同**：本项**不使任何 PASS→FAIL 或 FAIL→PASS** |
| ⚠️ | 若实施中 J-1～J-3 任一变化 | **立即停手并按契约变更单独立项** | 铁律 18 |

## 八·五、文档维护与**唯一写者**约定（U180-2 裁定落位）

> **裁定依据**：U180-2「A/B/C/D/E 全甲」→ 决策 D 甲项「写入方案正文唯一写者约定，唯一写者为节点 7」。

| 项 | 约定 |
|---|---|
| **唯一写者** | **节点 7**（PTrade 平台回测转模拟盘和实盘执行会话） |
| 适用范围 | 本方案正文（`a4-is-trade-doc-basis-light-plan-*.md`）及其改后 SHA 登记 |
| 他线修订程序 | **须先与本节点交接并书面确认**，不得并行直写；未交接即写视为冲突，按停核上呈处理 |
| 冲突判定 | 同一文件出现两方改动（`git diff` 混入非本批差量）⇒ **立即停手、不覆盖、不合并不 stash**，上呈裁定 |
| 历史说明 | 本正文历史版本曾由他线 U168／U169／U170 修订；自本节起写者权归节点 7 |
| 变更留痕 | 每次改写须在本节下方追加一行「日期／改动人／改动摘要／改后 sha256」 |

### 变更留痕

| 日期 | 改动人 | 改动摘要 | 改后 sha256 |
|---|---|---|---|
| 2026-11-18 | 节点 7 | 合入 D-1～D-4（C-3 时序句／E-2 四点小指纹／S-6 status 限定＋白名单／J-4 第 0 步）＋ 建立唯一写者约定 | 见本次改后登记 |

## 九、知识留痕（R1）

```
知识反哺：[命中] knowledge\platform\ptrade\01-API文档主页\001-page.md, knowledge\platform\ptrade\01-API文档主页\002-index.md, knowledge\platform\qmt\05-数据字典\081-scenario_based_example.md
```

| 项 | 内容 |
|---|---|
| 检索面 | `knowledge/**`（按族 + domain），关键字 `is_trade`／`runtime_corrections`／`local_only_symbols` |
| 命中文件数 | **5** |
| 命中清单 | `knowledge\platform\ptrade\01-API文档主页\001-page.md`<br>`knowledge\platform\ptrade\01-API文档主页\002-index.md`<br>`knowledge\platform\qmt\05-数据字典\081-scenario_based_example.md`<br>`knowledge\ptrade-deploy\PD-2026-0012.md`<br>`knowledge\ptrade-deploy\PD-2026-0022.md` |
| 采纳要点 | 见 r9 规范 §二（T1 四锚点／T3 合并转述／T4 先例） |

## 十、A5 另案声明（**边界**）

| 项 | 内容 |
|---|---|
| A5 内容 | 解除 BLOCK（`local_only_symbols` 移除 ＋ `signatures` 补条目 ＋ skill 三处） |
| 与 A4 关系 | **A5 不要求先完成才允许准备 A4**；**A4 批准也不解除任何 BLOCK** |
| A5 前置 | 见 r9 规范 §六 P-1（T2 实测）／P-2（签名）／P-3（行为）／P-4（影响审计） |
| T2 缺失 | **不得以「接受风险」代替** |
| 本批 | **不涉及 A5** |

## 十一、本批未做与待办

| 项 | 状态 |
|---|---|
| 改 JSON | **未做** |
| 改 signatures／Validator／skill 脚本 | **未做** |
| 平台探测／D5／订单／生产 | **未做** |
| 新测试 | **未做**（J-4 仅登记核法） |
| Git | **未做** |
| **待你确认** | A4 实施范围（是否即「1 键、`str`、8→9 成员」）；schema 结论措辞是否认可；J-4 是否留待实施轮 |
