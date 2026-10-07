# 大 QMT 转换管线 · 架构定稿（M1，2026-10-07 呈②审）

> M0 已批（确认节点 1）；本件=架构方案定稿，**呈 ZCode ②审**后 M2 实施。
> R2 四项勘察已完成（每条带 ctx7 依据），一项含 M5 实测验证标注。

## 🔹 M1 Plan-Mode 执行记录（八项）

① 阶段：架构定稿（M0 基线已批）；② 目标：R2 四项闭合+可实施架构定稿；③ 分工：主导=本会话
（PDO 载体），②审=ZCode，终审=用户；④ 事项：ctx7 四项勘察（只读，已完成）+本文件；
⑤ 待确认：本定稿②审；⑥ 风险：见 §5；⑦ M2 实施另出 Plan；⑧ 验收=R2 四项有依据+
模块接口可实施（M2 可直接照做）；回退=纯文档无回滚。
⑧·判型：M1 纯文档；管线整体维持新增检测型声明。

---

## 1. R2 勘察结论（四项闭合）

| # | 项 | 结论 | 依据（ctx7 `/websites/dict_thinktrader_net`） |
|---|---|---|---|
| ① | 盘前钩子等价 | **`ContextInfo.run_time(func, time_point, interval, repeat_times)`** 定时回调：注册每日固定时刻（如 09:25）触发 `func(C)`——before_trading_start 映射通道确认。**限制**：run_time 周期语义（interval+repeat_times=0 无限重复）在**回测模式**下的驱动行为文档未明——回测优先策略（四问①）M2 渲染时以「handlebar 内时点判断」为主实现、run_time 为实盘增强路径，**M5 实测裁定** | innerApi run_time 函数页（func/time_point/repeat_times 签名实测） |
| ② | 持仓查询 | **`get_trade_detail_data(accountID, 'STOCK', 'POSITION')`**；资金='ACCOUNT'，委托='ORDER'，成交='DEAL'——get_positions/get_account 全映射面确认；账号在策略交易界面运行时自动赋值（编辑器运行需手填——与回测先行兼容：回测账号任意串） | innerApi get_trade_detail_data 页（参数枚举实测） |
| ③ | include=False 等价 | QMT **无显式 include 参数**；等价组合=`C.barpos`（当前 K 线位置）+`is_last_bar()`+`get_market_data` count 语义（count>=0 取 N 根）。**E1 铁律迁移**：信号取 D-1 = 渲染层保证信号 API 取数**多取一根取 `[-2]`**（对齐本地 `count>=2 取[-2]` 既有惯例）——「最后一根是否含当前未完成 bar」在回测/实盘语境有差异，**M5 实测验证后钉死** | is_last_bar/barpos/get_market_data count 语义页 |
| ④ | ETF 动态池 | **`xtdata.get_stock_list_in_sector(sector_name)`**：板块成分股/基金合约（含 ETF/LOF，如'沪深300'）——get_etf_list_local 的 QMT 注入等价确认；PIT 语义（历史时点池）QMT 侧仅最新快照——**双端策略静态池铁律本就要求转换期固化 ETF_POOL_STATIC**（四象限先例），无 PIT 缺口 | get_stock_list_in_sector 页 |

## 2. 模块设计（M2 实施清单）

### 2.1 新增/扩展面

| 模块 | 动作 | 接口（定稿） |
|---|---|---|
| `render_qmt.py`（**新文件**） | QMT 渲染器 | `render_qmt(ir: StrategyIR) -> str`（返回 gbk 待写盘的源码文本）；内部复用 `_build_template_context` 语义装配+QMT 专属上下文（生命周期映射/注入 wrapper 集合） |
| `render.py` | 分发扩展（最小侵入） | `render_strategy` profile 分发表加 `qmt`→`render_qmt.render`（3 行级，不动既有两分支逻辑） |
| `templates/qmt_strategy.j2`（新） | QMT 模板 | `#coding:gbk` 头+`_QS_QMT_*` 注入区+init/handlebar 主体+（盘前）handlebar 内时点判断段 |
| `portability_rules.py` | QMT 白名单分支 | 允许集：C.* 属性/get_market_data(_ex)/passorder/get_trade_detail_data/xtdata.get_stock_list_in_sector；**禁用集**：get_history_data（官方 deprecated） |
| `contracts.py` | profile='qmt' 契约 | 生命周期映射+编码契约（gbk）+E1 D-1 语义契约 |
| `publish.py` | gbk 写盘 | 按 profile 选编码：qmt→`encoding='gbk'`（其余路径不动） |
| `source_import.py` | QMT 注入 wrapper 模板 | `_QS_QMT_ORDER_EXT`（order(n)→passorder 包装：拒单/资金容错吸收）+`_QS_QMT_HISTORY_EXT`（get_history→get_market_data 重定向+D-1 取行）+`_QS_QMT_POSITION_EXT`（get_positions→get_trade_detail_data 视图）+`_QS_QMT_ETF_POOL_EXT`（静态池直灌）——**矩阵哈希追认同批纪律适用** |
| `cli.py`/`orchestrator.py` | profile 透传 | `--target qmt` 参数链 |

### 2.2 生命周期映射定稿表

| 本地/PTrade 语义 | QMT 渲染 | 备注 |
|---|---|---|
| `initialize(ctx)` | `def init(C):` | g.*→C.*_attr（C 动态属性）；账号：回测='testS' 占位+注释头声明 |
| `before_trading_start(ctx, data)` | handlebar 内时点判断段（`C.barpos` 首根/固定时刻分支）为主；run_time 注释模板备实盘 | R2①限制项 M5 裁定 |
| `handle_data(ctx, data)` | `def handlebar(C):` | data[code] 面→注入 `qs_data_snapshot(C, codes)`（get_market_data 实时段） |
| `after_trading_end` | handlebar 尾分支（is_last_bar 判定） | 语义等价待 M5 复核 |
| `order(code, n)`/`order_value` | 注入 `_qs_order(code, n, C)`→passorder(23,1101,...) 包装 | 数量语义保持（零股 CORP-02 经验：卖出全量语义直传） |
| `get_history(..., include=False)` | 注入 `_qs_get_history(code, n, ...)`→get_market_data(count=n+1)+取`[-2]` | E1 铁律渲染层保证（R2③） |
| `get_positions()` | 注入 `_qs_get_positions(C)`→get_trade_detail_data POSITION 视图 | R2② |
| `get_etf_list_local()` | 转换期固化 ETF_POOL_STATIC（双端铁律）+注入静态直灌 | R2④ |

### 2.3 oracle L0 静态校验（M3）

- portability QMT 白名单全 PASS（禁 API 拦截）；
- gbk 解码+AST 编译过（`compile(src.decode? / ast.parse(gbk_text))`——产物语法级验证）；
- 生命周期完备性：init/handlebar 必在+注入区哈希登记。

## 3. 判型与不变面（复述）

新增检测型/纯增益：render 分发 3 行外 PTrade 管线零触碰；`api_portability` 六策略回归+
既有测试套件全绿为证（M3 验收硬门）。策略源码零改动（重转生效）。

## 4. M2 实施计划预告（批后另出 Plan-Mode）

M2a render_qmt+模板+单策略打通（四象限——双端铁律已固化静态池，转换面最小）→
M2b 注入 wrapper 四件+portability 分支 → M3 六策略横验证+L0 校验+回归全绿 →
M4 文档同步 → M5 用户域实测（R2①③ 裁定项）→ M6 推送批。

## 5. 风险（诚实申报）

1. R2①③两项 M5 实测前为「设计假定」——M2 渲染实现按主路径写，M5 若裁定相反则渲染层
   单点改（注入层吸收，策略零改动——框架层铁律兜底）；
2. passorder 拒单/资金不足容错面复杂——包装层先对齐 PTrade 侧 `_QS_ORDER_SPLIT_EXT`
   既有容错语义，差集 M5 实测补；
3. gbk 不可编码字符（策略名/注释生僻字）——渲染层转码失败即 BLOCK 报错（fail-closed，
   不静默替换）。

**暂停语义**：本定稿呈②审（ZCode）——审过后 M2 另出 Plan-Mode 实施计划呈批。
