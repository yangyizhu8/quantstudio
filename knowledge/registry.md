# 对齐面注册表（Alignment Surface Registry）

> 性质：双端对齐洞的**总账本**（唯一权威）。收敛度量 = 「未验证」数单调下降。
> **三端口径（2026-10-16 D-33 件B）**：本地↔PTrade（原口径）＋本地↔QMT——共享面（POS/API/MCH/FEE/DAT）加「QMT 锚点」列（本地语义一份、平台锚点两个）；QMT 专属面（QLC/QMCH/QCAL）独立成节。
> 更新纪律：每轮对齐修复六步④验收时同步更新；禁止陈旧。
> 状态四态：`已锚定`（本地已对齐 + 平台证据 + 契约测试）｜`已排查`（探针/案例证实无分歧，无专项测试）｜`未验证`｜`已知分歧`（修复中，附方案件指针）

## 条目总览

| 类别 | 前缀 | 条目数 | 已锚定 | 已排查 | 未验证 | 已知分歧 |
|---|---|---|---|---|---|---|
| Position/Account 字段 | POS | 17 | 0 | 1 | 15 | **1** |
| 注入 API 契约 | API | ~40（待枚举） | 部分 | — | 待枚举 | 0 |
| 撮合规则 | MCH | 10（骨架） | 0 | 1 | 9 | 0 |
| 生命周期 | LC | 8（骨架） | 0 | 0 | 8 | 0 |
| 费用模型 | FEE | 5（骨架） | 0 | 1 | 4 | 0 |
| 数据口径 | DAT | 15（骨架） | 1 | 1 | 13 | 0 |
| 指标计算 | IDX | 10（骨架） | 0 | 1 | 9 | 0 |
| QMT 生命周期专属 | QLC | 4 | 1 | 1 | 2 | 0 |
| QMT 撮合/引擎专属 | QMCH | 4 | 0 | 0 | 4 | 0 |
| QMT 日历/合约专属 | QCAL | 6 | 4 | 1 | 0 | 1 |

## POS · Position/Account 字段（17，据 PTrade 文档 get_positions 契约）

| 编号 | 字段 | 语义 | 状态 | 证据/测试指针 | QMT 锚点 |
|---|---|---|---|---|---|
| **POS-01** | `cost_basis` | 每股持仓成本 | **已锚定（2026-10-07 修复落地）** | 平台=摊薄（卖出扣费净得+买入不含费加权），双锚点实证 0.973/0.972；引擎 `cost_basis_method='diluted'` 默认；契约测试 `tests/test_cost_basis_diluted.py`；验收 `docs/evidence/cost-basis-diluted-acceptance-20261007.md` | 未验证 |
| POS-02 | `amount` | 总持仓量 | 已排查（四象限案例逐笔一致） | `docs/evidence/dual-end-alignment-final-report-20260827.md`；本案例 26 笔委托一致 | 未验证 |
| POS-03 | `enable_amount` | 可用量（T+1） | 未验证 | 探针 probe_enable_amount 规划 | 未验证 |
| POS-04 | `today_long_amount` | 今买量 | 未验证 | — | 未验证 |
| POS-05 | `today_short_amount` | 今卖量 | 未验证 | — | 未验证 |
| POS-06 | `long_amount` | 多头量 | 未验证（A股场景可能恒 0） | — | 未验证 |
| POS-07 | `short_amount` | 空头量 | 未验证（同上） | — | 未验证 |
| POS-08 | `last_sale_price` | 最新价 | 未验证 | — | 未验证 |
| POS-09 | `sid` | 证券代码 | 已排查（四象限案例代码口径一致 .SS/.SZ 归一） | 本案例代码归一实证 | 未验证 |
| POS-10 | `business_type` | 业务类型 | 未验证 | — | 未验证 |
| POS-11 | `contract_multiplier` | 合约乘数 | 未验证（ETF/股票恒 1?） | — | 未验证 |
| POS-12 | `margin_rate` | 保证金率 | 未验证 | — | 未验证 |
| POS-13 | `long_pnl` | 多头浮盈 | 未验证 | — | 未验证 |
| POS-14 | `short_pnl` | 空头浮盈 | 未验证 | — | 未验证 |
| POS-15 | `long_cost_basis` | 多头成本 | 未验证 | — | 未验证 |
| POS-16 | `short_cost_basis` | 空头成本 | 未验证 | — | 未验证 |
| POS-17 | `delivery_date` | 交割日 | 未验证（股票场景 None?） | — | 未验证 |

## API · 注入 API 契约（待全量枚举，首批已知锚点）

| 编号 | 条目 | 状态 | 证据指针 | QMT 锚点 |
|---|---|---|---|---|
| API-00 | 平台吸收规则集（get_history/include/fq 等一批语义） | 已锚定（部分面） | `docs/evidence/ptrade-platform-absorptions-20260903.md` | 已锚定（部分面）：wrapper 七类 portability 规则＋矩阵哈希＋49 用例 |
| API-01..NN | get_history / get_price / order 系 / set_universe / …（待从 ptrade_api 暴露面全量枚举，P2 清单） | 未验证 | — | 未验证 |

## MCH · 撮合规则（骨架）

| 编号 | 条目 | 状态 | 证据指针 | QMT 锚点 |
|---|---|---|---|---|
| MCH-01 | 成交价模式（收盘/开盘撮合） | 已排查（本案例双端成交价逐笔一致） | 四象限案例 trades 逐笔一致 | 未验证（QMCH 系承接） |
| MCH-02 | 滑点 | 未验证（本案例双方均无滑点痕迹） | — | 未验证（QMCH 系承接） |
| MCH-03 | 涨跌停可成交性 | 未验证 | probe_limit | 未验证（QMCH 系承接） |
| MCH-04 | 停牌处理 | 未验证 | probe_limit | 未验证（QMCH 系承接） |
| MCH-05 | T+1 约束 | 已排查（本案例 9/11 盘后 可用=53000<总54900 双端同构） | 平台日志 2024-09-11 盘后行 | 未验证（QMCH 系承接） |
| MCH-06 | T+0（ETF 分类） | 未验证 | probe_enable_amount | 未验证（QMCH 系承接） |
| MCH-07 | 最小交易单位/整手 | 已排查（本案例逐笔整百一致） | 同 MCH-01 | 未验证（QMCH 系承接） |
| MCH-08 | 部分成交/拒单 | 未验证 | — | 未验证（QMCH 系承接） |
| MCH-09 | 资金不足边界 | 未验证 | — | 未验证（QMCH 系承接） |
| MCH-10 | 碎仓防护 | 未验证 | — | 未验证（QMCH 系承接） |

## LC · 生命周期（骨架）

LC-01 initialize 时机｜LC-02 before_trading｜LC-03 handle_data 时刻遍历｜LC-04 after_trading｜LC-05 run_daily 定时任务｜LC-06 盘后快照时点｜LC-07 上下文对象语义｜LC-08 异常中断行为 —— 全部未验证（probe_lifecycle）。

## FEE · 费用模型（骨架）

| 编号 | 条目 | 状态 | 证据指针 | QMT 锚点 |
|---|---|---|---|---|
| FEE-01 | 最低佣金 5 元 | 已排查（首日净值 99980 = 4×5 双端一致） | 平台日志 2024-01-02 盘后；本地 trades commission 列 | 未验证 |
| FEE-02 | 佣金费率 | 未验证（比例区间未触及） | — | 未验证 |
| FEE-03 | 印花税（卖出） | 已排查（本案例 ETF 免税双端均 0） | 本地 tax 列全 0 | 未验证 |
| FEE-04 | 过户费 | 未验证 | — | 未验证 |
| FEE-05 | 费用计入成本口径（含费净投入） | 已锚定（2026-10-07 双锚点判别：买入不含费+卖出含费扣） | 验收证据 §2 三口径判别表 | 未验证 |

## DAT · 数据口径（骨架，已锚定存量回填）

| 编号 | 条目 | 状态 | 证据指针 | QMT 锚点 |
|---|---|---|---|---|
| DAT-01 | pctChg 合成口径（除权日涨跌幅） | 已锚定 | `docs/evidence/ptrade-pctchg-synth-evidence.md` | 未验证 |
| DAT-16 | ~~估值价格快照微差~~（2026-10-07 P1 S1+S2 破案：价格逐位一致已证伪）→ **归因转移 CORP-01** | 已关闭（归因转移） | 破案链 `docs/evidence/corp-action-etf-merge-2025-09-22.md` | 未验证 |
| **CORP-01** | **ETF 份额合并/折算处理缺失**（引擎公司行为层；159934 黄金ETF 2025-09-22 合并比例 0.948126，平台 4000→3796 份+成本等比 7.673→8.086+nav 连续；本地未处理→9/22 净值虚高 +1715.23=204×8.408 逐位，滚存至 9/30 差 1780.92→回撤判定异号→10/9 级联 3.88pp 残差全链） | **④验收 PASS（2026-10-07 ③实施+④验收，待⑤确认推送）** | 修复证据 `docs/evidence/corp-action-etf-merge-fix-acceptance-20261007.md`：合并带去吸附+按份取整，30 测试绿，9/30 差 1780.92→−26.19，首分歧推迟 112 日 | 未验证 |
| **CORP-02** | **零股卖出整手截断**（2026-01-29 四象限清仓单本地 3200 vs 平台 3296：持仓 3293 被 int(n/100)×100 截 93 份滞留；A 股清仓单零股可全卖）——CORP-01 修复后暴露的级联缺口，V2'' 残差 2.23% 主源 | **④验收 PASS（2026-10-07 ③实施+④验收，待⑤确认推送）** | 单点修复 `_execute_sell` 数量式分支；证据 `docs/evidence/corp02-odd-lot-sell-acceptance-20261007.md`：36 测试绿，V2''' 残差 2.23%→**0.013%**，分歧 18→3 组（全为 CORP-01 尾差级，两组总量对齐 35300=35300），末日持仓逐位一致；**双端对齐主线<0.5% 判据达成（CORP-01+02 合并）**；V3''' 基线 2.08%（moving_avg） | 未验证 |
| DAT-02 | 基准收益（000300） | 已排查（双端 36.85% 逐位一致） | 四象限案例 | 未验证 |
| DAT-03 | 日线 include 语义（D-1 锚定） | 已锚定（E1 铁律 + 四格实测） | AGENTS.md E1 条；ptrade_api.py:598 | 已锚定：wrapper `_qs_snap_kwargs` 锚定回测 bar 当日＋D-28 端测四轮 L0-L6 逐位一致（`docs/evidence/d28-e2e-run-20261015.md`） |
| DAT-04 | 前复权 fq='pre' | 已排查（本案例价格序列逐笔一致） | 四象限案例 | 未验证 |
| DAT-05..15 | 日期边界/首末日/fallback/指数日线专门 API/停牌日 K 线/ETF 池 PIT/… | 未验证（P2 枚举细化） | — | 未验证 |

## IDX · 指标计算（骨架）

IDX-01 总收益｜IDX-02 年化｜IDX-03 基准收益（=DAT-02 复用）｜IDX-04 回撤｜IDX-05 夏普｜IDX-06 索提诺｜IDX-07 胜率口径（按平仓对 vs 按日）｜IDX-08 盈亏比｜IDX-09 Alpha/Beta｜IDX-10 平均持仓时长。
当前：IDX-03 已排查；IDX-07 疑似口径差（本地 38 笔 vs 平台 14 笔口径不同——**待探针验证后定级**）；其余未验证。
> QMT 锚点（D-33 件B）：IDX 全部**未验证**（QMT 真机指标对照未做）。

## QLC · QMT 生命周期专属（D-33 件B，2026-10-16 建面）

| 编号 | 条目 | 状态 | 证据指针 |
|---|---|---|---|
| QLC-01 | handlebar/bar 驱动回调时机 | 未验证 | probe_lifecycle（QMT 腿待建） |
| QLC-02 | ContextInfo 对象语义（C.get_*/订阅面） | 已排查（形态库定谳） | `docs/qmt/inner-api/`（2043 行离线转写＋11 项实测定谳）；主库「平台转换铁律-文档签名不可信」 |
| QLC-03 | 生命周期壳改写（initialize/handle_data 保留时序） | **已锚定** | portability `QMT-LIFECYCLE-SHAPE` 规则（`quantstudio/strategy_compiler/portability_rules.py`）＋六策略端测运行通过 |
| QLC-04 | 定时任务/遍历语义（run_by_bar 等） | 未验证 | — |

## QMCH · QMT 撮合/回测引擎专属（D-33 件B，2026-10-16 建面）

| 编号 | 条目 | 状态 | 证据指针 |
|---|---|---|---|
| QMCH-01 | QMT 回测撮合价口径（open/close） | 未验证（match_profile qmt 档未建） | `docs/qmt-conversion-design.md` §5.2 L1/L2/L3 分层 |
| QMCH-02 | 涨跌停/停牌可成交性（QMT 端） | 未验证 | probe_limit（QMT 腿待建） |
| QMCH-03 | T+1 约束（QMT 端） | 未验证 | — |
| QMCH-04 | 费用/滑点配置面（回测参数面板） | 未验证（文档线索在案） | QS-qmt `15-界面操作.md:86-102` 回测参数面板（可配置半，文档级线索） |

## QCAL · QMT 日历/合约信息专属（D-33 件B，2026-10-16 建面）

| 编号 | 条目 | 状态 | 证据指针 |
|---|---|---|---|
| QCAL-01 | 合约信息活表漂移（get_instrument_detail 无 as-of，跨推送窗 L1/L2 漂移） | **已锚定（D-30 冻结快照层；E2E 判据 A/B/C 候用户端测窗）** | `docs/qmt-instrument-snapshot-design.md` v0.3＋`tests/test_qmt_instrument_snapshot.py`（15 用例）＋commit `0b88728` |
| QCAL-02 | 板块成份无 as-of（realtime 传毫秒时间戳→静默空列表，不抛错） | **已排查（D-31 探针定谳）** | `docs/probe/qmt_realtime_sector_probe-20261013.py`（三档时间戳全空返回）；主库「QMT大转换管线…形态坑」复现固件节 |
| QCAL-03 | hist 财务日频全史帧→报告期折叠（组合键 keep=last） | **已锚定（D-28 盘后正式归档）** | `docs/qmt-hist-fold-design.md` v0.2.1＋`tests/test_qmt_hist_fold.py`（12 用例）＋`docs/evidence/d28-e2e-run-20261015.md`（判据 1/2/3＋四轮交叉）＋commit `8be00aa` |
| QCAL-04 | profit_ability 表映射缺失（映射表三表→hist 分支死代码→恒空帧） | **已知分歧（D-29 候裁：修①补映射／裁②移除表集）** | `docs/ISSUES.md` D-29 节 |
| QCAL-05 | 必填参数空串未定义行为（get_financial_data endDate 空串→静默空） | **已锚定（D-22 安全终点 '20991231'＋QSFUNDHIST 审计行）** | 主库「平台转换坑-必填参数空串」；`docs/evidence/qmt-m5-batch2-probe-20261010.md` |
| QCAL-06 | 产物物理编码契约（GBK 可解码；注释特殊字符高频复发） | **已锚定（canonical 重转门四自检＋测试强制 encode('gbk')）** | 主库「产物落盘编码契约」；`scripts/qmt_reconvert_six.py`；**R 口径案例：D-28 轮 `≪`(U+226A) 复发计 R_i>0→先修管线＝D-33 件D 写前拦截（`docs/qmt-alignment-loop-closure-design.md`）** |

## 变更记录

- 2026-10-07 POS-01 →已锚定（修复落地+契约测试）；FEE-05 →已锚定（双锚点判别：买入不含费）；DAT-16 新增（估值价格快照微差，V2 残差 3.88pp 归因）。
- 2026-10-16 **D-33 件B（QMT 维度首批）**：表头升三端口径；POS/API/MCH/FEE/DAT 五节加「QMT 锚点」列（共享面首批锚点 2：API-00 部分面、DAT-03）；新增 QLC(4)/QMCH(4)/QCAL(6) 专属面节（已锚定 5＋已排查 1＋已知分歧 1＋未验证 6）。方案：`docs/qmt-alignment-loop-closure-design.md` v1.0。
- 2026-10-06 v1 建账：17 POS + 6 类骨架；回填历史锚点（DAT-01/03、API-00）；四象限案例新增排查锚点 7 条 + 已知分歧 1 条（POS-01）。

> **维度对齐档案（件 G，2026-10-08）**：维度宣布机制与首例候选（DIM-01 ETF 日线策略域，N=1/3）见 [contracts/dimension-alignment.md](contracts/dimension-alignment.md)；机检 scripts/check_dimension_alignment.py（只提示不自动宣布）。
