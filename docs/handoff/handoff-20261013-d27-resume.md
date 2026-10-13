# 交接件：QMT 管线 D-23/D-24/D-26/D-27a → 新会话续跑（2026-10-13 00:20）

> **新会话首读顺序**：本文件 → `docs/PROJECT_STATE.md`（末节快照）→ `docs/ISSUES.md`（D-23~D-27）
> → `docs/evidence/qmt-d26-hist-perf-evidence.md`（D-26 全卷证据）
> **用户指令**：本会话上下文耗尽，用户将**新开会话继续**。

---

## 0. 一句话状态

**QMT 端已从「零交易」修到「全漏斗贯通并产生交易」**（`R_selected=10` / `buy_submitted=7` / 总资产 1,003,261.49）；
三卷（D-23/D-24/D-26）端测实证；**本地已提交 `e269fc8`、未 push**（用户裁定 B：并入明晨呈批轮统出批）；
**明晨轮待办 4 项**见 §5。

---

## 1. 环境与基线

| 项 | 值 |
| --- | --- |
| 工作目录 | `D:\miniQMT策略实盘\QuantStudio`（分支 `main`） |
| Python | `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe` |
| HEAD | **`e269fc8`**（三卷提交，未 push） |
| 与远程 | `main...origin/main [ahead 11]`（他线 8~9 提交 + 本提交） |
| 矩阵哈希 | `qmt_wrapper_hash=a3719f737dfb`（`--check` 绿） |
| 当前回退点 | **`da21e0d`**（D-27a 快照 `baseline-20261012-d27a-anntime`） |
| 回退点链 | `9912f07`(D-26) → `2266e36` → `bdd7efb`(D-26b) → `b23e724`(D-26c) → `26cf345`(D-26d) → `da21e0d`(D-27a) |

### 关键命令
```powershell
# 回归（70 用例）
& $py -m pytest tests/test_source_import_qmt.py tests/test_qmt_progress_audit.py tests/test_qmt_hist_vectorize.py -q
# 六策略重转门
& $py scripts/qmt_reconvert_six.py
# 矩阵哈希（模板改动后必须同批追认）
& $py scripts/check_fund_matrix.py --check --reverify ; & $py scripts/check_fund_matrix.py --check
# 写前快照四步配方（git stash create 不接受 -u/-m 的坑已固化）
git add -A 2>$null    # ⚠ 本仓 agent_workspace/_wt_* 嵌入仓库会使 add 超时(>10min)，
                      #   实测可行路径：先清 .git/index.lock，再直接 git stash create
$h = git stash create "baseline-<ts>" ; git stash store -m "baseline-<ts>" $h ; git reset -q
```

---

## 2. 已完成卷（全部端测实证）

| 案 | 内容 | 判型 | 关键读数 |
| --- | --- | --- | --- |
| **D-23** | `get_stock_info` 补本地契约键 `listed_date`/`de_listed_date`（新增 `_qs_norm_ymd`） | 数据修正型 | `L1_listed` **0 → 4410** |
| **D-24** | 取数层进度审计行（`_qs_progress` 系列；5 落点；`get_financial_data` 起止 `finally` 配对） | 新增检测型 | 全程无静默段 |
| **D-26** | `_qs_fin_to_rows_hist` 性能与结构（4 轮：批量 → 回落 → 结构探针 → 契约对齐） | 新增检测型→**纯性能型** | hist **509s → 112s**；全漏斗贯通 |
| **D-27a** | `m_anntime` 收敛尝试（**自保护版**） | 纯性能型 | ⚠️ `kept` 未变＝**无行被删**（`m_anntime` 全非空或全 NaN **二义**，S3 更正 2026-10-13：原「QMT 对 m_anntime 亦做日频填充」为证据不足断言已废）→ 收敛未生效，**但正确性零漂移**（`R_selected=10` 保持） |

**终极判据（2026-10-13 00:12 端测）**：
```
QS_FUNNEL_AUDIT L0=5227 L1_listed=4410 L2_board=3816 L3_status=3666 L3v_complete=3663
                L4_growth=366 L5_smallcap=30 L6_eps=17 R_rankable=16 R_selected=10 note=ok
QS_REBALANCE_AUDIT selected=10 tradable=10 buy_submitted=7
QS_PORTFOLIO_AUDIT positions=7 cash=308609.49 total_value=1003261.49   （单周区间 572 秒）
```

---

## 3. 关键技术事实（勿重新摸索）

### 3.1 QMT 真实结构（D-26c 探针定谳）
```
raw = QMT 自定义 Panel（非 pandas Panel）
raw.to_frame() → index = MultiIndex(major=日期, minor=字段)，columns = 股票代码   ← 方向与直觉相反
raw[code]      → DataFrame(8739 行 × 3 列)，index = 日期（19901219…，日频全历史）
字段名（native）：growth_ability → inc_revenue_rate / m_anntime / m_timetag
                 eps            → s_fa_eps_basic   / m_anntime / m_timetag
```
- `.loc[code]` **必然 miss**（index 首层是日期）→ 只能用 `raw[code]`。
- **8739 行/只** = 日频全历史 vs 「PIT 全报告期」~40 期 → **超量 ~200×**。

### 3.2 消费端 mask（判型依据，四大主策略全 mask）
```
weekly_smallcap :149 mask = ~np.isnan(val) ／ :158 frame = frame[mask]
周频三层        :145 mask = ~np.isnan(val)
低流动性溢价    :121 mask = ~np.isnan(val)
CANSLIM         :394 dropna(subset=['code','end_date','eps']) ／ :490 notna()
```
→ 预删 value=NaN 行 ⟹ 消费结果不变（**纯性能型**）。

### 3.3 性能画像（单周区间 572s）
| 段 | 耗时 | 状态 |
| --- | --- | --- |
| `query_last_close` 3666 只 | **144s** | ⏳ D-27 第 2 项（批量化）待做 |
| `fin_to_rows_hist` ×2 | 112.6 + 113.8 = **226s** | ⏳ D-27b 待做 |
| `get_financial_data` ×3 | 33 + 55 + 56 = **144s** | QMT 端，不可控 |
| L1+L3 | ≈0.5s | 已达最优 |

`QSFUNDHIST`：`kept=9718706 drop=9168`（growth）／`kept=9692826 drop=35048`（eps）。

### 3.4 血泪教训（已固化）
1. **self-made mock 无法代替真实结构**：D-26 本地黄金对照（fake Panel，Index=code）全绿，真实环境 `.loc` 全 miss → `kept=0` → 零交易事故（白跑一轮端测）。
2. **探针禁忌**：禁 `x or []` 作用于 pandas Index（`truth value of a Index is ambiguous`）；禁 `list(完整 index)`（千万行）。
3. **`git stash create` 不接受 `-u`/`-m`**；本仓 `git add -A` 会因嵌入仓库超时 → 先清 `.git/index.lock` 再用 `stash create`。
4. **自保护设计有效**：D-27a 的「仅当被删行 value 全 NaN 才收敛」成功拦下一次潜在结果漂移。**S3 更正（2026-10-13）**：`kept 未变 = 无行被删（全非空或全 NaN 二义），未获收敛效果`——不能据此断言「QMT 对 m_anntime 做日频填充」；定谳依赖 D-27b 探针 `QS_QMT_HISTDBG3` 的 `ua/uk` 读数（已实施，候端测）。

---

## 4. git 状态

- **已提交**：`e269fc8`（15 项精确清单：`source_import_qmt.py` +213/−8、矩阵 yaml/md、ISSUES/STATE、3 份证据 + 1 份设计、3 个测试文件、README + 2 份 docs 同步）—— **未 push**。
- **已修改未提交**：D-27a 改动（`source_import_qmt.py` + 矩阵 yaml/md 哈希 `a3719f737dfb`）—— **待明晨轮与推送一并处理**。
- **他线在途（禁触碰）**：`AGENTS.md`、`docs/AGENT-PRESET-dev-optimizer.md`、`quantstudio/backtest/strategies/`（首板线）、`scripts/*`、`config/*` 等 200+ 项。
- **禁提交**：`docs/evidence/_bak/`（本轮各版本备份，临时）。

---

## 5. 明晨轮待办（按序）

1. **archify 影响面图**（§10 必做；D-24/D-26 卷补件）
   - 环境预检**已过**：Chrome 三处均无 → **走 Edge** `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`（设 `ARCHIFY_CHROME`）
   - skill 位置：`C:\Users\Administrator\.agents\skills\archify`
   - 类型：`workflow`（取数链四/五落点调用流）；产出后随 Plan 第⑧项过审
2. **D-27b 探针**：查 `m_anntime` / `m_timetag` 的**唯一值数量与分布**
   - 目的：定收敛键（候选：`(m_anntime, m_timetag)` 去重 → 8739 → ~40 行/只）
   - 若两者亦为日频填充 → 第四方案：按 **value 变化点分段**
   - **必须沿用自保护**（被删行 value 须全 NaN 才生效）
3. **D-27 第 2 项**：`query_last_close` 批量化（3666 次 `get_market_data_ex` → 批量）
   - ⚠️ 改动前**必须先用 Context7 查文档**（`/websites/dict_thinktrader_net`）复核 `get_market_data_ex` 批量语义（项目铁律）
4. **⑥双仓推送收口**（用户已批准推送三问：①推双仓 ②同步 README+toolbox+prompt_engineering ③走 QuantStudio-trading 同步门）
   - ⚠️ 但用户同时裁定 B：**并入明晨批统出批**——推送将连带他线 `ahead` 提交，**推送前再次向用户确认**
   - 推送后：核对两远程 HEAD 逐位一致 + QuantStudio-trading `fetch/merge origin/main` + check-drift 九项 + ci-smoke 共享层回归段

---

## 6. 待用户裁定 / 未闭合

| # | 项 | 状态 |
| --- | --- | --- |
| 1 | D-27b 收敛键选择 | 待探针取证后呈批 |
| 2 | `query_last_close` 批量化是否单独成轮 | 待裁定 |
| 3 | wrapper 键名差异（`stock_name`/`stock_type`/`exchange_type`/`code`） | D-23 登记 P2 |
| 4 | 件4 部署器重设计（"拷明文 .py" 已证伪） | 候呈批 |
| 5 | `python\_qs_plaintext_bak\`、`indexUserConfig.xml.bak-20261012-m52` 清理 | 候答复 |
| 6 | Obsidian 主库回灌（QMT 加密存储/剪贴板路线 + mock 陷阱 + 自保护范式） | 待做（配套规则 4/6） |

---

## 7. 铁律要点（本任务适用）

- **六步流水线**：方案 → 审计 → 实施 → 验收 → 用户确认 → 双仓推送（禁跳步；推送前必须用户点头）。
- **策略源码零改动**：修复只落框架层（`source_import_qmt.py` 模板），禁改 `quantstudio/backtest/strategies/` 与 `output/qmt_import/` 产物。
- **矩阵哈希同批**：改 wrapper 渲染常量 ⟹ 同 commit 含 `--check --reverify` 产出的 `qmt_wrapper_hash`（yaml **+ md** 两文件）。
- **写前快照 → 精准改动 → 证据化自测 → 台账刷新**（每轮）。
- **零越权**：计划外问题登记 `docs/ISSUES.md`，禁顺手修。
- **QMT 端落地路线**：GBK 产物 → 剪贴板（`Set-Clipboard`）→ QMT 编辑器 Ctrl+A/Ctrl+V → 保存（QMT 自行加密落盘）。**明文 .py 拷入 `python\` 目录无效**。
