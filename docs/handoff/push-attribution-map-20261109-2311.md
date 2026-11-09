# 推送后 SHA → 归属线映射件

- **生成时刻**：2026-11-09 23:11:27
- **基线**：`8099a3181c3767ac7d3f46ba6e66423e300b74c9` = `8099a3181c3767ac7d3f46ba6e66423e300b74c9`
- **本次推送面（`8099a3181c3767ac7d3f46ba6e66423e300b74c9..HEAD`）**：**119 笔**
- **HEAD**：`74d629b81565ddaa75eda25beefa154a8bfbc497`
- **未归类**：**0 笔**（0，全部已归簇）

> 用途：本仓 author 字段无法切分归属（全部为同一 git 身份）且主分支为线性历史 ⇒ 一次 push 推出全部提交。
> 推送后如需追责/回滚/核对批准来源，**唯一线索是 commit 标题**；本件把该线索固化为可机读映射。
> 生成器：`scripts/push_attribution_map.py`（规则见脚本 `LINE_RULES`，未命中一律入「未归类」不静默猜测）。

## 一、按归属线汇总

| 归属线 | 笔数 | 范围（最短→最长 SHA） |
|---|---|---|
| 性能线（R-PERF） | **35** | `9f19a75` → `6d4883a` |
| ⑦ ptrade 部署线（qs-deploy） | **24** | `3b18075` → `e27acad` |
| ⑲ duckdb 升级线 | **18** | `c1b0248` → `b9831c4` |
| ⑳ 采集守护 / 观测线 | **17** | `c2fe26a` → `74d629b` |
| QMT / QFQ 线 | **10** | `5788cc5` → `ce65ba6` |
| 采集 / pipeline 线 | **10** | `1a8d9a1` → `3977d7a` |
| 跨线分账 / 白名单标准件（他线·候选归属） | **5** | `2d8b94e` → `646c8cd` |
| **合计** | **119** | — |

## 二、逐笔映射（按提交时间序，与 `git log` 一致）

| # | SHA | 时间 | 归属线 | 标题 |
|---|---|---|---|---|
| 1 | `3b18075` | 2026-11-06 21:28 | ⑦ ptrade 部署线（qs-deploy） | docs(design): PD-0019 ①方案件——多策略同账户隔离（N 策略通用·管线模板与语义），含平台能力盘点与官方模板更正 |
| 2 | `4cc46e1` | 2026-11-06 21:56 | ⑦ ptrade 部署线（qs-deploy） | docs(design): PD-0019 方案定稿——终版五项裁定＋四条补充条件（S1-S4 落为 N7-N10）＋三端额度输入设计＋执行序 |
| 3 | `804bea0` | 2026-11-06 22:13 | ⑦ ptrade 部署线（qs-deploy） | docs(kb): 入册 PD-2026-0018——两跳固件盲区（deploy 固件只测第二跳 ⇒ 第一跳注入改动系统性漏检） |
| 4 | `39d325d` | 2026-11-06 22:19 | ⑦ ptrade 部署线（qs-deploy） | fix(ptrade-deploy): PD-0018 F1/F2/F3 修复批自测全绿（修A 承诺额去重 + 修B 两跳固件 + 修C 断言AST定界 + 两跳元测试） |
| 5 | `5788cc5` | 2026-11-06 22:22 | QMT / QFQ 线 | docs(state): 甲案候②审登记＋四子裁落地（base=导出时点/落点qmt_snapshot/因子1不写/V7≤5）；执行序=②审过→V0→才委派 |
| 6 | `1a8d9a1` | 2026-11-06 22:24 | 采集 / pipeline 线 | feat(soft-lock): G1-G4 E 盲区封堵（运维2 预复验 5/5 已在） |
| 7 | `297c584` | 2026-11-06 22:24 | 采集 / pipeline 线 | fix(s1): 健康基准改近期窗口中位 A′（N=60·全表中位口径缺陷修复） |
| 8 | `9f19a75` | 2026-11-06 22:24 | 性能线（R-PERF） | docs(s1): A′ 生产首验件（11-06 首轮实读·81,801 行落库/水位推进/poison 未复现） |
| 9 | `8ef50c9` | 2026-11-06 23:44 | 性能线（R-PERF） | docs(handoff): R-PERF-10 ②审——F-2注释通过；E-01文档同步退回(百分比口径混用+超时1.5h与目标35-45min内部矛盾) |
| 10 | `763fbbb` | 2026-11-07 00:02 | QMT / QFQ 线 | evidence(qmt)+docs(state): 复权快照 V0 前置探测四项全过——front 物化于主库 stock_daily（*_front_ratio 现成列）；qfq_aux … |
| 11 | `58089e7` | 2026-11-07 00:05 | ⑦ ptrade 部署线（qs-deploy） | docs(kb,evidence): 入册 PD-2026-0019（重转缺第一跳⇒假重转，含 IMPORT-FRESHNESS 闸门）＋ PD-0018 ④验收件（四项硬门+时序自证+体积归… |
| 12 | `eaa7563` | 2026-11-07 01:10 | 性能线（R-PERF） | docs(handoff): R-PERF-11 ②审——D-1文档更正通过(实读双口径分列)；D-2合流受理附3条(含须复核55次计数本身)；D-3撤下认可 |
| 13 | `4caf5f8` | 2026-11-07 01:48 | QMT / QFQ 线 | fix(qmt): 复权因子快照层（QD-2026-0023 甲案）——QMT 侧按本地 *_front_ratio 跳变点换算，三态 loaded/absent/broken fail-lo… |
| 14 | `47509c7` | 2026-11-07 02:07 | 性能线（R-PERF） | docs(handoff): R-PERF-12 ②审——E-04前置①②③通过(daemon零进程/隔离/在途实测吻合)；基线在途观察推翻审核方自身14s每日与3.33h推算 |
| 15 | `c1b0248` | 2026-11-07 02:47 | ⑲ duckdb 升级线 | docs(duckdb): 11-07 升级窗口执行报告——1.4.5→1.5.6 首跳达成（11min，S1-S6 6/6，T057 四步过，daemon 33780 恢复） |
| 16 | `1cd9df5` | 2026-11-07 03:09 | 性能线（R-PERF） | docs(handoff): R-PERF-13 ②审——受理续跑与非线性登记；新发现QS_HIST_FAIL 5次(涉正确性,加R-7/R-8)与计数口径不一(105/107/108) |
| 17 | `93592fa` | 2026-11-07 15:26 | ⑲ duckdb 升级线 | docs(evidence): Obs-2 + 【P0】etf_daily DuckDB ART 索引损坏——水位冻结触发器升级到存储引擎层 |
| 18 | `b5b3895` | 2026-11-07 15:40 | 性能线（R-PERF） | docs(handoff): R-PERF-14 ②审——基线跑已失败停摆12h(108/148,03:38:47,无traceback)；读数JSON系11-06旧跑须标陈旧；R-8预登记受… |
| 19 | `0aaa6c0` | 2026-11-07 15:42 | ⑦ ptrade 部署线（qs-deploy） | feat(ptrade-deploy): IMPORT-FRESHNESS 闸门落地（内嵌 GEN 时刻戳为主判据·legacy 仅提示·P0 前 fail-closed） |
| 20 | `b9ad9eb` | 2026-11-07 15:43 | ⑦ ptrade 部署线（qs-deploy） | docs(kb,state): PD-2026-0019 结项（IMPORT-FRESHNESS 已落地·0aaa6c0）＋ E 项「任务运行周期与信号频率须匹配」边界行入 PROJECT_S… |
| 21 | `9b939e6` | 2026-11-07 19:45 | ⑲ duckdb 升级线 | docs(design): 【六步①】etf_daily DuckDB ART 索引损坏修复方案 + 无锚点全量重建熔断护栏（呈②审） |
| 22 | `3be300d` | 2026-11-07 19:46 | ⑲ duckdb 升级线 | docs(handoff): R-PERF-15 review - T-3/R-9/stale-rename accepted; new blockers P-1 (duckdb 1.4.5-… |
| 23 | `bf98a35` | 2026-11-07 19:47 | ⑲ duckdb 升级线 | docs(duckdb): T057-②c 旧版 SELECT 补齐成功（SC-004 三要素齐备）+ T053 七件齐自核 + 窗口任务补勾 |
| 24 | `04eeb7b` | 2026-11-07 19:48 | ⑦ ptrade 部署线（qs-deploy） | refactor(ptrade-deploy): IMPORT-FRESHNESS 改旁挂 sidecar（裁定乙）——产物本体零污染＋哈希可复现＋闸门读 .gen.json |
| 25 | `4ac840a` | 2026-11-07 20:30 | ⑲ duckdb 升级线 | docs(duckdb): db 族第三批候选备忘（epoch-ms 类型/标准库遮蔽两坑）+ 首周观测预备卡（T049/T050） |
| 26 | `c6d0729` | 2026-11-07 20:34 | ⑦ ptrade 部署线（qs-deploy） | feat(ptrade-deploy): sidecar 纳入交付包（P6）+ 部署 runbook 注明用途与可删性（裁定乙配套） |
| 27 | `8418af4` | 2026-11-07 20:37 | 性能线（R-PERF） | docs(handoff): R-PERF-16 review - R-8 verdict OVERTURNED (branch1 not supported, degradation did… |
| 28 | `69742ee` | 2026-11-07 20:52 | QMT / QFQ 线 | docs(design): 甲案修订 v1.1——快照文件被 QMT 沙箱证伪（FILEPROBE 三变体 PermissionError Forbidden fileIO），改嵌入式快照常量… |
| 29 | `5c646ed` | 2026-11-07 21:05 | ⑲ duckdb 升级线 | fix(duckdb): etf_daily ART 索引损坏——候选 B 单表重建完成（V1/V2/V3/V6 全通过） |
| 30 | `e50814c` | 2026-11-07 21:32 | 性能线（R-PERF） | docs: 分钟拉取性能复核证据件 + 水位滞后修复方案件（呈用户审核） |
| 31 | `61fcc2d` | 2026-11-07 21:49 | ⑲ duckdb 升级线 | docs: ②审勘误——根因机理更正（全表计数→窗口内428）+ 方案改 A' + P0-D 升格 |
| 32 | `ee00790` | 2026-11-08 00:08 | 采集 / pipeline 线 | docs(evidence): Obs-3 终态包——once 轮完整诊断（分叉 B 确认 + 两必答 + 交集） |
| 33 | `6872845` | 2026-11-08 01:11 | 性能线（R-PERF） | docs(handoff): R-PERF-17 review - double self-correction: R-10 withdrawn (file never rewritten; … |
| 34 | `af24d4f` | 2026-11-08 01:46 | QMT / QFQ 线 | fix(qmt): QFQ 快照改嵌入式常量（R337 沙箱证伪修复·修订 v1.1）——产物内 _QS_QMT_QFQ_SNAP_DATA（repr 全精度/禁时间戳/常量在位三态 embe… |
| 35 | `d3a4655` | 2026-11-08 02:11 | ⑦ ptrade 部署线（qs-deploy） | docs(ledger): 追加 E′ shadow 取证证据指针（trading 仓 c6619277/ac2a6b89·不回流主仓·随节点10统排） |
| 36 | `aa2970d` | 2026-11-08 02:11 | 性能线（R-PERF） | docs(handoff): R-PERF-18 review - P-1 narrowed CONFIRMED (dual short-window sha identical across… |
| 37 | `c2fe26a` | 2026-11-08 02:12 | ⑳ 采集守护 / 观测线 | feat(duckdb): 首周观测脚本化（T049 每日读数采集，不开主库）＋ day-1 日表与 forever 终态核因 |
| 38 | `1904641` | 2026-11-08 02:14 | 采集 / pipeline 线 | docs(design): 【六步①】分钟新交易日断流修复方案——F1/F2 判别定谳 + 三重修复腿（呈②审） |
| 39 | `c5f4b87` | 2026-11-08 02:28 | 采集 / pipeline 线 | docs(design): 方案件 v2——三形态定谳(b+c') + 腿1执行级对账 + 日分片下限 + 监控双阈值 |
| 40 | `45c0936` | 2026-11-08 02:40 | ⑦ ptrade 部署线（qs-deploy） | docs(handoff): 第二批呈批面备件——⑦线主仓 10 笔清单＋证据强度限定语（摘录非全证·11-09 须回原件） |
| 41 | `d41cbc4` | 2026-11-08 02:43 | 性能线（R-PERF） | docs(handoff): R-PERF-19 review - D-2 remediation accepted; C1b same-state compare CONDITIONALLY… |
| 42 | `2d8b94e` | 2026-11-08 02:43 | 跨线分账 / 白名单标准件（他线·候选归属） | docs(handoff): 第二批推送分账清单（40 笔未推＝我方4+他线36·共享 git 身份无法按作者切分）＋候裁 R-20261108-01 |
| 43 | `cd636dc` | 2026-11-08 02:45 | QMT / QFQ 线 | docs(evidence,state): QMT×本地 v8 对照证据——嵌入生效真机轮（embedded n=5385）·D=27 未收敛→两舱判据分层归因（成交价口径/信号输入价待验/选… |
| 44 | `35844d6` | 2026-11-08 03:00 | 采集 / pipeline 线 | docs(design): 方案件 v4——b) 撤回 + 缓存回声排除 + 1.5 动态下限 + 1.6 + 方法论结语 |
| 45 | `b14e124` | 2026-11-08 03:21 | 采集 / pipeline 线 | feat(pipeline): 分钟批次完整性守卫纯函数（1.5 动态下限 + 1.4 逐日对账）+ 27 项单测 |
| 46 | `c891cea` | 2026-11-08 03:24 | 采集 / pipeline 线 | feat(scripts): 分钟逐日覆盖对账脚本 + 04:30 恢复执行手册 |
| 47 | `c166912` | 2026-11-08 03:33 | 采集 / pipeline 线 | feat(pipeline): 1.6 挂账+下轮重拉纯函数（23 单测）+ runbook 补 06:00 硬上限 |
| 48 | `4ade651` | 2026-11-08 04:54 | ⑲ duckdb 升级线 | fix(scripts): 回填脚本复权 bootstrap 两处缺陷（实证 11-04 dry-run 全败 -> 全通） |
| 49 | `faf461f` | 2026-11-08 07:33 | ⑳ 采集守护 / 观测线 | fix(duckdb): 观测脚本口径修正（窗口内日志过滤＋第5项判据：采集轮质量审计 CLI 级失败=0）＋ day-2 读数落表＋分账清单计数刷新 |
| 50 | `aa62689` | 2026-11-08 13:35 | ⑲ duckdb 升级线 | feat(pipeline): 1.6 钉子（已收盘语义）+ 缺口账本 aux 侧 + P0-D 三分类清单 |
| 51 | `4b2b394` | 2026-11-08 14:02 | 性能线（R-PERF） | docs(handoff): R-PERF-20 review - four-run matrix verified correct BUT conservative bound -45.7%… |
| 52 | `d4df067` | 2026-11-08 14:03 | ⑦ ptrade 部署线（qs-deploy） | docs(handoff): 第二批备件补 D 批准状态自证（⑦线 11 笔逐笔对应⑤采信）＋E 推送面实态（领先 50 笔·两远程 8099a31·他线 40 笔口径已过期） |
| 53 | `0936f24` | 2026-11-08 14:04 | ⑲ duckdb 升级线 | docs(duckdb): T056 真实触发验证成立（EOL 守护真实当日 +9 天 ⇒ close_or_accept + 真实落盘提醒件） |
| 54 | `177cea0` | 2026-11-08 14:45 | ⑲ duckdb 升级线 | docs(analysis): DuckDB ART 索引反复损坏的归因分析 + 治本方案（回答用户「为何无缘无故坏」） |
| 55 | `bc348b8` | 2026-11-08 15:05 | ⑲ duckdb 升级线 | docs(report): 合并报告——stock_minutes ART 损坏 + 归因分析 + 治本方案（呈审核裁定） |
| 56 | `d8dd02b` | 2026-11-08 15:14 | 性能线（R-PERF） | docs(state,decisions): 登记节点 21 交叉依赖触发链（R-20261108-01·唯一触发条件＝改动触及 stock_daily.*_front_ratio 生成逻辑；… |
| 57 | `5929305` | 2026-11-08 15:14 | ⑳ 采集守护 / 观测线 | docs(duckdb): forever 恢复实况（pid 36788）+ day-3 前置四绿一红 + 我方第七～十三笔成册 |
| 58 | `0fb2347` | 2026-11-08 15:16 | 性能线（R-PERF） | docs(handoff): R-PERF-21 review - items 1-3 ACCEPTED (interval/scenario/C-approx-v1/four-labels/… |
| 59 | `175366e` | 2026-11-08 15:17 | ⑦ ptrade 部署线（qs-deploy） | feat(scripts): 推送后 SHA→归属线映射件生成器（LINE_RULES 分簇·未归类 fail-loud）+ 备件 F 段落地规程 |
| 60 | `4d55a05` | 2026-11-08 15:36 | 跨线分账 / 白名单标准件（他线·候选归属） | docs(reports): 四项内容合并呈报（呈总调度审核与调度） |
| 61 | `14c9db5` | 2026-11-08 15:58 | 性能线（R-PERF） | docs(handoff): R-PERF-22 review - gate v2 ACCEPTED (self-test 4/4 reproduced by auditor, v1 fals… |
| 62 | `1ea4559` | 2026-11-08 15:58 | ⑦ ptrade 部署线（qs-deploy） | docs(handoff): PD-0019 期1 ②审送审要旨（7 待核点+成本边界）+ 登记 R-20261108-02 候裁 |
| 63 | `3058c87` | 2026-11-08 15:59 | ⑳ 采集守护 / 观测线 | docs(duckdb): forever 代际更替核（36788 ExitCode=-1 external-terminate→重拉）＋ day-3 读数 ＋ T050 首周哨兵关键词接入清… |
| 64 | `3977d7a` | 2026-11-08 16:03 | 采集 / pipeline 线 | feat(pipeline): T2.1 周期 CHECKPOINT + T2.3 入口 CHECKPOINT（②审三条件全满足） |
| 65 | `e3a9f80` | 2026-11-08 18:56 | ⑲ duckdb 升级线 | fix(data): 重建 stock_minutes —— 破解 ART 索引损坏 + 六次失败的完整判据链 |
| 66 | `41ff498` | 2026-11-08 20:11 | 性能线（R-PERF） | docs(handoff): R-PERF-23 review - KNOWN_VIOLATIONS conditional (mtime-freeze blind spot: D-2 ove… |
| 67 | `78972ea` | 2026-11-08 20:13 | ⑳ 采集守护 / 观测线 | feat(duckdb): daemon 占用声明豁免机制（data/daemon_occupancy.json 共用约定）＋ day-4 读数 |
| 68 | `b82b909` | 2026-11-08 20:15 | ⑦ ptrade 部署线（qs-deploy） | docs(handoff): PD-0019 期1 ②审裁决——zcode「有条件可实施」＋我方复核三处修正（双读分叉真落点=持仓读数/风险方向=真实清仓/兜底收口） |
| 69 | `1477f17` | 2026-11-08 20:21 | ⑲ duckdb 升级线 | docs(design): 舱二 EPS 因子层对齐方案（起草件·候②审·Issue 随舱一）——四规格取证落地：主源双口径(eps vs basic_eps·2,346行历史分叉)/PIT落… |
| 70 | `630b05b` | 2026-11-08 20:21 | 性能线（R-PERF） | perf+docs(evidence): R-PERF-22~24 证据命名门禁 v2 + legacy 归档 + 知识条目入库 |
| 71 | `66671d2` | 2026-11-08 21:14 | ⑦ ptrade 部署线（qs-deploy） | docs(design): PD-0019 回写 §11.6——②审结论(有条件通过)+P0 三项前置+P0-3 实证(Portfolio 不可 import/继承)+P0 实现路线建议(可得… |
| 72 | `3f72247` | 2026-11-08 21:15 | 性能线（R-PERF） | docs(handoff): R-PERF-24 review - M-1/M-2 ACCEPTED (mutated=2 reported, self-test 5/5, first com… |
| 73 | `5928de0` | 2026-11-08 21:16 | ⑳ 采集守护 / 观测线 | docs(evidence): day-4 复跑 + 两条新事实（forever 自 15:58 external-terminate 后 5h16m 未自愈；once 40344 长跑 2h… |
| 74 | `572f590` | 2026-11-08 21:22 | ⑲ duckdb 升级线 | docs(evidence,kb): 舱二 EPS 层 R3 核实结论（只核实不裁）——uflag=1 集中 2023 后(71~87%)且与 uflag=0 滞后分布几乎相同(56 vs 4… |
| 75 | `a38e1ee` | 2026-11-08 21:55 | ⑳ 采集守护 / 观测线 | feat(pipeline): T3.1 索引一致性 canary —— 把 ART 损坏从「突然 FATAL」变「提前告警」 |
| 76 | `af5451a` | 2026-11-08 22:43 | 性能线（R-PERF） | docs(handoff): R-PERF-25 review - sha fabrication accepted (2c5d9bb invalid, actual 630b05b); N-… |
| 77 | `be264af` | 2026-11-08 22:46 | ⑳ 采集守护 / 观测线 | docs(evidence): ⑳占用声明补登未落（我方豁免未生效）+ ⑳窗采集轮 22:15 收工但 forever 未重拉（窗后供血未恢复） |
| 78 | `c20dc31` | 2026-11-08 22:46 | QMT / QFQ 线 | fix(scripts): 复权快照补记「每只 code 首值基准比率」——修复舱一远端段换算按 F=1.0 的缺陷（301557 c251 真机 74.38=raw vs 本地 front … |
| 79 | `4e8ee15` | 2026-11-08 22:49 | ⑦ ptrade 部署线（qs-deploy） | feat(ptrade-deploy): PD-0019 期1 ③实施切片A/B（isolation 配置面+注入模板+transformer 接线）——默认关纯增益已证；启用态被 P4 闸阻… |
| 80 | `5c145bb` | 2026-11-09 00:22 | ⑳ 采集守护 / 观测线 | fix(data+ops): 时区 bug 根治 + 每日 ART canary 自动化 + 铁律 23 依据③修订 |
| 81 | `dcfac04` | 2026-11-09 00:26 | 性能线（R-PERF） | perf(evidence): R-PERF-26 全窗 148 日收口（1.5.6 hot 态）+ O-1 调用侧修正 |
| 82 | `233bf5e` | 2026-11-09 00:26 | 性能线（R-PERF） | chore(evidence): R-PERF-26 命名修正——golden 补 layer 段、探针内层产物归档 legacy |
| 83 | `f458a15` | 2026-11-09 01:09 | ⑳ 采集守护 / 观测线 | feat(ops): canary 三层留痕 —— 损坏时产出可转发的 md 报告 |
| 84 | `ffa0e48` | 2026-11-09 01:11 | ⑦ ptrade 部署线（qs-deploy） | docs(design): PD-0019 回写 §11.7——equiv 判据由文本标记改 AST 特征命名（含反向风险防御 fail-loud）+ P0-2 strict 拒减仓语义入验收… |
| 85 | `b5817d0` | 2026-11-09 01:11 | ⑦ ptrade 部署线（qs-deploy） | feat(ptrade-deploy): PD-0019 期1 切片B 通闸——equiv 改 AST 特征命名剔除 ISO 纯新增块（§11.7）；启用态 deploy EXIT=0，纯增益… |
| 86 | `f2a166a` | 2026-11-09 01:13 | 性能线（R-PERF） | docs(handoff): R-PERF-26 review - FULL-WINDOW cross-version behavior equivalence PROVEN (golden … |
| 87 | `fe433bf` | 2026-11-09 01:15 | QMT / QFQ 线 | test(qmt): 补 T9 回归守卫「首值基准比率必入快照」并同步 T7 期望值语义（R347 缺陷守卫：首值≠1.0 时入快照·兜底路径同守·首值=1.0 不入；T1-T9 13 pas… |
| 88 | `2cd3b64` | 2026-11-09 01:15 | ⑳ 采集守护 / 观测线 | chore(duckdb): 勾销「待④挂载」挂账（④ 已 11-01 挂载）+ 两 eol-reminder 件语义区分 + 路径笔误登记 + day-5 豁免生效读数 |
| 89 | `449995d` | 2026-11-09 01:31 | 性能线（R-PERF） | docs(kb,evidence): R-PERF-27 回执纪律条目入库 + P-1 分层更新 + 对外口径草案 |
| 90 | `59751bf` | 2026-11-09 01:33 | QMT / QFQ 线 | docs(evidence): 舱一达成证据——QMT 复权口径对齐价格层目标域达成（v3 真机轮 26 同窗：23 逐位一致·口径超差 0·3 处残余定性为窗起点不同/两侧交易日历不齐不可比… |
| 91 | `ce65ba6` | 2026-11-09 02:01 | QMT / QFQ 线 | docs(design): 舱二 EPS 实施规格 v1——前置闭合（Context7 面板语义：s_fa_eps_basic=当期基本EPS·m_anntime=公告日·m_timetag=… |
| 92 | `3dd4973` | 2026-11-09 02:04 | ⑲ duckdb 升级线 | chore(duckdb): 首周日表加时点列（回溯性）＋补录已发生观测时点读数 ＋ T049 豁免命中注记 |
| 93 | `45f384a` | 2026-11-09 02:17 | 性能线（R-PERF） | docs(handoff): R-PERF-27 acceptance - items 1/3 PASS, item 2 CONDITIONAL (doc L7 still says all … |
| 94 | `3bad74d` | 2026-11-09 02:19 | 性能线（R-PERF） | docs(design): R-PERF-27 补正——L7 P-1 段改写为「初判→R-17短窗复证→R-26全窗复证」三段式 |
| 95 | `2784c7f` | 2026-11-09 02:52 | ⑦ ptrade 部署线（qs-deploy） | feat(ptrade-deploy): PD-0019 期1 验证舱——加 _QS_ISO_BANNER 认货行（首启/跨日重置打点，build=20261109-p1）；四象限隔离产物 P… |
| 96 | `3fbcf97` | 2026-11-09 02:52 | 性能线（R-PERF） | docs(handoff): R-PERF-27 correction ACCEPTED - doc L7-L10 three-part rewrite verified verbatim (… |
| 97 | `1a54916` | 2026-11-09 02:56 | ⑳ 采集守护 / 观测线 | docs(evidence): day-6 观测（豁免持续·声明窗在册）+ ⑳阶段5 未起·供血主体仍缺席（属窗内计划内） |
| 98 | `771f001` | 2026-11-09 02:56 | 性能线（R-PERF） | docs(design): R-PERF-28 交接包自足性——补入 E-04 开工三条实施纪律 |
| 99 | `377021e` | 2026-11-09 02:57 | 跨线分账 / 白名单标准件（他线·候选归属） | docs(handoff): 第二批分账改显式 SHA 白名单（关键词分账误判他线 7 笔）＋我方 14 笔逐笔清单 |
| 100 | `a63fdfe` | 2026-11-09 07:31 | ⑳ 采集守护 / 观测线 | docs(evidence): day-7 守护读数（forever 真实恢复 pid 34512·阶段5 已执行）＋库尺寸 62.47→62.06 GB 变化登记（归⑳链） |
| 101 | `f5bf70d` | 2026-11-09 11:23 | ⑦ ptrade 部署线（qs-deploy） | fix(ptrade-deploy): PD-0019 期1 止血——隔离启用时四个下单入口全禁（order/value/percent/target_orig），阻断调仓真单风险；出止血版产… |
| 102 | `3ad5988` | 2026-11-09 11:24 | 性能线（R-PERF） | docs(handoff): R-PERF-28 acceptance - E-04 three disciplines landed verbatim at doc L73-L80 incl… |
| 103 | `585b8fc` | 2026-11-09 11:25 | 跨线分账 / 白名单标准件（他线·候选归属） | docs(handoff): 推送分账显式 SHA 白名单法立为跨线标准件（全线适用·与⑦ attribution map 并池）＋我方白名单补至16笔 |
| 104 | `e11b856` | 2026-11-09 11:26 | 性能线（R-PERF） | docs(design): R-PERF-29 交接包补 E-4 共享核心文件纪律（三条→四条） |
| 105 | `e5fcf42` | 2026-11-09 11:41 | ⑦ ptrade 部署线（qs-deploy） | feat(ptrade-deploy): PD-0019 期1 正式批四件——现金预算虚拟接管+拆单乐观记账+拒单回滚等价+启动认货；§11.8 教训入册（紧急动作的账户形态门） |
| 106 | `b78f91b` | 2026-11-09 11:42 | 性能线（R-PERF） | docs(handoff): R-PERF-29 acceptance - E-4 landed and start chain is 10 steps; MEASURED erratum: … |
| 107 | `646c8cd` | 2026-11-09 11:43 | 跨线分账 / 白名单标准件（他线·候选归属） | docs(handoff): 首周收官预检件（11-14 汇总六项·我方3项已达成/④侧4项待回传）＋日表 11-08 两行补时点 |
| 108 | `b31ee44` | 2026-11-09 11:46 | 性能线（R-PERF） | docs(design): R-PERF-30 stash 语法勘误 + 补 E-5 回退点可用性验证（四条→五条纪律） |
| 109 | `edf6547` | 2026-11-09 20:18 | ⑳ 采集守护 / 观测线 | docs(evidence): day-8 观测——daemon 全无·库 7h45m 无写入，⑳声明称「forever 常驻」与实态矛盾（判据语义错位如实上报·候裁改进） |
| 110 | `0476b5f` | 2026-11-09 20:18 | ⑦ ptrade 部署线（qs-deploy） | docs(design,evidence): PD-0019 隔离前 baseline 取证（2026-11-09 实跑·非隔离版）＋新缺陷方案件（下单预算口径=总现金≠可用资金·数据修正型待… |
| 111 | `308e187` | 2026-11-09 20:28 | 性能线（R-PERF） | docs(design): R-PERF-31 计数更正（四→六）+ 补 E-6 基线读数复用须先复核前提 |
| 112 | `b9831c4` | 2026-11-09 22:30 | ⑲ duckdb 升级线 | feat(duckdb): 判据语义分类落地（豁免只认停机窗类·在位描述类缺席判「声明与实态不符」单列告警）＋三场景验证 |
| 113 | `e32f3b3` | 2026-11-09 22:30 | ⑳ 采集守护 / 观测线 | docs(evidence): 更正 §7.18——⑳ 于 22:30:02 撤销声明（非「撤除」模糊表述）＋retroactive_log 核实＋与我方独立观测交叉印证 |
| 114 | `4f1a2d1` | 2026-11-09 22:31 | ⑳ 采集守护 / 观测线 | data(p0d): 窗口外死信归档登记清单（B 乙口径·仅登记未动库） |
| 115 | `2c53e5f` | 2026-11-09 22:31 | 性能线（R-PERF） | docs(handoff): R-PERF-31 acceptance - all three items PASS; reuse precondition independently re-… |
| 116 | `e27acad` | 2026-11-09 22:43 | ⑦ ptrade 部署线（qs-deploy） | docs(evidence,design): ②审后勘误——今日实跑载体为旧产物（早于 PD-2018 F3(a)/49,000 上限）⇒ 根因改判「陈旧产物为主·口径为并行」；算术勘误（超发… |
| 117 | `1ddddc3` | 2026-11-09 22:46 | 性能线（R-PERF） | perf(design): R-PERF-05 v3 落点修正——超时在分钟表不在日线表；日线落点实测收益为负已回退 |
| 118 | `6d4883a` | 2026-11-09 23:02 | 性能线（R-PERF） | chore(gate): dep-closure 白名单登记 _dda_old_rperf05_tmp（R-PERF-05 对拍件引用工作树专属依赖·随第二批授权推送） |
| 119 | `74d629b` | 2026-11-09 23:11 | ⑳ 采集守护 / 观测线 | docs(evidence): day-9 新判据正常态验证 ＋ 我方16笔全部出仓 ＋ 双远程逐位一致（6d4883a）⇒ T055 勾选 |

## 三、未归类明细（须人工确认后再补规则）

_无_
