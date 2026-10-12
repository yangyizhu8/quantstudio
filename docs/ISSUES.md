# ISSUES · QuantStudioHybrid

> 计划外问题登记台账。纪律：发现即登记，不顺手修；处置由用户决策。
> 字段：问题描述 / 定位证据 / 严重等级 / 状态 / 归属迭代 / 修复方案 / 验证证据 / 关闭时间

## ISS-001

- **问题描述**：分钟档主循环每 bar 用 `df.iterrows()` 遍历全 universe 构建价格 dict——pandas 最慢反模式，每 bar 数千次 Python 对象实例化
- **定位证据**：`quantstudio/backtest/backtest_engine.py:2432-2435`
- **严重等级**：高（Phase 0/后段一实测：U2 口径 B 段 80.67%、iterrows 61.36% = 2× 目标主战场）
- **当前状态**：**已转正为 M2 段二主内容（2026-09-20 用户批准）并实施完成**——向量化构价（dict(zip) + 实例级裸码→QMT 映射缓存）两态逐位门 U1-U5 全 PASS；U2 真口径 487.4→194.8s（门禁口径 2.50×）/ 三跑中位 173.2s（2.81×）；后段二 B 段 80.67%→2.49%
- **归属迭代**：M2 段二（路线 P）✅
- **修复方案**：已实施（:2432-2435 热路径向量化 + 病态列缺失保留原路径绝对等价 + `_qmt_map_cache` 实例级缓存）
- **验证证据**：gate_m1 --m2 五用例报告 + 回归门 102 passed + 契约门 PASS + post_m2_u2 快照
- **关闭时间**：2026-09-21（M2-P 验收通过，随本轮 commit 关闭）

## ISS-002

- **问题描述**：`DataDict.__contains__` 与 `__getitem__` 的裸码匹配回退路径存在 O(n) 遍历（`any(...)` 逐键比对），策略高频 `in` 判断时有理论开销
- **定位证据**：`quantstudio/backtest/ptrade_api.py:314-328`（`__contains__` 的 any 循环）；`:286-288`（`__getitem__` 同型回退）
- **严重等级**：低（性能；仅裸码键场景触发）
- **当前状态**：候选（未立项；H1/H3 相关实测均证伪为非热点——DataDict 重建 0.00s/BarData 0.07s）
- **归属迭代**：待定（优先级降低）
- **修复方案（候选）**：_data 构建时同步维护裸码→key 的辅助索引
- **验证证据**：Phase 0 H1/H3 对照（方案件 §4.3）
- **关闭时间**：—

## ISS-003

- **问题描述**：`attach_bar` 每 bar 清空 `_query_cache`（PIT 语义所需）+ `get_history` 查询路径对策略高频调用付出重复代价——原假设为 Phase 4B 内存切片全量掩码
- **定位证据**：`ptrade_api.py:602`（每 bar 清缓存）；`:1336-1359`（4B 内存切片）；`:1377-1384`（SQL 回退链）
- **严重等级**：**高（Phase 0 实证：锚点场景 get_history 链 86.35% = 1185.93s——项目首要热点）**
- **当前状态**：候选（归因落定 2026-09-20，总调度预告「可能成为首选迁移段」**实证确认**）
- **归属迭代**：**方案件段一（数据访问查询面批量化）首选迁移段**——路径修正：锚点大头是 SQL 回退链（get_bars_by_count→_ensure_bars_in_cache 逐 code 全史装载 5178 次 SQL/943s 锁等待），4B 内存切片在锚点未参与（策略查日线）、overnight 分钟场景实测 3.82%（`_current_minute_bar` 650 次 46.19s）
- **修复方案（候选）**：批量预装全史（~26 片 SQL 替 5200 次单码）+ 日级结果缓存 / Rust Arrow 列存（方案件 §1.2 段一，随②审立项）
- **处置注记（2026-09-21 R 终止令转写，总调度指令①）**：**R 已终止（2026-09-21 总调度裁定）**；装载扫描残余（后段二 C-api 83.98% 占比，绝对值 ~202s/3日，IO 界）转立**数据层 SQL 预筛新题候选**（杠杆=每码全史 SELECT 加日期下界 WHERE 预筛/索引评估，Python/duckdb 域），入主仓 pending 池（并回后生效，本仓 ISSUES 随并回流向主仓台账）
- **验证证据**：Phase 0（方案件 §4.2 场景 1/2；docs/evidence/phase0_hotspots.md §2）
- **关闭时间**：—

## ISS-004

- **问题描述**：①2026-02~05 窗口分钟档股票回测 universe 退化为 ETF-only——**成因定谳（总调度 2026-09-20）：影子副本陈旧**（生产实有股票分钟数据；BR 09-12 备份与 SNAP003 08-25 快照两个批准影子源均未含 2026-02~05 股票分钟）→ 影子库数据覆盖缺口连带连板梯队三轮密集窗零成交的归因（数据覆盖非行情）；②`连板…py:1106 _QS_INDUSTRY_CODES = ()` 转换模板占位符未替换 → get_industry 行业过滤 fail-open 降级（不剔除，方向为多候选非抑制，QS_INDUSTRY_UNAVAILABLE 告警）
- **定位证据**：Phase 0 只读探针（两影子源 stock_minutes 2026-02-20~03-11 计数 0 行；BR etf_minutes 03-02~04 = 1,022,291 行）；**总调度定谳原文（2026-09-20）为权威引用**；策略文件 :1106 占位符
- **严重等级**：中（影子库陈旧 + 转换管线质量；不影响本立项性能结论——锚点复现与客户归档同域同行为）
- **当前状态**：**台账关闭追踪、留档（2026-09-20 定谳后更新）**
- **归属迭代**：超出本项目范围；**补救 = 影子库回补/刷新，归数据拉取线（总调度处置三路之"回补评估线"闭环）**；行业码占位符项另归转换管线属主（主线 f2d588f 已修——2026-09-20 主仓提交《行业码提取器判据重写》，M1 段门同步带入）
- **修复方案（候选）**：影子库回补（数据拉取线）；**本项目不得为验证生产数据而触碰生产库（定谳裁定原文）**
- **验证证据**：Phase 0 探针输出 + 三轮 TRIAL 日志 + 总调度定谳记录
- **关闭时间**：2026-09-20（本项目台账关闭；补救归数据拉取线）

## ISS-005

- **问题描述**：分钟档引擎无 QS_FILL_AUDIT 审计行产出——`_emit_fill_audit`（backtest_engine.py:790-824）仅日线路径调用（:602 注"仅日线 Profile 输出"）；分钟档回测无日末审计行（M0 实证：U1/U2/U3/U5 audit=0，U4 日线 audit=1）
- **定位证据**：M0 自校验五用例 report.json（output/double_run/U*_20260920_*/report.json）；源码 backtest_engine.py:602/:790-824
- **严重等级**：待属主判定（可观测性设计缺口候选，非正确性问题）
- **当前状态**：候选（登记 2026-09-20，M0 验收裁定随附指令）
- **归属迭代**：**超出本项目范围**——是否属设计缺口由属主判定；双跑对比器口径按现状执行（日线比审计行 / 分钟档 trades+nav 位级主通道，2026-09-20 M0 验收裁定①）
- **修复方案（候选）**：—（属主判定后另立）
- **验证证据**：M0 五用例自校验报告
- **关闭时间**：—（本项目台账挂候选，不追踪）

## ISS-006

- **问题描述**：`inspect_capabilities.py` 的 F6 参考数据能力 `industry_classification_sw2021` 在当前生产库判 **BLOCKED**（`industry_classification missing/incomplete or contains pseudo SW_ codes`）——SW2021 行业分类数据缺失/不完整，使该能力恒红。
- **定位证据**：2026-10-03 A 项门禁窗口跑（`--db data/quantstudio.db --profile daily-bar-v1`）报告，**修复前/后两侧均为 BLOCKED**；`tests/test_inspect_capabilities_f6.py` 因此在本库无法通过（能力循环按字母序，先撞 `index_constituents*`，A 项修复后往下露出本项）。
- **严重等级**：中（`required=False` 不参与 overall 门禁，overall 仍 READY；但使 F6 测试恒红，**掩盖后续真实回归**）。
- **当前状态**：**待处置（数据线）**——A 项门禁修复（新增检测型）**不扩大范围**处理本项：`index_constituents` 两能力已按实测更新期望并转绿，本项与其后项保持红并在此登记。
- **归属迭代**：数据线（industry_classification 数据回补）。
- **修复方案（待定）**：核查 `industry_classification` 表数据来源与完整性（是否含 pseudo `SW_` 代码）；补数后 F6 该能力复判。
- **验证证据**：2026-10-03 窗口 BEFORE / AFTER 两侧能力报告（均 BLOCKED）；能力 message 原文见上「定位证据」。
- **关闭时间**：—

## ISS-007

- **问题描述**：F6 参考数据能力 `sw_l1_index_daily` 判 **BLOCKED**（`SW index daily coverage/quality/routing/resident check failed`）——SW L1 指数日线的覆盖/质量/801 路由/resident 路径检查未通过。
- **定位证据**：同 ISS-006（2026-10-03 窗口报告，修复前后两侧均 BLOCKED）。
- **严重等级**：中（同上：不参与 overall 门禁，但使 F6 测试恒红）。
- **当前状态**：**待处置（数据线）**。
- **归属迭代**：数据线。
- **修复方案（待定）**：核查 SW L1 指数日线覆盖与质量门、801 路由数据、以及正式 resident 采集路径是否可达（能力 message 已列四类失败面）。
- **验证证据**：同 ISS-006。
- **关闭时间**：—

| 2026-10-10 | D-2b：QMT 回测模式 ACCOUNT 查询空 → portfolio.cash=0（M5.1b ②审批准暂缓） | 回测 183 天 cash_ratio=0.0000（22:42 日志） | P2 | open（BLOCKED 用户裁定暂缓，解除条件=首个持仓交易链复测） | M5.1c 候选 | 待回测出现真实持仓后重评影响面 |
| 2026-10-10 | stub 保真度第二批：qmt_stub get_market_data_ex 已原生支持窗口（:623），但 stub 侧 count 形态仍存在（-1 默认）——与真实 QMT count=全局最新 的保真对齐仍未做 | M5.1a R1 + M5.1b 复核 | P3 | open | stub 保真度批次 | 本批 smoke 6 passed 证明窗口路径兼容 |

| 2026-10-11 | D-12（已修待复测）：get_stock_list_in_sector realtime 参数误传布尔 1（=时间戳 1970）→ 空池 selected=0 | 复测日志 00:54 FUN A=0 + 07:3309 文档定谳 | P0 | fixed-pending-retest | M5.1c 微批（直达闭环授权内，同轮修复） | 文件7 复测 A>0 即关单 |

| 2026-10-11 | D-13（已修待复测）：ExpireDate=99999999（int YYYYMMDD 永续）被旧代码按毫秒 /1000 → 1970-01-02 → now()>1970 恒真 → 5227 只全判退市（F=0） | PROBE 02:31 日志（4/4 详查+census 100 只）+ 复测 01:48 F=0 零 UNDETERMINED | P0 | fixed-pending-retest | M5.1c 微批（直达闭环授权内） | 38 passed+矩阵零漂移+文件7 sha 17af261996c02af6；复测 F>0 即关单 |
| 2026-10-11 | D-12 关单更新：01:48 复测 A=5227（原 0）实证通过——单参 get_stock_list_in_sector 唯一可用形态（realtime 实参任何值回测模式均空） | 复测 01:48 FUN A=5227 + PROBE 01:45 定谳 | P0 | fixed（已复测关单） | M5.1c | 证据 §8.1；ISSUES 原 fixed-pending-retest 行据此关闭 |

| 2026-10-11 | D-14（候选·取证中）：D-13 打通 F 后文件7 回测卡在 init 后首 bar——_load_history 500码/批×11批窗口 RPC（260根×550自然日）吞吐风暴嫌疑（QMT 回测懒加载+每bar重复全池），FUN 审计行后置致加载期静默 | 产物 :1428(分批500)/:1342(FUN行在加载后)/:323(批量RPC)；02:38 复测日志卡点 | P1 | investigating | M5.1c 微批 | PROBE 文件8 吞吐取证中 |

| 2026-10-11 | D-16a（已定谳）：QMT volume 单位=手（04-数据结构.md:24 股手转换后），本地/PTrade 语义=股——产物 close×volume 成交额低估 100 倍，3000万流动性门槛实际要求 30亿等价 | 文档行级引用 + D-15 探针（平安 amt20=1123万 vs 真实~15亿） | P1 | confirmed（修复随 D-16b 取证后一并实施） | M5.1c 微批 | 依据 docs/qmt/inner-api/04-数据结构.md:24-25 |
| 2026-10-11 | D-16b（取证中）：500 码批 get_market_data_ex 疑瞬态竞态静默返回空 df（:1423 自注教训）——H=4414 计键数含空壳（:351 空 df 也入 out），茅台单码 pass=True 但回测 q=0 矛盾待判 | D-15 探针茅台 pass=True vs 回测 q=0；D-16 探针部署文件8 | P1 | investigating | M5.1c 微批 | batch500 nonempty 统计取证中 |

| 2026-10-11 | D-16b 关闭：500 码批 get_market_data_ex 竞态假设排除——D-16 探针 returned=500 nonempty=500 rows 全 365 满行（elapsed 14.45s） | 探针 02:56 输出 | - | closed（假设证伪） | M5.1c 微批 | 亦解释每 bar ~10s 构成（11 批 × ~1.3s） |
| 2026-10-11 | D-16a 实施完成（fixed-pending-deploy-retest）：wrapper 双层 volume ×100 股数换算（_qs_fetch_bars 后处理 + _QmtDataBar 快照）；pytest 36 passed（新增 4：换算核心/回归锚/边界/快照）；六产品重转 +942B 机械同构；**file7/file8 被 QMT 客户端回测后自动改写为 token 加密形态（file7 92094B/file8 4162B，时间相关定谳），部署落点待用户裁定** | source_import_qmt.py :435-437/:581-592；证据 §8.4 | P1 | fixed-pending-deploy-retest | M5.1c 微批 | 判型=数据修正型（影响面=volume 消费路径） |

| 2026-10-11 | D-17（已修复待复测）：get_history 纯关键字形态 field= 路由洞——count=/frequency=/field=/security_list= 全关键字不进 int 重映射分支，field 丢弃 → fields=None → wrapper 默认 ['close'] → high/volume 未请求 → q=0 全灭（H=4414 因 close 在而自洽；探针直调 get_market_data_ex 测不出）。D-16a 换算未失效，volume 列根本不在返回 | 产物 :1009-1046 适配层 + 03:12 复测 q=0 恒定；source_import_qmt.py get_history :1284+ 无条件归一 | P0 | fixed-pending-retest | M5.1c 微批 | 判型=数据修正型；pytest 39 passed（+3：kw 路由核心/签名A锚/签名B锚）+ stub 冒烟 6 |

| 2026-10-11 | D-17 复测通过关闭：FUN q=4135→4216（98% 量级吻合探针预估）、b=5→14 非零、selected=5 达标（target=5）、tradable 打开——三层修复链（D-13 状态 + D-16a 单位 + D-17 字段路由）实证闭环 | 03:24 复测日志 FUN/REBALANCE 行 | - | closed | M5.1c 微批 | M5 漏斗验收目标达成 |
| 2026-10-11 | D-18（新，P0 investigating）：回测模式资金源恒 0——cash_ratio/gross_exposure=0.0000、buy_submitted=0 恒定；_QmtPortfolioView.cash 依赖 C.accountID + get_trade_detail_data('ACCOUNT').m_dCash（产物 :90-100），回测语境下 accountID 属性/ACCOUNT 表形态未实测；per_value=0 → 无买单，回测空仓 | 产物 :90-100 + 03:24 PORTFOLIO_AUDIT 全零行 | P0 | investigating | 待授权探针 | 需探针定谳 QMT 回测资金源（C.capital? accountID? ACCOUNT 字段名?） |
| 2026-10-11 | D-19（新，P1 investigating）：get_fundamentals 每 bar 报 invalid literal for int() 'NaT'——财报 m_timetag/m_anntime 存在 NaT 行，int() 转换无防御；策略 try/except 降级 → EPS 因子缺失（CANSLIM C 分量），selected=5 为纯价量选出 | 产物 :918-919 映射 + 03:24 warning 行每 bar 出现 | P1 | investigating | 待授权修复 | wrapper 层 NaT 行防御（skip 而非 crash） |

| 2026-10-11 | D-19 已修复待复测：wrapper _qs_get_fundamentals :1210 毫秒→'YYYYMMDD' 过度归一移除——本地契约为 epoch 毫秒数值（CANSLIM:389 to_datetime(unit='ms') / weekly:154 / 周频三层:150 astype(float)；五策略消费形态全查证）；缺失毫秒→NaN 契约行（消费方 dropna/fillna 天然剔除）；pytest 41 passed（+2：毫秒直通核心/缺失 NaN 边界） | 模板 :1206-1217 + 策略源 :389 实证 | P1 | fixed-pending-retest | M5.1c 微批（用户已授权） | 判型=数据修正型 |

| 2026-10-11 | D-18 已修复待复测（四处模板）：①_QmtPortfolioView.cash 实盘链字段名修正 m_dCash→m_dAvailable（08:655）+ 回测 fallback capital 推导；②_qs_get_trade_detail 账号解析链 C.accountID→模块级 account→''（08:623）；③POSITION_EXT 模块加载期捕获 _qs_native_otv（探针 v3 实证模块 globals 预注入）；④_qs_order_target_value 回测语境（无 accountID）转发 QMT 原生 order_target_value（08:1517 引擎虚拟账户自管差额/可卖仓位，根治每 bar 重复建仓+卖单全灭）。白名单登记 _qs_native_otv。门禁：pytest 46 passed（+5 D-18）+ stub 6 + portability 族 66；六产品重转+portability 全 PASS；矩阵 reverify 7a424f397983 OK | 探针 v1/v2/v3 日志 + 模板 :307-327/:655-672/:775-810 | P0 | fixed-pending-retest | M5.1c 微批（已授权） | 判型=数据修正型+新增能力型 |

| 2026-10-11 | 计划外发现（不越权处置）：check_fund_matrix.py 矩阵门禁 SRC=source_import.py（PTrade 管线，AGENTS.md 铁律字面范围）；QMT 管线 source_import_qmt.py 模板串无对应矩阵门禁——wrapper 改动（D-16a/D-17/D-18/D-19）均无哈希登记面。建议后续为 QMT 管线建同款矩阵或扩 SRC 覆盖，由用户决策 | scripts/check_fund_matrix.py:23（SRC 定义）+ AGENTS.md 矩阵哈希追认同批纪律条 | P3 | open（登记待裁定） | 待立项 | 纯登记，零改动 |

| 2026-10-11 | D-18 复测通过关闭（16:05-16:16 全量回测 0105→1009 九个月无崩溃）：①buy_submitted 打开（rebalance_id=1 首批 5 单）②cash_ratio 非零（0.0165 起点，随买卖波动至清仓 1.0）③positions 动态变化 0→5→4→3→2→1→0（原生 order_target_value 引擎虚拟账户管理生效）④卖出链打开（sell_submitted 止损触发多次：300643 -9.8%/300214 -11% 等正确卖出）⑤止损链运转（STOPDBG ratio 逐日跟踪） | 16:05-16:16 复测日志 REBALANCE/PORTFOLIO/STOPDBG 行 | P0 | closed | M5.1c 微批 | 主链全通；残余见 D-20 |
| 2026-10-11 | D-19 残余（新定谳 investigating）：NaT 报错仍在（每次 FUN 行）——第一层修复（毫秒直通）正确但不足；第二层根因：wrapper _qs_get_fundamentals 返回单行 frame（index=codes 每 code 一行，:934）vs 本地多行历史契约（CANSLIM:215 '全部历史行'+:386-388 reset_index+groupby('code') 证多行同 code）——策略 _compute_eps_factors 需要跨季度序列算 EPS 加速/CAGR；且 QMT 数据源缺失行（NaN end_date）未剔除，策略 :392 astype('Int64') 在 :394 dropna 之前炸 | 策略源 :215/:383-394 + 16:05 复测 NaT 行 | P1 | investigating | 待授权修复 | 修复落点：wrapper 返回多行历史 frame + NaN 行剔除（对齐本地契约） |
| 2026-10-11 | D-20（新登记 P3 显示层）：PORTFOLIO_AUDIT gross_exposure 恒 0.0000——positions_value 走 wrapper 持仓查询（回测语境恒空）→ 显示近似缺陷，不阻断回测（策略自管持仓数正确显示 positions=0→5→…） | 16:05 复测 PORTFOLIO 行 | P3 | open | 待裁定 | 回测无持仓查询通道（探针 v1/v2 定谳）；可后续以 capital+策略自管市值近似 |
| 2026-10-11 | D-19 二层修复完成待复测：wrapper 历史表（eps/growth_ability/profit_ability）返回 PIT 全报告期多行 + 完整行过滤（NaN 行剔除）；valuation 快照路径零改动；49 测试全绿 + 六策略 portability 全 PASS + CANSLIM 部署 sha16=522db759 | source_import_qmt.py _qs_fin_to_rows_hist 新增 + _qs_get_fundamentals 历史分支；证据 §8.7 | P1 | fixed-pending-retest | M5.1c 微批 | 复测判据：①FUN 行后无 NaT warning ②EPS 因子恢复（selected 基于 composite 含 C/A 列）③漏斗维持 |
| 2026-10-11 | D-21 产物编码契约违规（临时脚本 UTF-8 落盘致 QMT GBK SyntaxError）已修复：新增 scripts/qmt_reconvert_six.py canonical 重转门（契约常量驱动 + 四自检），六策略 GBK 重转全 PASS，CANSLIM sha16=23f79b1a 部署待复测 | scripts/qmt_reconvert_six.py 新增；证据 §8.8；纯恢复型 | P1 | fixed-pending-retest | M5.1c 微批 | 复测判据：QMT 端无编码 SyntaxError，回测正常起跑 + D-19 三判据（无 NaT / EPS 恢复 / 漏斗维持） |
| 2026-10-11 | D-19/D-21 复测：①②④ PASS（无编码错误/NaT 零出现/漏斗维持，183 轮全区间跑完）；③ composite skipna 归一致盲点 → 新增 QSFUNDHIST 审计行（新增检测型）部署 sha16=fc8e76d1，待复测 kept>0 关闭 | §8.9；门禁拦截 ⟺ 案例在录 | P1 | retest-partial（①②④过，③待审计行） | M5.1c 微批 | 复测看 FUN 行后 QSFUNDHIST t=eps kept>0 |
| 2026-10-11 | D-22 get_financial_data endDate 空串致返回空（17:31 QSFUNDHIST cc=0 定谳）：必填参数空串未定义行为；修复=缺省补 20991231 安全终点 + 审计行增 raw 形态；文档依据 Context7 dict_thinktrader_net | §8.10；快照 9f349c05；sha16=330e16e5 已部署 | P1 | fixed-pending-retest | M5.1c 微批 | 复测 QSFUNDHIST kept>0 且 raw 形态可见 |
| 2026-10-11 | D-22 复测全过：QSFUNDHIST kept=12015+ cc=n 全覆盖，raw=Panel 确认；判据③（EPS 因子恢复）关闭——首批选股 diff（002908 出/603055 进）可归因于 C/A 因子生效。D-18/D-19/D-21/D-22 四案全关 | §8.11；17:45 复测日志 | P1 | closed | M5.1c 微批 | 无 |

## E7 登记 2026-10-12（M5.2 件2 对齐执行器 · ZCode 委派）
- 委派：zcode_code（sess_d8f66a9e，tokens 211.7万 in/out）；任务包 docs/zcode-taskpkg-m52-item2.md（O4 七件套）。
- 产物：scripts/qmt_align_check.py（634行）+ tests/test_qmt_align_check.py（290行），仅两新文件零既有改动（diff 核对过）。
- 验收五步：①范围 ✓ ②pytest 17 passed（ZCode 侧无执行权限，DSH 侧代跑）③纯标准库+零引擎 import（AST 用例机检）④--help 契约 ✓ ⑤新测试绿+M5.2 套件零衰减。
- 【本模块由 ZCode-GLM 外部生成（E7）；已过本地机检（pytest 17/17 + 契约冒烟），请人工复核归因三分与容差表业务逻辑】

## D-23 登记 2026-10-12（QMT `get_stock_info` 日期契约键缺失致 L1 全滤）

| 日期 | 问题描述 | 复现步骤/定位证据 | 严重等级 | 状态 | 归属迭代 | 修复方案/验证证据 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-12 | D-23（P0，已修复待端测）：QMT 端 `QMT_WEEKLY_SMALLCAP` 回测零交易——漏斗 `L0_all=5227 → L1_listed=0`。根因：wrapper `get_stock_info` 按 `field` 白名单取键（`source_import_qmt.py:1531-1533`），而 `_qs_get_stock_info` 返回字典只有 QMT 原生字段名（`start_date`/`end_date`/…），**无本地契约键 `listed_date`** → `field=['listed_date']` 恒 None → 策略 L1 `if not listed: continue`（`weekly_smallcap_growth_momentum_10_quantstudio.py:180-182`）全滤。数据源侧 QMT `OpenDate=19910403`（int YYYYMMDD）真实可用；本地契约（`ptrade_api.py:2481-2523`）listed_date 为 `'YYYY-MM-DD'` 字符串 | wrapper :900-910 键表 + :1531-1533 白名单取键 + 策略 :176/:180；QMT 端 FUNNEL_AUDIT 行 L1_listed=0 | P0 | fixed-pending-retest | 修复=新增 `_qs_norm_ymd` 归一 helper（int/str YYYYMMDD → 'YYYY-MM-DD'；0/负/None/bool/非 8 位/非法月日含 99999999 → None）+ `_qs_get_stock_info` 增 `listed_date`/`de_listed_date` 两契约键，**既有 9 键零改动**（纯新增 42 行、零删除）。证据 `docs/evidence/qmt-l1-listed-date-norm-20261012.md`；新增单测 24 passed（含 L1 阻断回归闸）；既有 49 用例零衰减；六策略重转 api_portability 全 PASS；六份产物统一 +2007B 可归因；矩阵 `qmt_wrapper_hash=c45941bf5399` 同批追认 |
| 2026-10-12 | D-23 端测判据（候用户执行）：GBK 产物经剪贴板粘入 QMT 保存后回测——主判据 `L1_listed > 0`（预期与 L0_all=5227 同量级）→ `L6_eps>0` → `R_selected>0` → 产生交易；回归判据 `get_history` 族（D-16a/D-17）与 `QSFUNDHIST`（D-19/D-22）不回退 | 候 QMT 复测日志 | P0 | retest-pending（用户端） | M5.2 件8 微批 | 用户已批本轮即端测 |
| 2026-10-12 | 零越权登记（本轮未修，候批）：QMT wrapper `get_stock_info` 其余键名与本地契约存在差异——本地（`ptrade_api.py:2511-2519`）为 `stock_name`/`stock_type`/`exchange_type`/`code`，wrapper 侧为 `display_name`/`name`（无 stock_type/exchange_type/code）。策略侧零消费（12 策略 / 54 处消费点取证，全部只用 `listed_date`），故本轮按「不扩大范围」未改 | wrapper :900-910 键表 vs 本地 :2511-2519 契约表 | P2 | open（登记待裁定） | 待立项 | 修复候选：wrapper 增补 `stock_name`/`stock_type`/`exchange_type`/`code` 契约键（纯新增，零回归）；是否后批由用户决策 |
| 2026-10-12 | D-24（新，P2 可观测性缺陷，候批）：QMT 端回测启动后日志停在 `start back test mode` + `QS_QMT_DENY_REMOVED: set_benchmark`（16:34:05）→ 用户判为「卡住」。**实为漏斗首次真实计算期间全程静默**：`init` 已完整执行（产物 :1621-1629，打印后仅剩 `g.initialized = True`）；卡点在 `handlebar` 首日 `_select_weekly_targets`，而 `QS_FUNNEL_AUDIT` 仅在**整个漏斗返回后**才打印（产物 :1655）→ 期间无任何中间输出，无法区分「在算」与「卡死」 | 产物 :1621-1629（init 结构）+ :1655（审计行出口）+ :1544-1579（漏斗各层） | P2 | open（登记待裁定） | 待立项 | 修复候选：漏斗分层进度审计行（L1/L2/L3/完成 各一行）或首日一次性心跳行；属框架改动，须走六步流水线 |
| 2026-10-12 | D-25（新，P3 性能冗余，候批）：首日 `get_instrument_detail` **重复取数约 9700 次**——L1 `get_stock_info(all_codes, field=['listed_date'])` 逐只 5227 次（产物 :1323-1333 → `_qs_get_stock_info`）+ L3 `filter_stock_by_status` 对同一批再逐只 `_qs_status_flags` → `get_instrument_detail` 约 4500 次（产物 :832-838）。两处消费同一 API 同一快照，可合并为单次批量取数 + 会话级缓存 | 产物 :649-706 / :814-838 / :1323-1333 | P3 | open（登记待裁定） | 待立项 | 修复候选：`_qs_get_stock_info` 与 `_qs_status_flags` 共用会话级 `get_instrument_detail` 缓存（resource 纯性能型，须附黄金对照）；属框架改动，须走六步流水线 |
| 2026-10-12 | D-24 ③实施+④验收完成（判型＝新增检测型）：五处落点落地（`_QS_QMT_LOG_EXT` helper 局部 import time + `filter_stock_by_status`/`get_stock_info`/per-code 三处 tick + `get_financial_data` begin/end `finally` 配对含 `err=` 尾键）。实施中自测发现契约边界缺陷（t0=0.0 → elapsed=epoch 天文数字，会误导判读），生产路径不受影响，按局部阻断修复分级豁免直接加固。验收：新单测 15 passed（判据 a/b/h/i 全绿，含 vs 改动前副本的真黄金对照）+ 既有 49 用例零衰减 + 六策略 ALL 6 PASS (enc=gbk) + 产物统一 +2969B 可归因 + `qmt_wrapper_hash=5c3586666747`（yaml+md 同批，--check 绿） | diff 108+/3-（3 删除＝3 处 for 头 enumerate 等价改写）；改动面 5 项零越界 | P2 | fixed-pending-retest | M5.2 件8 微批 | 证据 docs/evidence/qmt-progress-audit-evidence.md；端测候用户（D-23+D-24 并轮）；**正式 archify 影响面图未出**（本会话 context 不足，非工具故障，不属条款降级情形，已如实申报，候下轮补齐） |
| 2026-10-12 | D-25 **优先级下调**（2026-10-12 端测实测修正，用户裁定③）：原假设「L1/L3 重复 `get_instrument_detail` 约 9700 次为主要浪费」被实测推翻——`get_stock_info` 5227 只仅 **0.3s**、`filter_stock_by_status` 3816 只仅 **0.1s**（合计 0.4s）。真实耗时大头：`query_last_close` 3666 只 **115.0s**（65%）、`get_financial_data` valuation 11.2s + growth_ability 49.8s（33%）。修复收益评估：合并缓存收益 ≤0.4s，**性价比极低** → 由 P3 降为 P4/暂缓，让位 D-26（hist 段规模化卡死） | 20:34-20:37 QMT 端测 `QS_QMT_PROGRESS` 行（elapsed 字段） | P4 | open（暂缓） | 待用户重新裁定 | 若将来 L1/L3 规模显著放大（如全市场 + 多表）再评估 |
| 2026-10-12 | **D-26 全卷闭环（四轮递进）**：D-26（进度 helper + `to_frame()` 批量 + `to_dict` 取代 `iterrows`）→ D-26b（`.loc` 失败回落 → 修 `kept=0` 零交易事故）→ D-26c（结构探针定谳：`to_frame()` index=(major=日期, minor=字段)、columns=股票代码，`.loc[code]` 方向错；契约超量 200×=8739 行/只）→ D-26d（`dropna(how='all')` 删全 NaN 行，纯性能型）。**端测贯通**：`L1_listed=4410 → L3v_complete=3663 → L4=366 → L5=30 → L6=17 → R_selected=10`，`buy_submitted=7`、持仓 7 只、总资产 1,003,261.49、`note=ok`；hist **509s → 127/109s（4×）**；全区间 10 分钟 | 端测日志 23:17–23:27；证据 docs/evidence/qmt-d26-hist-perf-evidence.md | P0 | fixed-pending-retest（端测已过，候⑤呈批） | M5.2 件8 微批 | 70 passed 零衰减 + 六策略 ALL 6 PASS + `qmt_wrapper_hash=75108c301bf3`；**教训固化**：self-made mock 无法代替真实结构（D-26 黄金对照本地全绿但真实环境破） |
| 2026-10-12 | D-27（新登记，候批）：① 按 `m_anntime`（公告日）过滤 hist 行 → 2654 → ~40 行/只，目标 10 分钟 → **1 分钟**；② `query_last_close` 3666 只 121s → 批量化；③ **全策略 `get_fundamentals` 消费面 mask 审计**（D-26d 口径残留风险：若有消费者依赖 NaN 行则行为变） | 端测性能画像（证据 §3.2 / §5） | P2 | open（候批） | 待立项 | 三项均属框架层，须走六步流水线 |