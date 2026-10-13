# D-27b 实施验收证据（B 探针 + C/D/E close 分批）

- 日期：2026-10-13｜方案：`docs/qmt-d27-fetch-convergence-design.md` v1.4（两轮审计 PASS 后定稿）
- 判型：件 B＝**新增检测型**（只增诊断输出）；件 C/D/E＝**纯性能型**（float_value 逐位一致 + EQC diff=0 + 端测结果不变为证）；件 A 折叠＝**下轮 D-28 立项（数据修正型）**，本轮零代码
- 实施方：**ZCode-GLM 外部生成（E7，sess_51082fe4）**；验收执行：DSH 侧（本会话）
- 回退基线：git stash `00b0c5a3f9ad27290ab78bde6fe9fa493d665bda`（706 文件；`add -A` 路径排除法实测 **17.5s**，替换原「>10min」假设）＋ 文件备份 `docs/evidence/_bak/source_import_qmt.py.bak-20261013-d27b`（SHA256 `8E38812B9B6D97AD54D32B1C0D0E73B87DEACFEDA07E5FF389044EE8F381535B`，与写前源一致）

## 一、改动落点（唯一代码文件 `quantstudio/strategy_compiler/source_import_qmt.py`，+167/−3）

| 件 | 落点 | 内容 |
| --- | --- | --- |
| C | `_qs_close_batch` :1191-1259（紧跟 `_qs_last_close`） | 批量预取 helper（批 400；五态回落；`QS_QMT_CLOSE_BATCH` 仅首调打印） |
| B | `_qs_fin_to_rows_hist` 签名 :1360 | 增 `table=None` 缺省形参（兼容既有调用） |
| B | HISTDBG3 探针 :1430-1467 | 每表一次：`t/code/rows/ua/ut/uk/uv/after/head/tail`（drop_duplicates 口径，NaN 视相等） |
| C | `_QS_QMT_ONCE = {}` :1563 | 产物模块级一次性标志（键 `histdbg3:<table>` / `close_batch_log` / `close_eqc`） |
| B | hist 调用点 :1628 | 显式传 `table=table` |
| D/E | `_qs_get_fundamentals` 非 hist 路径 :1678-1742 | 循环前分批预取→循环内查表（命中且非 None 用之，否则回落 `_qs_last_close` 逐只，逐位一致）；EQC 分母＝批量命中码集，`hit+miss==n`，命中 0 → `n=0 SKIP` |

新测试：`tests/test_qmt_close_batch.py`（10 用例，零 QMT 依赖：五态负向 c1/d1/d2、EQC 触发/口径/抽样上限 e1/e2/e3、逐位一致 c2、table 兼容 b1、探针算术 b2、NaN 口径 b3）。

## 二、O5 验收五步（DSH 侧执行，过环硬性串行）

1. **diff 范围核对 ✅**：近 40 分钟全仓 `.py` 修改扫描（排除 data/agent_workspace/.zcode）＝恰 2 文件＝允许写入清单，零越界；字节级 diff（vs 写前备份）＝ `+167/−3`，与 ZCode 申报一致。
   - 注：`git diff <stash>` 整树比对因 CRLF 归一化噪声不可用，改用「时间戳扫描 + 备份 no-index diff」双重法。
2. **编译 ✅**：`python -m py_compile source_import_qmt.py tests/test_qmt_close_batch.py` PASS。
3. **静态审查 ✅**：DSH 亲读全量 diff（helper 五态/探针 try-except/EQC 双哨兵/循环查表回落逐条核对），与方案 §2.2/§2.3 规范一致；无新 import、无白名单外调用族。
4. **契约一致性 ✅**：对外签名唯一变化＝`_qs_fin_to_rows_hist` 增缺省形参（产物内 helper）；`_qs_snap_kwargs`/`_qs_last_close`/字段映射/valuation 语义零改动；wrapper 渲染哈希同批追认（见判据 d）。
5. **测试 ✅**：`pytest tests/test_qmt_close_batch.py tests/test_source_import_qmt.py tests/test_qmt_progress_audit.py tests/test_qmt_hist_vectorize.py` → **81 passed in 1.53s**（既有 70 零衰减 + 新增 11 全绿含 M1 用例 e4；无既有红清单豁免项，新增红＝0）。

## 二·M1 增补（2026-10-13 用户批复方案 A，判据 3 由人工改机检）

- **改动**：`_qs_close_batch` 首调逐帧 `df.index.is_monotonic_increasing` 取与（独立 try，异常不污染取值路径；零有效帧 → `-1` 未知，防 vacuous-true），结果经 `_QS_QMT_ONCE['close_asc']` 汇入 `CLOSE_EQC` 行尾 `asc=1|0|-1`（正常与 SKIP 两分支均携带）。
- **门禁终态（M1 后重跑）**：pytest **81 passed**；六策略 **ALL 6 PASS (enc=gbk)**——CANSLIM c5df9bdcd8783e2b / fall_reversal b7c410bb6a1a8ee0 / tech_etf_mvo 1336a7344e0cafc4 / vol_regime_mom_rev d95947645ae1cf1d / weekly_smallcap d298641ba3a0f4c8 / 周频三层 202c28fe4a9a56aa；`check_fund_matrix --check --reverify` → `qmt_wrapper_hash=92c05ce43085`（复核 `--check` 绿）。
- **M1 实施期过程申报**：①一次编辑事故（edit 误删 `def _qs_fin_to_rows` 函数头行）当轮即发现即修复，py_compile + 81 用例零影响，净效果为零；②e4 用例两次迭代（`[0]→[-1]` 取本用例第 2 条 EQC 行；单码走 mock 逐只分支 boom 不触发 → 改双码），均测试侧修正、模板零返工。

## 三、门禁判据

- **判据 c（六策略重转）✅（M1 后终态）**：`python scripts/qmt_reconvert_six.py` → **ALL 6 PASS (enc=gbk)**，指纹见 §二·M1 增补（weekly_smallcap d298641ba3a0f4c8 已入剪贴板供端测）。
- **判据 d（矩阵哈希同批追认）✅（M1 后终态）**：`reverify` → `qmt_wrapper_hash=92c05ce43085`；复核 `--check` → `OK: 契约矩阵门禁通过（哈希一致 + MD 一致）`。

## 四、验收期局部阻断修复申报（分级豁免条款，3 处，均在本轮目标文件内、不改业务行为）

1. **模板 `source_import_qmt.py` HISTDBG3 head/tail 单行化**（首轮 pytest `test_b2` 红）：DataFrame `repr` 天然多行 → 审计行断行、grep 失效。修复＝`' '.join(repr(...).split())[:120]` 空白折叠（:1462-1466）。
2. **测试 mock `record_size1_as_batch` 开关**（首轮 pytest `test_c2` 红，mock 侧缺陷非模板缺陷）：mock 按 `len(codes)>1` 分类会把 size=1 合法尾批误记为逐只；增开关后直调用例可正确断言 `[3,3,1]` 批切分；逐只 kwargs 取样改用独立干净 mock（`fx2`）。
3. **测试模块 docstring 加 `r` 前缀**：消除 `\d` 转义 DeprecationWarning。

修复后全量重跑：**80 passed**（见 §二.5）；六策略重转与矩阵门禁均在修复后执行（§三全部为终态证据）。

## 五、端测判据（判据 f，用户侧待执行）

基线（2026-10-13 00:12 单周）：`L0=5227 L1=4410 L2=3816 L3=3666 L3v=3663 L4=366 L5=30 L6=17 R=16 R_selected=10`、`buy_submitted=7`、`positions=7 cash=308609.49 total=1003261.49`、572s。

1. **HISTDBG3 读数（uk 定谳，三分支）**：`uk≈报告期数(~40)`→折叠键可用（下轮 D-28 立项）；`uk≈rows(~2654)`→折叠彻底放弃；中间值→呈报裁定。期望出现 2 行（eps / growth_ability 各一）。
2. **CLOSE_EQC**：`n>0` 时 `diff=0` 且 `hit+miss==n` 且 **`asc=1`**（M1 机检：批量帧时间升序断言，替代原「人工核对」判据 3；`asc=0`＝行序假设被证伪须停用批量路径、`asc=-1`＝首调无有效帧未知）；或回落场景显式 `n=0 SKIP`。
3. ~~人工核对一次批量帧时间升序~~ → **已由 M1 并入判据 2 的 `asc=` 机检**（2026-10-13 用户批复方案 A）。
4. **结果逐位不变**：L5=30 / R_selected=10 / buy_submitted=7 / 持仓现金总额与基线逐位一致。
5. **耗时**：＜572s（目标：close 段 144s → ~15s；hist 段 226s 本轮不动）。

## 六、端测第一轮读数（2026-10-13 10:12:25–10:23:57，盘中运行）

- **判据 2（CLOSE_EQC）✅ 全过**：`QS_QMT_CLOSE_EQC n=20 hit=20 diff=0 miss=0 asc=1`（含 M1 机检 asc=1）；`CLOSE_BATCH i=1..10`（400×9+66）**hit=3666 miss=0 零回落**；EQC 内 20 次逐只调用仅 22ms（1.1ms/只，服务器热缓存）。
- **判据 1（HISTDBG3）→ 呈报裁定**：两行齐（eps / growth_ability）。读数：`rows=8740 ua=79 ut=79 uk=79 uv=79|71 after=79`——**uk=79 ≪ rows=8740（110× 冗余）**，`ua=ut=uk` 完全一致、`uv≤uk`、`after=uk`，⟹ `m_anntime/m_timetag` **非日频填充**（若日频 uk≈8740）；79＝**全史**（1990→2026 帧窗口）报告期数（原 ~40 估计按 10 年窗口作出）。技术解读：**折叠键可用**（8740→79 行/只）；eps `uv=71<uk=79` 证明值列去重会丢 8 期——键选 `(m_anntime,m_timetag)` 正确。
- **判据 3（逐位一致）❌ 未达（归因：时点混杂，非批量路径）**：`buy_submitted=6≠7`、`positions=6≠7`、`total=1002734.60≠1003261.49`、`L1=4409≠4410`、`L2=3815≠3816`、`L3v=3664≠3663`；但 **`L4=366 L5=30 L6=17 R=16 R_selected=10` 全保**。归因证据链（2026-10-13 用户批复重排，①为最强）：
  **① L1/L2 纯本地计算也变（最强，不依赖间接推理）**：L1（`get_stock_info` listed_date 过滤）与 L2（板块前缀纯本地列表操作）与本轮 close 批量毫无关系，纯本地计算都变了 ⟹ **输入数据源本身变了**（QMT 本地合约信息表在 00:03 与 10:12 两次运行之间被客户端/供应商更新），下游 L3→L5→R→orders→净值全部继承；
  ② total 差 −0.05% 系证据①名单漂移的下游继承（选股成员变→持仓变→净值变）。**原「query_last_close 盘中读到今日实时价」归因已撤回**（2026-10-13 用户据官方文档质证后修正）：行情读数经 `_qs_snap_kwargs`（`source_import_qmt.py:316-329`，D-1 设计）锚定回测 bar 当日（end=bar 当日/start=回退 10 自然日），批量路径同款（:1224）——读的是 1 月历史 bar，跨运行稳定；官方文档亦确认回测模型＝遍历固定本地历史数据（get_market_data_ex + subscribe=False）；
  ③ 未改动路径同轮变慢（valuation `get_financial_data` 11.2s→40.2s，3.6×）证明服务端时点效应存在；
  ④ EQC `diff=0` 已在本轮内证明批量↔逐只等价（20 抽样）。
  **归因≠豁免（用户裁定钉死）**：L1 变动本身即证明两次回测输入不同 ⟹ 逐位门禁必须**同窗同源 A/B 对照**定谳（D-26 血泪教训同源）。kept：growth 9,730,178（+11,472）、eps 9,704,256（+11,430）＝+1 交易日数据增量，与基线口径自洽。
- **判据 5（<572s）❌ 未达（同混杂）**：总墙钟 ≈691s；close 段 280.5s（批 28-36s/400 只，盘中）；hist 段 growth 49.6+114.7s / eps 29.9+113.5s（与基线 127/109s 基本持平——**未改动路径耗时稳定**，反衬 close/valuation 变慢属服务端时点效应而非代码）。**性能收益在盘中无法测准，须收盘后复跑。**
- **结论**：判型 C/D/E（纯性能型）的逐位门禁**本轮不可判定**（对照被时点污染）；判型 B（新增检测型）**已过**（探针两行 + 定谳读数齐）。待**收盘后（≥15:30，数据静止窗）背靠背 A/B 黄金对照**：旧产物（git `e269fc8` 重转，逐只版）vs 新产物连跑，逐位比较 selection/orders/positions/total——同窗同数据，等价性可严格判定。

## 八、盘后 A/B 黄金对照定谳（2026-10-13 15:11–15:41，背靠背同窗同源）

**方法**（用户 2026-10-13 上午批复②）：A＝当前工作树批量版产物（`weekly_smallcap.py` sha16=`d298641ba3a0f4c8`，canonical 重转）vs B＝stash `00b0c5a3` D-27a 态逐只版产物（单路径 `git show` 字节级提取 wrapper 后 canonical `--only weekly_smallcap` 重转，sha16=`69966ef2f6f97abb`，portability/selfcheck PASS）；A 15:11:14→B 15:28:21 背靠背（间隔 <17 分钟，盘后数据静止窗）；转换期间 wrapper 与产物均 sha 校验后逐位还原，工作树零残留。

**判据 3（A/B 逐位一致）✅ PASS（14/14 + 财务行全等）**：

| 读数 | A（批量版）15:11 | B（D-27a 逐只）15:28 | 一致 |
| --- | --- | --- | --- |
| L0/L1/L2/L3/L3v | 5227/4409/3815/3666/3664 | 5227/4409/3815/3666/3664 | ✓ |
| L4/L5/L6/R/R_selected | 366/30/17/16/10 | 366/30/17/16/10 | ✓ |
| selected/tradable/sell/buy | 10/10/0/6 | 10/10/0/6 | ✓ |
| positions/cash_ratio/cash | 6/0.4077/408811.60 | 6/0.4077/408811.60 | ✓ |
| total_value | 1002734.60 | 1002734.60 | ✓ |
| QSFUNDHIST growth（kept/drop/cc） | 9730178/9168/3666 | 9730178/9168/3666 | ✓ |
| QSFUNDHIST eps（kept/drop/cc） | 9704256/35090/3665 | 9704256/35090/3665 | ✓ |

且 A 轮 14 项读数与上午 10:12 端测 r1 **亦逐位一致**（L1=4409/L2=3815/L3v=3664/buy=6/positions=6/total=1002734.60）——10:12 与 15:11 之间合约信息表无新推送，§六「归因≠豁免」的同窗对照诉求成立并闭环：**判型 C/D/E 纯性能型的行为等价判定完成**。

**判据 5（close 段 A≪B）❌ FAIL——批量化零净收益（如实呈报）**：close 段（query_last_close 全程）A=182.4s（CLOSE_BATCH 10 批合计 181.1s，~49.4ms/只）vs B=182.9s（逐只匀速 ~49.9ms/只）——**持平**。机理：批量消除的是 API 调用次数开销（EQC 热缓存单只仅 1.1ms 证明调用开销本就极小），瓶颈在每只全史数据的服务端扫描/传输量（3666 只不变），故无可削减。旁证（未改动路径的噪声幅度反衬）：valuation 33.6s vs 8.9s、growth 拉取 76.9s vs 46.4s、eps 拉取 32.5s vs 68.3s（A/B 两轮互有快慢，非代码因素主导）；另 10:12 盘中 close 段 280.5s vs 盘后 182s 证实时点负载效应。总墙钟 A≈682s vs B≈749s 的差 67s 落在未改动段抖动内，不归功批量。**close 段 183s 的真瓶颈留给后续优化评估（如 D-28 折叠属 hist 段；close 段需另议数据窗口策略，不在本轮范围）。**

**回退条款判定**：用户批复原文「任一不同→回退批量」——本轮**无任何不同**，回退条款未触发；但性能预期（A≪B）未兑现，**保留/回退呈用户裁定**（见 §九呈报）。

## 九、遗留与登记（2026-10-13 盘后更新，接替 §七）

- **D-27b 终态**：判据 2/3 ✅（EQC 机检 + A/B 同窗逐位 14/14）；判据 5 性能预期 ❌（close 段 A=B≈182s，零收益）——**批量保留/回退呈用户裁定**；裁定后走 R1-B（提交）→ R1-C（双仓推送）。
- D-28（折叠，数据修正型）：已立项（2026-10-13 上午批复①(a)），方案①起草排队中——hist 段才是可削峰路径（uk=79 ≪ 8740 行/只）。
- D-29（profit_ability 契约缺口）：OPEN 候裁（与本轮正交）。
- R1-B archify 交付目录（`docs/evidence/archify-qmt-fetch-chain-20261013-005255/`）与本批代码同 commit 提交（精确 add，待用户确认后执行）。
- 本模块由 ZCode-GLM 外部生成（E7，sess_51082fe4，tokens≈500 万含 cache）；已过本地机检（py_compile / 80 用例 / 六策略重转 / 矩阵哈希门禁 4/4 类），业务逻辑与边界分支已经 DSH 亲读审查——盘后 A/B 黄金对照（§八）为最终行为判据：**逐位等价 PASS**。
