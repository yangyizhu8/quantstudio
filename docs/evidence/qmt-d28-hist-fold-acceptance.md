# D-28 ③实施 + ④验收 证据文档（QMT hist 财报表报告期折叠）

- **日期**：2026-10-15（凌晨轮）
- **判型**：**数据修正型**（QMT 端回归本地「PIT 全报告期」契约口径；影响面声明 + diff 只源于修正行集 + 准确度只升不降）
- **方案**：`docs/qmt-hist-fold-design.md` v0.2.1（②审计两轮闭环，用户 2026-10-13 裁定「①②验收通过在卷」，2026-10-15 裁定「A 批准——③实施轮启动」）
- **基线**：实施起点 HEAD `0b88728`（D-30 R1-B）；本轮期间他线持续提交（至 `c46681c`），我方 diff 仍限于允许写入清单内文件
- **回退点**：`git stash` 快照 `17040123be1fd32c5ed24c0fb349254d440a1222`（`baseline-20261015-0130-d28-impl`，40 文件全树超集）＋文件级备份（`agent_workspace/_d28_backup/`）
- **图（Plan 第⑧项子项）**：`docs/evidence/archify-qmt-hist-fold-20261015-0115/IMPACT-DIAGRAM.md`（mermaid 降级载体，含 7 轮修复记录与四门状态）

---

## 一、③实施（E7 外部委派 + DSH 侧就地修复）

### 1.1 委派产物（ZCode-GLM，E7）

| 文件 | 内容 | 证据 |
| --- | --- | --- |
| `quantstudio/strategy_compiler/source_import_qmt.py` | 新增 `_qs_hist_fold(_one, table, code)`（:1383，组合键 `drop_duplicates(['m_anntime','m_timetag'], keep='last')` + 缺键/异常回落 `dropna(how='all')` + 原帧直通终态 + `QS_QMT_HISTFOLD` 审计行每表一次含 `intravar` 哨兵）；批次/逐码路径 :1612 接入；单码 DataFrame 路径 :1642 对称接入；`_qs_get_fundamentals` 键字段恒附 :1696-1700（hist 三表，幂等表尾追加） | `git diff --stat` = +114/−31（后经 1 字符 GBK 修复，见 1.2） |
| `tests/test_qmt_hist_fold.py` | 12 用例（设计 §五 逐条对应，354→382 行） | `pytest tests/test_qmt_hist_fold.py -q` → **12 passed**（`docs/evidence/d28-new-suite-run.txt`） |

### 1.2 DSH 侧修复与打回项（**逐项披露**）

| # | 项 | 性质 | 证据 |
| --- | --- | --- | --- |
| F1 | wrapper 注释含 **非 GBK 字符 `≪`(U+226A)** → 渲染产物 GBK 编码契约被破坏，连带 **17 例**测试失败（`UnicodeEncodeError: 'gbk' codec can't encode character '\u226a'`） | **局部阻断修复**（本轮目标文件内 1 字符，GBK 安全字符 `<<` 替代） | 修前 22 failed/80 passed → 修后 5 failed/97 passed；`docs/evidence/d28-six-suites-prefix-fail.txt`（修前）vs `d28-adjacent-six-suites-run.txt`（修后）；GBK 扫描 BAD_COUNT 3→2（余 2 处为宿主侧既有 `⇒`，非渲染模板） |
| F2 | 委派测试文件用 **模块属性访问** `wmod._qs_hist_fold` 取函数，但函数体定义于 `_QS_QMT_FUNDAMENTALS_EXT` **模板常量内**（模块面无同名属性）→ **12 例全部 setup 失败** | **委派轮缺陷打回·就地修复**（机制对齐既有 `_ns()` exec 范式） | `AttributeError: module '...' has no attribute '_QS_QMT_ONCE'` → 修复后 12 passed |
| F3 | 用例 5 把**双码帧整体折叠**（生产为按码折叠）+ 镜像 `_latest_by_code`（与真实函数不等价）→ 断言口径错误 | **委派轮缺陷打回·就地修复**（改调真实策略函数 `weekly_..._quantstudio.py:138 _latest_by_code`，按码折叠回汇） | 修前 1 failed（shape (2,6) vs (1,6)）→ 修后 12 passed |
| F4 | 新测试文件遗留未用 import（ruff F401） | 清理 | ruff 复查无输出 |

> 编码委派事实：本轮 `zcode_code` 共 5 次调用——第 1 次成功产出 wrapper 实现（回执丢失但产物落盘）；第 2–5 次用于测试文件，其中 2 次回执中断（结果未持久化，产物实际于 02:35:08 落盘）、2 次 `[zcode-fallback:ratelimit]`（GLM 5 小时窗口上限，重置时点 02:42:30，两次重试均在重置前 3 分钟）。**最终测试文件为 ZCode 产物**，未被 DSH 重写；DSH 仅做 F2/F3/F4 三处机制修复。E7 台账见 ISSUES。

---

## 二、④验收（O5 五步，全 DSH 侧执行）

### ① diff ⊆ 允许写入清单

本轮实际改动（`git status` 精确核对）：`quantstudio/strategy_compiler/source_import_qmt.py`（M）＋`tests/test_qmt_hist_fold.py`（新增）＋`docs/evidence/fundamentals-contract-matrix.{yaml,md}`（哈希 1 行）＋ `docs/evidence/archify-qmt-hist-fold-20261015-0115/`（图交付）＋簿记（ISSUES/PROJECT_STATE）。
工作区其余 40 项 M 文件为**他线在途改动**（未纳入、未触碰）。**无清单外改动**。

### ② 构建/编译

- `python -m py_compile quantstudio/strategy_compiler/source_import_qmt.py` → exit 0
- `python -m py_compile tests/test_qmt_hist_fold.py`（随套件导入通过）

### ③ 静态扫描（对照基线）

工具：`ruff check --statistics`（本机 pyflakes/flake8/pylint 不可用）。

| 对象 | 基线（HEAD 版） | 现行 | 结论 |
| --- | --- | --- | --- |
| wrapper | F841 × 1 | F841 × 1 | **零新增** |
| `tests/test_qmt_hist_fold.py` | —（新文件） | 0（F401 已清） | 干净 |

### ④ 契约一致性

- `python scripts/check_fund_matrix.py --check` → **OK（哈希一致 + MD 一致）**；`qmt_wrapper_hash: d89e3aa103e6 → 20f58868117f`（同批追认，PTrade 侧 `wrapper_hash: 7a424f397983` 不变）
- `python scripts/run_contract_gate.py --strategies` → **CONTRACT GATE : PASS**（契约套件全部通过；矩阵门禁通过；**6 策略 api_portability 全部通过**；既有白名单无触发）——`docs/evidence/d28-contract-gate-six-strategies.txt`
- 返回帧契约零变化实证：新用例 10 断言「调用方视角列集不变」（辅助键经既有投影剥离）→ passed；`test_qmt_hist_vectorize` 黄金对照（`_qs_fin_to_rows_hist` 输出逐位）→ passed

### ⑤ 测试

| 套件 | 结果 |
| --- | --- |
| **`test_qmt_hist_fold`（新，12 例）** | **12 passed** |
| `test_qmt_instrument_snapshot` | passed |
| `test_source_import_qmt` | passed |
| `test_fund_matrix_qmt_gate` | passed（哈希追认后） |
| `test_qmt_close_batch` | passed |
| `test_qmt_hist_vectorize` | passed（黄金对照存活**已被证明**） |
| `test_qmt_progress_audit` | **3 failed（既有遗留，非本轮引入，见 §三）** |
| **合计** | **111 passed / 3 failed**（`docs/evidence/d28-seven-suites-final.txt`） |

---

## 三、未闭合项（单列，零越权登记）

**D-30 遗留缺陷（本轮六套件门禁首次暴露）**：`tests/test_qmt_progress_audit.py` 的 3 例（`test_b_stock_info_golden[codes0/codes1]`、`test_b_filter_status_golden`）失败于 `NameError: name '_qs_instr_detail' is not defined`。

- **机理**：`_qs_instr_detail` 定义在 `_QS_QMT_MODULE_HEADER`（:2066 起 / :2154），由 **D-30 提交物**引入并被模板 `:500`/`:986`/`:1024` 调用；而该测试的 exec 命名空间 `_TEMPLATES`（:31-35）**不含 MODULE_HEADER** → 调用即 NameError。
- **归因证据**：①该 3 例失败与 D-28 改动无关（D-28 未触碰 `_qs_instr_detail`/快照层/该测试文件）；②HEAD 基线对照（detached worktree）该套件 11 passed / 4 errors（缺 `_bak` 文件无法执行同 4 例），说明其在本轮之前即已不完备；③D-30 验收（2026-10-13）只跑三套件，未含本套件。
- **处置**：修点 `tests/test_qmt_progress_audit.py`（补 `_QS_QMT_MODULE_HEADER` 入 exec 命名空间，~2 行）**不在 D-28 允许写入清单内** → 按零越权**登记 ISSUES 并报用户裁定**，本轮不擅自修改。D-28 的门禁口径据此记为「**零新增失败 + 全部新增用例通过 + 六策略重转全过**」。

其余候用户端测排期项（设计 §五 端测四判据）：判据 1（HISTFOLD 两表读数）/ 判据 2（`QS_QMT_PROGRESS` 处理段 226s→~10s）/ 判据 3（盘后背靠背 A/B 逐位零漂移）/ 判据 4（D-29 条件项）——随用户端测窗与 D-23/D-24/D-30 判据同队。

---

## 四、回退方案（可执行）

1. `git stash show --stat 17040123` 核对后，按路径单文件提取：`git checkout 17040123 -- <path>`（禁整仓 pop——快照含他线在途态）；或 `git reset --hard 17040123`（仅限本会话独占工作区时）。
2. 矩阵哈希回追认：`python scripts/check_fund_matrix.py --check --reverify`。
3. 六策略重转复核 + 产物 sha16 对照 + **QMT 端已部署产物替换**（回退不重转＝端上仍跑折叠版）。
4. 文件级备份：`agent_workspace/_d28_backup/source_import_qmt.py.bak-d28gbk`、`tests/test_qmt_hist_fold.py.bak-premechfix`。

## 五、判定

**④验收结论：PASS（本方案零新增失败）**——12/12 新用例通过、6 策略 api_portability 全过、契约门 PASS、静态零新增、矩阵哈希同批追认、diff 限于允许写入清单、回退点齐备；唯一红项为**已归因的外部遗留（D-30）**，已登记候裁。候 **⑤用户确认**后进入提交/推送批次。
