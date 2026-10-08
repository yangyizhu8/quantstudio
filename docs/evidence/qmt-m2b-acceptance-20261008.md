# QMT 管线 M2b 验收证据（④轮，2026-10-08）

> 计划：`docs/qmt-pipeline-m2b-plan.md`（rev2，`c27d21f` ②审复核放行）；
> 用户裁定：③实施令 + 五项确认（⑤-1 出图 / ⑤-2 单次大委派 / ⑤-3 七项写入清单 /
> ⑤-4 白名单准入标准 / ⑤-5 计划不落盘）。
> 本文档 = 六步④验收结论（判据对照 M2b-rev2 §⑧ 五判据）。
> **执行方式披露**：实现型编码经 `zcode_code` 委派（3 轮，详 §6）；首轮自测阻断缺陷与
> 部分实现缺口由**主导者代修**（详 §7，超出「局部阻断笔误豁免」范围，如实登记不掩饰）。

## 0. 判型声明

**新增检测型**——新增能力面（QMT source 转换路径 + QMT 白名单全量 + `_QMT_CONTEXT_METHODS`
登记面）对新目标报告 verdict；既有产物零漂移以判据③④实证。
修复前置三问：①影响其他功能=无（独立新模块 + dual 路径字节不变门）；②影响性能=无
（新路径不在既有链路）；③影响精度=无（本地引擎 / PTrade 产物零触碰）。

## 1. 判据① 六策略 QMT 产物 —— **PASS（6/6）**

命令：`python -m quantstudio.strategy_compiler.cli import <strategy.py> --target qmt --out output/qmt_export --no-smoke`

| 策略 | gbk 解码 | ast.parse | 首行 | init+handlebar | 字节 |
|---|---|---|---|---|---|
| CANSLIM突破成长选股策略 | ✓ | ✓ | `#coding:gbk` | ✓ | 60799 |
| fall_reversal | ✓ | ✓ | `#coding:gbk` | ✓ | 47309 |
| tech_etf_mvo_rotation | ✓ | ✓ | `#coding:gbk` | ✓ | 55110 |
| vol_regime_mom_rev | ✓ | ✓ | `#coding:gbk` | ✓ | 62889 |
| weekly_smallcap_growth_momentum_10 | ✓ | ✓ | `#coding:gbk` | ✓ | 57015 |
| 周频小市值成长动量（三层止损） | ✓ | ✓ | `#coding:gbk` | ✓ | 61397 |

**残余断言（②审实测需自建面）**：六策略产物 `context`/`g`/`data`/`log` 未定义裸引用
**= NONE(0)**；wrapper/视图注入 **10/10**（`_qs_context_view`/`_qs_data_view`/`_qs_log_view`/
`_qs_get_history`/`_qs_order_target_value`/`_qs_get_positions`/`_qs_should_run_daily`/
`_qs_should_run_after`/`_qs_get_fundamentals`/`_qs_get_ashares`）；审计行 10–11 条/策略。

**exit code 说明（非缺陷）**：qmt 单目标下 `run_card.status=PARTIAL` → CLI 返回 1。
与 **M2a spec 路径同构**（`orchestrator.py:361-362`：smoke_result is None → PARTIAL），
机理=qmt 产物为 QMT innerApi 生命周期、不在本地引擎可跑，smoke 不适用（`:341-348`）。
判定以产物本体 + `validation.api_portability=PASS / source_import=PASS` 为准。

## 2. 判据② QMT 白名单 —— **PASS（6/6）**

`validate_qmt_portability(产物)` 六策略全 PASS，无 BLOCK。
白名单扩容（`portability_rules.py`）：`_QMT_API_WHITELIST` 3→44 条、`_QMT_CONTEXT_METHODS`
1→5（`get_market_data_ex` / `get_financial_data` / `get_stock_list_in_sector` /
`get_instrument_detail` / `get_trading_dates`，各附转写册行号）。

**准入红线执行记录（用户 ⑤-4 裁定）**：登记项**逐一对应** `source_import_qmt.py` 实际注入面
（每条第附定义行号或转写册行号）；**未放宽任何 fail-closed 门**——实施中一度出现的
驱动段循环变量调用被白名单正确 BLOCK，**以改设计（按函数名逐一驱动）而非放宽门禁**解决。

## 3. 判据③ PTrade 零回归硬门 —— **PASS（同源同名终证法，6/6 IDENTICAL）**

载体：`git worktree add D:\tmp\qs-m2b-head HEAD`（改动前框架）双侧对跑；
**同源终证法**：把工作区当前六策略源同步至 worktree，隔离源码变量（他线在途 M 不影响结论）。

| 策略 | 主侧 sha12 | 对照侧 sha12 | 判定 |
|---|---|---|---|
| CANSLIM突破成长选股策略 | 949679f23708 | 949679f23708 | IDENTICAL |
| fall_reversal | 265865048619 | 265865048619 | IDENTICAL |
| tech_etf_mvo_rotation | a884eac2c690 | a884eac2c690 | IDENTICAL |
| vol_regime_mom_rev | 6b1516fd7ddf | 6b1516fd7ddf | IDENTICAL |
| weekly_smallcap_growth_momentum_10 | 98b25cd100cc | 98b25cd100cc | IDENTICAL |
| 周频小市值成长动量（三层止损） | 3d29403e2506 | 3d29403e2506 | IDENTICAL |

**对照基准有效性核验**：worktree 三共享文件 vs 改动前备份——`orchestrator.py`/`cli.py`
字节相同；`portability_rules.py` 归一化行尾后内容相同（差异仅 CRLF/LF 检出转换）。
worktree 已清理（`git worktree remove --force`）。

## 4. 判据④ 既有回归 —— **PASS**

- `python scripts\run_contract_gate.py --strategies` → `CONTRACT GATE : PASS`
  （契约矩阵门禁：哈希一致 + MD 一致；契约套件 pytest 全过 + 既有白名单无触发；
  六策略 api_portability 冒烟全过）。矩阵哈希**未触发**（两 EXT 常量零触碰，架构 D 两裁之二成立）。
- 新模块单测：`python -m pytest tests/test_source_import_qmt.py -q` → **6 passed**
  （正例 / gbk fail-closed 负例 / 未定义裸名负例 / DENY 剥除负例 / 生命周期缺件负例 /
  白名单回归锚点）。

## 5. 判据⑤ 策略源码零改动 —— **PASS**

以 M2b 开工快照（`docs/handoff/m2b_strategy_hash_20261008-221509.txt`，34 策略 SHA-256）为基线
逐文件比对：**六策略源全部 UNCHANGED**（大小写归一后比对；首次全 CHANGED 系 PowerShell
`Get-FileHash` 大写 vs `hashlib` 小写之假阳性，已归因）。
`source_import.py` 零改动（`git status` 空）；`git log -- source_import_qmt.py` 空（未被任何提交卷入）。

## 6. FR-QMT-01 字段映射表（③首项交付物，定稿落地）

**消费面实测**：六策略中 **3/6** 消费 fundamentals（CANSLIM / weekly_smallcap /
周频小市值），唯一字段集 = `eps{eps,publ_date,end_date}` / `growth_ability{or_yoy,publ_date,end_date}`
/ `valuation{float_value}`（`date=None` → 前一日 PIT 语义）。

| 请求 table | QMT 表 | 字段映射 | 依据 |
|---|---|---|---|
| `eps` | PERSHAREINDEX | `eps`→`s_fa_eps_basic`；`publ_date`→`m_anntime`；`end_date`→`m_timetag` | 07-行情函数.md:2388；:2329-2330；:1837（毫秒时间戳） |
| `growth_ability` | PERSHAREINDEX | `or_yoy`→`inc_revenue_rate`；`publ_date`/`end_date` 同上 | 07:2395 |
| `valuation` | CAPITALSTRUCTURE + 行情价 | `float_value`(流通市值,元) = `circulating_capital`(股) × close（`get_market_data_ex(dividend_type='none')`） | 07:2376；:96；:122 |

- 取数统一 `report_type='announce_time'`（**显式钉死，不依赖默认值**）；PIT 依据 07:1852/1867/1877
  （`report_time` 官方警告「可能取到未来数据」→ 禁用）。
- `fieldList` 拼表名前缀（`PERSHAREINDEX.s_fa_eps_basic` 形态），依据 07:1863/1906/1837——**③轮首修缺陷**（§7-A）。
- 返回契约与 PTrade 侧同构：`DataFrame(index=code, columns=请求字段本地名)`，空→NaN 契约行不抛错。

## 7. 委派披露与主导者代修清单（如实登记）

### 7.1 委派执行（E7）

| 轮 | 内容 | 结果 |
|---|---|---|
| 委派1 | 单次大委派（新模块 + 三处贯通 + 单测，七件套全量） | **超时 >1800s，零落盘**（无产物可收尾） |
| 委派2 | 块1/3：模块骨架 + AST 工具族复制 + EXT 常量 + 改写链 | 落盘 1247 行 |
| 委派3 | 块2/3：19 件 wrapper + 入口 + 机制②接线 | 落盘 1741 行 |
| 委派4 | 块3/3：三处贯通 + 单测 + 1 处缺陷修正 | 落盘 5 文件 |

**关键环境事实**：ZCode 侧 **Python 执行权限缺失**（`No permission client configured for Bash`，
13 种形态全被拦），故 4 轮委派产出的自测**全部未能执行**——所有机器验证由本会话（DSH 侧）
承担（亦合规于 C4 审计禁区：构建/测试/diff 核对不委派）。
**成本**：四轮合计约 1.26 亿 GLM 侧 input tokens（含 cacheRead，按各轮汇总）。

### 7.2 主导者代修清单（**超出「局部阻断笔误豁免」范围，逐项登记**）

| # | 缺陷 | 根因（文件:行） | 修复 |
|---|---|---|---|
| A | fundamentals `fieldList` 缺表名前缀 | 块2 产物（`native_table` 解包未消费） | 拼 `f"{native_table}.{field}"`，附依据注释 |
| B | 产物 `def init(C):` 后**语法错误**（unindent） | 锚点取 `body[0].col_offset` 致 prologue 缩进叠加为 8 空格；`get_source_segment` 不含首行缩进致 docstring 脱体 | 引入 `_body_rep` + `_shell_body`：**函数体区间整体替换 + 统一缩进**，废除局部锚点拼接 |
| C | 产物首行非 `#coding:gbk`（QMT-GBK-HEADER BLOCK） | `_assemble` 对已含 PEP263 声明者**不追加** gbk 头，而策略主体拼接在模块头之后 → 声明沉到中部 | 产物首行**恒为** `#coding:gbk`；源内原声明改写为中性注释 |
| D | `run_daily` 回调注册被丢弃（策略主体永不驱动） | `_pass2` 把整句替换为审计行，注册语义丢失 | 收集回调名（`_run_daily_funcs`），pass3 在 handlebar 尾段逐名驱动；非标识符表达式如实 warning |
| E | 缺 `handle_data` 的策略无 `handlebar`（QMT-LIFECYCLE-SHAPE BLOCK） | 规则 h 要求「缺件不误杀 + handlebar 由 run_daily 生成」，实现缺失 | 合成 handlebar 壳（prologue + 驱动段），不改写源内任何函数 |
| F | 驱动段循环变量调用被白名单 BLOCK | 产物内对局部循环变量发起调用 | **改设计**（按函数名字面量逐一驱动），**不放宽门禁** |
| G | vol_regime 非 gbk 字符（`⇒` U+21D2）致写盘 fail-closed | 源注释/docstring 含非 gbk 符号；策略源码零改动铁律下只能由转换器转写 | 新增 `_pass0_gbk_translit`：范围**严格限定注释 + docstring**（tokenize COMMENT ∪ AST docstring 节点），**仅替换字符不重建引号**；运行期字符串字面量仍 fail-closed |

**定性**：B/D/E 为实现缺口（非笔误），由主导者代修而非打回重生成——理由：①首轮自测（判据②构建）
被直接阻断；②修复仅限本轮新建文件内部、不改变对外业务行为；③委派方无自测能力，打回成本高且
收敛不确定。**该选择已如实上报，供用户裁定是否接受此执行模式。**

### 7.3 设计偏离登记（待用户裁定）

- **别名区形态**：M2b 计划字面「模块级 `g = C`」在 QMT 下**不可行**（QMT 无模块级 ContextInfo，
  导入期 `g = C` → NameError）。落地为「模块级占位 `None` + init/handlebar 壳内 `global` 刷新」，
  语义等价且 `hasattr(g,'x')` 守卫天然可用。
- **与 M2a 的路径差异**：M2a spec 路径产物用 `g.*`→`C.*` **AST 改写**（产物 L33 实证）；
  M2b source 路径按计划用**别名注入**（理由：source 策略含约 55 处 `hasattr(g,...)` 反射守卫，
  AST 改写无法覆盖）。两路径实现独立，符合 M1 §2.2 双路径声明。

## 8. 过程事件

1. **他线 HEAD 推进**：实施期间 HEAD 由 `c27d21f` 推进至 `68743cb`（他线 loop-cd 4 提交，
   未触及本任务 5 文件）。本会话改动全程为未提交状态、**未被卷入任何他线提交**（精确核对）。
   判据③对照基准因此改用 `68743cb`（其 `portability_rules.py` 与本任务改动前备份内容一致，已验证）。
2. **写前快照**：`git stash create -u` → `git stash store`（`stash@{0}`）+ 三文件 cp 备份
   （`docs/handoff/backup-m2b-20261008-221509/`）+ `git status` 基线落盘。
3. **越界核对（机械）**：基线 678 项 → 现 680 项，新增仅 `source_import_qmt.py`（本任务）与
   本会话基线文件——**零越界**（不以委派方自述为准，以 git status 集合差为准）。
4. **禁门不放宽实证**：两次门禁拦截（白名单循环变量调用 F、gbk 写盘 G）均以**改设计/限定范围**
   解决，未放宽任何 fail-closed 门（承 M2a BOM 案例方法学）。

## 9. 未闭合项（如实登记，转 M3/M5 或待用户裁定）

| # | 项 | 状态 |
|---|---|---|
| 1 | **minute deny 负例未实现**（判据①列的负例之一） | source 路径尚无分钟域 deny 面；六策略均日线，实际影响 0，但防护缺失 → 建议 M3 前补 |
| 2 | M5 实测项（QMT 运行期） | `get_market_data_ex` 末根 bar 语义、`C.accountID`/ACCOUNT 资金字段、单股 orderType 取值、`get_financial_data` 返回形态与 PERSHAREINDEX 是否含 `m_anntime`/`m_timetag`、`get_trading_dates` 元素形态与 init 内不可用、`get_stock_status` 枚举对齐、板块名「沪深A股」、bar 节拍 |
| 3 | ETF 动态池固化 QMT 化 | 六策略消费 0，按计划缓提 |
| 4 | 影响面图（用户 ⑤-1 裁定「出」） | archify-gate 独立产物（`.archify/` HTML）**本轮未产出**；会话执行预算已耗尽于缺陷修复与五判据验证 → 结构图以 mermaid 随 ④ 呈报，**降级登记** |

## 10. 结论

**判据①–⑤ 全部 PASS**（六策略 QMT 产物 6/6、QMT 白名单 6/6、PTrade byte-diff 同源终证 6/6
IDENTICAL、CONTRACT GATE PASS + 新模块单测 6 passed、策略源码 UNCHANGED）；
判型 = **新增检测型**成立（既有产物逐字节零漂移为凭证）。
**待⑤用户确认后随批⑥推送**（本会话不推送；推送批由用户另行批准）。
**未闭合项 4 项**（§9）已如实登记，其中 #1/#4 需用户裁定处置。

---

## 11. ⑤用户确认与裁定归档（2026-10-08）

**④验收结论**：**PASS**——五判据全过；FR-QMT-01 映射表定稿合格；四次委派 + 七项代修**如实披露合格**
（用户评价：「输出合格，过程有改进空间」）。

| # | 裁定事项 | 用户裁定 | 落点 |
|---|---|---|---|
| ① | 代修模式（7 项，其中 3 项超出笔误豁免范围） | **接受**——超出部分已逐项登记，且改动仅限本轮新建文件内部 | 本轮登记生效 |
| ①-附 | 委派效率（1.26 亿 token 成本异常 + 委派超时零落盘） | 转 **dsh 桥接侧改进项**（与 `exec.signal` 接线一并处理） | **跨线事项，非本管线范围** |
| ② | minute deny 负例未实现 | **转 M3 前补**——六策略均日线、实际影响 0，不阻塞 | M3 前置项 |
| ③ | archify 影响面图 | **接受 mermaid 降级**——本轮结构图足以表达管线形态；archify HTML 产物作为 **M3 附加项，不强制** | M3 附加项 |
| ④ | ⑤确认节点 | **通过**——⑤随下一推送批 | 见下 |

**当前状态**：M2b 六步流水线 **①方案 ②审计 ③实施 ④验收 ⑤用户确认 全部闭合** →
**⑥推送批候批**（与 dev 双件 + 双端对齐等合流；**本会话不推送**，由用户在纯呈批轮批准后执行）。

**推送批候批件（本管线侧）**：

| 件 | 内容 |
|---|---|
| `quantstudio/strategy_compiler/source_import_qmt.py` | 新建（1937 行，QMT source 转换器） |
| `quantstudio/strategy_compiler/orchestrator.py` | `orchestrate_source` 加 `target` 维度 + qmt 分支 + 汇总面 |
| `quantstudio/strategy_compiler/portability_rules.py` | QMT 白名单 3→44 条 + `_QMT_CONTEXT_METHODS` 1→5 |
| `quantstudio/strategy_compiler/cli.py` | `import` 子命令加 `--target dual\|qmt` |
| `tests/test_source_import_qmt.py` | 新建（6 测试） |
| `docs/evidence/qmt-m2b-acceptance-20261008.md` | 本证据文档 |
| `docs/qmt-pipeline-baseline.md` | M 系列进展台账刷新 |
| `docs/handoff/m2b_strategy_hash_20261008-221509.txt` | 判据⑤基线（含 `backup-m2b-20261008-221509/` 三文件备份） |

**推送前必办（承接纪律）**：① 精确 add（禁 `-A`，他线在途 `M`/`??` 一律不卷入）；
② 核对双远程 HEAD 逐位一致（多 push URL）；③ 主仓推送后同步 QuantStudio-trading 回测部分
（同步门：`git fetch && git merge origin/main` + check-drift 九项 + ci-smoke 共享层段）。
