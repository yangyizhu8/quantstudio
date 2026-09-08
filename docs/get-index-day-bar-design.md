# 恐慌抄底策略框架层修复方案（终版·终审通过稿）：新增本地注入 API get_index_day_bar

- 日期：2026-09-08
- 状态：终审通过（步骤1方案 + 步骤2审计完成），批准后实施
- 关联策略管线：agent_workspace/panic_bottom_fishing（R0-R2.5 已确认，R3 前技术阻断）

## 一、问题定义

恐慌抄底事件驱动逆向策略（panic_bottom_fishing）信号语义（客户 R0/R2.5 确认）：上证指数连续两个交易日跌幅均 > 1.5%，第二个信号日 T 的 14:55（≈收盘）确认后 T 日收盘满仓买入。

技术探测发现的框架阻断：
1. 上证指数不在任何引擎当日快照（duckdb_data_access.py::_snapshot_sql 仅 stock_daily UNION ALL etf_daily），data['000001.SS'] 返回空 BarData；
2. daily-bar-v1 下 get_history(include=True) 被校验器 NO-LOOKAHEAD-INCLUDE 硬 BLOCK；no-lookahead 契约硬闸 #2（T日收盘进信号且同日成交）为 const BLOCK 不可配置；
3. INDEX_ETF_MAP 代理仅覆盖 000300/000905/000016/000852 且仅在 get_history 查空时兜底，上证指数（000001）不在其中。

客户裁决（2026-09-08）：不采用 ETF 代理（510760），要求框架层改动。

## 二、方案比选结论

- 方案A（指数行并入共享快照）否决：000001 裸码与平安银行（000001.SZ）冲突，DataDict 裸码解析与 prices dict _to_qmt 归一双静默错配；
- 方案B（include=True 契约豁免）否决：动摇 no-lookahead 硬闸 #2；
- 方案C 采纳：新增 QuantStudio 本地注入 API get_index_day_bar，纯加法、引擎零改动、通用可复用。

## 三、详细设计（终审 6 项钉死条款已并入）

### 3.1 API 契约
- 签名：get_index_day_bar(security, count=1, fields=None) -> pd.DataFrame
- 返回"已完成"指数日线，行序时间升序，index=trade_date
- 不暴露 fq 参数（指数无复权，raw 即契约）
- fields 枚举写死：open/high/low/close/pctChg/volume/amount/trade_date；None=全列
- count 越界（<1 或 >250）→ 显性 ValueError（fail-closed），禁止静默截断【终审①】
- fail-closed：无连接/无数据 → 空 DataFrame；策略侧 fail-soft + 审计行
- 每次调用输出诊断日志：QS_INDEX_BAR code=... date=... rows=... mode=...【终审⑤】
- profile-aware"已完成"判定：
  - daily-bar-v1：上界=当前回测日 T end-of-day，含 T
  - daily-open-close-proxy-v1：以引擎既有回调上下文判定当前时钟（ptrade_api.py:1159 completed-bar 判定先例）；15:00 时钟含 T；09:31 不含 T；无法可靠判定 → fail-closed 不含 T（保守侧）【终审②】
  - minute-bar-v1：上界=上一完整交易日，永不含 T
- 数据约定（R1 实测 provenance）：index_daily 存裸码、时间戳=当日 00:00；.SS/.SZ/裸码归一为裸码查询。staging 库 000001 覆盖 2018-01-02~2026-09-04（2106 行），pctChg 2026-06~09 69 行全非空、量纲百分比

### 3.2 实现落点
1. duckdb_data_access.py：新增专用方法 query_index_day_bars(code, count, before_ms)——显式钉表 FROM index_daily WHERE code = ? AND time <= ?，不进 stock→etf fallback 链、绝不触发 INDEX_ETF_MAP ETF 代理替换；不动 query_bars_by_count_multi_table 既有代码
2. duckdb_provider.py：透传方法
3. ptrade_api.py（共享核心文件）：注入 get_index_day_bar——裸码归一 → engine_profile 判定"已完成"上界 → 专用查询 → fields 过滤/count 校验/trade_date 索引/QS_INDEX_BAR 日志
4. Skill 注册面：ptrade-api-signatures.json（API 条目 + local_only_symbols 登记）+ component-catalog.json；validate_agent_strategy.py 两新规则：PREOPEN-INDEX-BAR（before_trading_start 内调用 → BLOCK）+ MINUTE-PROFILE-INDEX-BAR（minute-bar-v1 设计内调用 → BLOCK）

### 3.3 PTrade 转换对齐（三件事）
① get_index_day_bar 登记进 local_only_symbols → TARGET-LOCAL-EXTENSION-BAN 对 PTrade 目标 fail-closed BLOCK（杜绝 set_backtest NameError 同型事故）
② 重写规则登记为后续项（本批不实施）：converter 重写为平台 get_history(..., include=True, fq='pre')，策略源码永不出现 include=True
③ 平台探针项按 D4 平台差异登记序列落编号；探针未过前含本 API 的源一律拒绝转换；三条执行状态写入证据文档

### 3.4 策略管线续跑
1. R1 补硬核验：000905（中证500）成分 PIT 的 index_constituents_snapshot_meta 完备契约——无 meta 的指数按纪律 DATA_BLOCKED
2. R2 设计修订：A-11 改为指数直接读数；required_apis 增补；r5_deployment_invariants 与涨停 fail-soft 语义一致性显式化（入场日候选全部涨停 → 低 gross exposure 合法，note=limit_up_skip_all_low_exposure_legal）
3. R3：实现 strategy.py（信号 / 中证500 成分 PIT + float_value 前50 / 剔 ST·停牌·成交额<3000万·科创·北交 / 等权满仓 / 锁仓20日无止损 / 涨停跳买留现金·到期跌停顺延 / QS_REBALANCE_AUDIT + QS_PORTFOLIO_AUDIT / _ensure_runtime_state）
4. R4：validate_agent_strategy.py 全 PASS
5. R5：副本库回测（data/staging/prehandover_20260905-223621/quantstudio.db），2026-01-01~2026-09-04、本金100万、daily-bar-v1 close；G3.5 两进程复现三件套 SHA-256 逐位一致；产出低频触发全周期复盘（2026-07-16/17 唯一触发：入场→锁仓20日逐日浮亏峰值→最大回撤→清仓）
6. R5.5 EXEMPTED；R6 发布 quantstudio/backtest/strategies/恐慌抄底事件驱动逆向策略.py

### 3.5 测试与验收标准
新增单测钉死断言：000001.SS 返回指数数据（断言非平安银行）；600519.SS→空；510300.SS→空；000300.SS 返回指数行（断言未触发 ETF 代理）；daily 含 T / minute 永不含 T / proxy 15:00 含 T·09:31 不含·时钟不可判 fail-closed 不含 T；fields 过滤；count 越界 ValueError；count=2 仅 1 行 → 返回实际行（fail-soft 路径断言）；fail-closed 空；后缀互通。
校验器单测：PREOPEN-INDEX-BAR BLOCK；MINUTE-PROFILE-INDEX-BAR BLOCK；quantstudio 通过；PTrade 目标 TARGET-LOCAL-EXTENSION-BAN BLOCK。
回归三证明：既有测试套件全绿；6 策略横验证全 PASS；1 个代表策略冒烟回测三件套 SHA-256 与既有黄金逐位一致。

### 3.6 交付物分轨（审计修正 2026-09-08：skills/ 在 git 仓库内被跟踪）
- skill 侧（skills/ 在仓库内 git 跟踪——修正初版「git 仓库外、无推送载体」之误；除提交进 commit 外，**必须同步至用户级权威副本 C:/Users/Administrator/.agents/skills/quantstudio-strategy-compiler**，防 ZCode 会话按用户级注册表误判 MISSING_REUSABLE_API）：validate_agent_strategy.py、ptrade-api-signatures.json、component-catalog.json、SKILL.md、api-capability-matrix.md（双副本 SHA-256 对照见证据 §四）
- repo 侧（推送载体）：duckdb_data_access.py、duckdb_provider.py、ptrade_api.py、新增测试、README.md、docs/strategy_toolbox.md、docs/prompt_engineering.md
- 共享核心纪律：ptrade_api.py 改动全套执行（stash create+store 回退点、精确清单 add、edit 后即时 git diff 自检）

### 3.7 R5.5 豁免证据
validation_contract.robustness_gates.enabled=false + R2.5 C3 豁免 verbatim 确认（原话+时间戳）入台账（skill 规则 33 唯一通道）

### 3.8 风险与回退
盘前/分钟误用 → 双校验规则拦截 + API 上界 fail-closed 双保险；指数数据缺失 → 空 DataFrame 策略 fail-soft；回退：git revert repo 侧三文件 + 设计字段删除，引擎核心零改动回退零风险

## 四、明确不做的事
不改引擎撮合/估值/快照/复权既有行为；不动 query_bars_by_count_multi_table；不放宽 include=True / no-lookahead 硬闸；不改任何既有策略源码；本批不做 PTrade 重写规则实施（仅登记+D4 编号+探针门禁）