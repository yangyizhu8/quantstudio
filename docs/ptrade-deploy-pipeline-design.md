# PTrade 回测代码 → 模拟盘/实盘 部署转换模块（qs-compile deploy）设计方案

> 版本：M1-v1（待审计）
> 状态：六步流水线第①步方案件——待 ZCode 审计（六步②归总调度呈审）
> 立项批复：总调度已批准立项（含三项修订 + 治理一条），本文件为批复的落地设计。
> 适用铁律：新功能模块添加纯增益+双端对等（global 常驻记忆 2b9a47c8）；新功能模块闭环收敛（ea05e93a）；六步流水线；框架层修复仅限框架层；E1 日线信号铁律；主仓双端推送后同步 trading 副本。

## 0. 立项批复引用与修订落实索引

| 批复项 | 内容 | 本文档落点 |
|---|---|---|
| 修订① | 公开仓边界声明——模块定位=转换工具（与转 PTrade tab 同族）；实盘部署操作细节不入公开仓，随沉淀走 trading 侧 | §2 |
| 修订② | 知识库 vault 落 QuantStudio-trading 侧（vault/knowledge/ptrade-deploy/ 建于 trading 仓）；主仓仅存 N_i/R_i 收敛台账+指针 | §8 |
| 修订③ | CLI=qs-compile deploy 子命令照准；首批横验证从池中选，若无分钟策略则以日线×3 替代并登记理由 | §7 / §9 |
| 治理一条 | AGENTS.md 铁律级固化属跨域治理动作，今后须先呈总调度备案（已入册，global 记忆 6576e07a） | §16.6 |
| 写死要求 | 双推+同步门+trading 侧沉淀同步铁律写死进 M1 | §16 |

## 1. 问题定义

### 1.1 背景与动机

PTrade 回测与交易共享同一策略框架（initialize / before_trading_start / handle_data / after_trading_end + 同名 API），但运行环境不同。当前从「PTrade 回测代码」到「可稳定运行的模拟盘/实盘代码」的转换靠模型逐次手写，存在三类问题：

1. **差异化幻觉风险**：不同模型/不同会话对平台 API 记忆不一致，产出不可复现；
2. **状态持久化缺失**：手写转换普遍遗漏官方持久化模式，服务器重启后策略状态丢失；
3. **无验证闭环**：转换产物无系统性验证，漏洞逐次修补、永不收敛（拆东墙补西墙）。

### 1.2 已查证平台事实（设计依据，全部可溯源）

| # | 事实 | 出处 |
|---|---|---|
| F1 | 框架自动持久化：pickle 保存股票池/账户/订单/g 变量；before_trading_start、handle_data、after_trading_end 事件后触发；重启先跑 initialize 再覆盖恢复 g 同名变量；`__` 前缀私有变量与不可序列化对象不保存 | 恒生投研平台 API 文档（kay-ou/ptradeAPI 镜像 Ptrade东莞.md「模拟盘和实盘注意事项-关于持久化」） |
| F2 | 策略级文件持久化：标准 open()/pickle 读写研究根目录 /home/fly/notebook/（get_research_path() 获取，回测/交易通用）；无 write_file/read_file 专用 API | 同上 + docs/advanced/faq.md |
| F3 | sqlite3 内置可跨策略共享持久化 | ptradeapi.com/hub/data/qa_data.html Q6 |
| F4 | run_daily 交易场景 time 可设 00:00~23:59（回测日线周期固定 15:00 执行）；run_daily+run_interval 累计线程上限 5，超限部分任务不触发 | Ptrade山西.md（Context7 /kay-ou/ptradeapi 查证） |
| F5 | run_interval 仅交易可用，股票最小 3 秒，interval_timer_ranges 可设时间窗 | 同上 |
| F6 | check_limit 返回 {1,-1,2,-2,0}；V202202.01.038 起支持回测/研究场景（回测/交易行为一致的护栏 API） | utilities.md + Ptrade东莞.md 版本变动表 |
| F7 | check_strategy() 官方策略兼容性预检：输入策略代码字符串，返回 api_change_list + package_change_list | Ptrade山西.md |
| F8 | 日线级交易策略盘后运行；15:00 后下单成隔夜单次日 9:15 报送；限价单不传 limit_price 默认最新价报单、行情失败则下单失败；账户数据 6 秒同步 | Ptrade东莞.md 策略运行周期 + FAQ |
| F9 | 模拟盘与实盘同引擎同代码，仅撮合不同（模拟撮合 vs 真实柜台）；两端分开部署、文件空间独立；策略跑在券商云端，客户端关闭不影响 | kay-ou FAQ + 知乎国金证券 |
| F10 | 交易环境封闭：不能 import 自有包、不能跨策略调用函数、不能连外网/外部数据库（tushare 内置除外） | kay-ou FAQ「平台与环境」 |

## 2. 公开仓边界声明（批复修订①）

**模块定位：转换工具——与既有「转 PTrade」tab（本地 QuantStudio 策略 → PTrade 回测代码）同族的下游段**：PTrade 回测代码 → PTrade 模拟盘/实盘部署代码。本模块只产出代码与验证证据，不执行任何实盘操作。

### 2.1 入公开仓（github.com/yangyizhu8/quantstudio）

- 转换内核（§5 全部代码与契约表——契约表内容为公开平台 API 事实，来源公开文档）；
- CLI 壳（qs-compile deploy 子命令）与 PyQt tab 壳；
- 测试与固件、runbook 模板（占位符化，不含真实券商信息）；
- 主仓收敛台账 `docs/ptrade-deploy-convergence-ledger.md`（仅 N_i/R_i 计数、轮次记录与 trading 仓 vault 指针，不含实盘细节）。

### 2.2 不入公开仓（随沉淀走 QuantStudio-trading 侧）

- 实盘部署操作细节：券商名称/柜台类型/服务器环境/账户配置/资金参数等；
- 含真实环境信息的验证证据（实盘客户端日志、真实账户对账截图等）；
- Obsidian 知识库 vault 实体（§8——含实盘环境细节的坑条目）。

**边界执行机制**：P6 交付报告与知识库条目写入器内置敏感词扫描（券商名/账号/资金数/服务器地址模式），命中即路由到 trading 侧路径并警告；公开仓 CI 不含 vault 内容。

## 3. 目标与非目标

**IN**：
- 输入：PTrade 回测策略 `.py` + `deploy.yaml`（§5.3）；
- 输出：**单一交易工件**（模拟盘与实盘部署同一份代码）+ 转换报告 + runbook（模拟盘先行验收 → 实盘迁移清单 → 状态重建流程）；
- 七段流水线 P0–P6（§5.2）；状态持久化注入（§6）；稳定性护栏注入（§6.3）；
- CLI 与 PyQt 双端对等（哈希门禁，§7）；
- 闭环知识库管线与收敛台账（§8）。

**OUT**：
- 不改既有「本地 QuantStudio 策略 → PTrade 回测代码」管线（source_import.py 等）的任何行为——本模块为其下游新增段，复用契约表/门禁/保真开关的**框架模式**但零改动既有路径；
- 不做 tick 级回测策略转换（回测引擎不支持 tick 回测，无合法输入；tick 级实现属手写增量，不进本管线）;
- 不消除撮合语义差/执行时点差（声明式管理，§14）；
- 不自动执行实盘/模拟盘部署（产出代码与操作清单，部署动作由用户在券商端执行）。

## 4. 总体架构

```
输入: 回测策略.py ──┐
deploy.yaml ────────┤
                    ▼
┌─────────────────── 单一内核（quantstudio/ptrade_deploy/）───────────────────┐
│ P0 预检 → P1 策略画像 → P2 配置校验 → P3 AST 变换 → P4 等价验证 → P5 平台门禁 → P6 交付 │
└──────────────────────────────────────────────────────────────────────────────┘
        ▲ 知识库回哺（P0 先查 vault，自动应用已知修复/已知坑规避）        │
        └────────────────── 验证发现 → 修复(框架层) → 知识库沉淀 ←────────┘
                    ▼
        单一交易工件 + 报告 + runbook ──→ 哈希门禁 ──→ CLI / PyQt 两端 SHA256 一致
```

**单内核 + 双薄壳**：CLI 壳（strategy_compiler/cli.py 追加 deploy 子命令）与 Qt 壳（main_gui.py 新 tab）只做参数收集与展示，均调用 `quantstudio/ptrade_deploy/runner.py:run_deploy()` 同一入口——对等判据可机检（§7.3）。

## 5. 核心组件设计

### 5.1 契约表（API 可用性矩阵）

文件：`quantstudio/ptrade_deploy/contract_table.yaml`。schema：

```yaml
api:
  set_commission:
    signature: "set_commission(...)"
    backtest: true        # 回测可用
    trading: false        # 交易不可用/无意义
    action: strip_or_gate # 变换动作：strip(剔除) | gate(条件化) | keep | replace
    replacement: null     # replace 时的替代表达式
    pbox_version: "all"   # 可用性依赖的最低 PBOX 版本
    source: "Ptrade东莞.md-设置函数"   # 文档出处（可溯源）
    note: "回测撮合参数，交易环境无意义"
  run_interval:
    backtest: false
    trading: true
    action: gate
    pbox_version: "all"
    source: "Ptrade山西.md"
  check_limit:
    backtest: true
    trading: true
    action: keep
    pbox_version: "PBOXQT1.0V202202.01.038"   # F6：此前版本回测不可用
    source: "Ptrade东莞.md-接口版本变动"
```

**种子条目集（M3 首版依据 F1–F10 录入）**：
- 回测专用（trading:false）：set_commission、set_slippage、set_fixed_slippage、set_volume_ratio、set_limit_mode、set_yesterday_position、get_trades_file、convert_position_from_csv；
- 交易专用（backtest:false）：run_interval、tick_data、get_snapshot、get_individual_entrust、get_individual_transaction、get_tick_direction、get_gear_price、get_deliver、get_fundjour、after_trading_order、after_trading_cancel_order、on_order_response、on_trade_response、send_email、send_qywx、permission_test；
- 双端可用：get_history、get_price、get_fundamentals、order 系列、get_position(s)、get_orders、get_all_orders、get_trades、cancel_order(_ex)、check_limit（版本列）、run_daily、get_research_path、create_dir、log、is_trade、get_stock_status、get_Ashares、get_etf_list。

契约表是**唯一 API 事实源**：转换器与校验器均查表驱动，禁止代码内散落硬编码 API 判断；表条目变更走框架层六步流水线。

### 5.2 七段流水线规则集

| 段 | 名称 | 职责与关键规则 | 输出 |
|---|---|---|---|
| P0 | 预检 | ① AST 可解析；② 知识库回哺：读 vault 已知坑清单（§8.4），命中即注入对应修复/规避；③ 危险 import 扫描（自有包/跨策略调用，F10）；④ 回测专用 API 与交易专用 API 计数 → 不可转换判定（如依赖 get_trades_file 核心逻辑）即拒 | 预检报告（PASS/REJECT+理由） |
| P1 | 策略画像 | 频率档位（日线/分钟）识别（set_backtest/frequency 参数与 handle_data 形态）；生命周期函数清单；股票池来源（静态/动态）；订单 API 家族；g.* 状态变量读写清单 + 可序列化性静态判定（IO/句柄/自定义类实例 → 标记需豁免） | profile.json |
| P2 | 配置校验 | deploy.yaml schema 校验（§5.3）+ 档位合法性（分钟策略不得选日线执行档）+ 与画像交叉校验 | 规范化配置 |
| P3 | AST 变换 | ① 环境差异 API 处理（查契约表 action：回测专用设置函数 → is_trade() 门控保留（回测行为不变）或按配置剔除；交易专用且回测不存在 → 仅注入不替换）；② 持久化层注入（§6.1）；③ 执行时点挂载（日线档：handle_data 逻辑包装为 run_daily 目标函数，注册时间取 deploy.yaml；分钟档：保持原生命周期）；④ 护栏注入（§6.3）；⑤ 类实例/IO 对象初始化改写为 `g.__` 前缀（F1 豁免自动持久化）；⑥ 定时任务计数注入检查（F4 ≤5） | 转换产物 .py（结构化生成，非字符串拼接） |
| P4 | 等价验证 | 策略逻辑函数体（信号计算/下单决策）与输入 AST **逐语句 diff**：除白名单改写行（§5.4）外必须零差异；任何未在白名单的行为变化 → FAIL | diff 报告（等价性判据） |
| P5 | 平台门禁 | ① 契约表全量校验：产物中每个 API 调用按目标环境（trading）查表，环境不可用且未被门控 → FAIL；② 定时任务累计 ≤5（F4）；③ g 状态变量 pickle 可序列化静态检查（§6.1 白名单外类型即 FAIL）；④ 产物 import 白名单（F10）；⑤ check_strategy() 官方预检（F7，可选：研究环境可达时执行，比对 api_change_list/package_change_list；不可达 → 降级声明并仅跑自建门禁①-④） | 门禁报告（逐项 PASS/FAIL） |
| P6 | 交付 | 产物落盘 output/deploy/<strategy_id>/；生成转换报告（改写清单+保真差异声明）与 runbook（占位符化）；敏感词扫描路由（§2.2） | 交易工件 + 报告 + runbook |

### 5.3 deploy.yaml schema

```yaml
strategy_id: "wsgm10_live_v1"        # 唯一 ID，决定状态文件名与台账登记名
source: "quantstudio/backtest/strategies/xxx.py"
execution:                            # 执行时点档位（唯一真决策点）
  profile: "daily_eod_1450"           # daily_eod_1450 | daily_open_0931 | minute_keep
  # daily_eod_1450：日线策略尾盘 14:50 run_daily 执行（信号 get_history include=False 取 D-1，E1 铁律）
  # daily_open_0931：次日开盘 09:31 执行（同 E1 口径）
  # minute_keep：分钟策略保持原 handle_data 生命周期
persistence:
  enabled: true
  state_file: "/home/fly/notebook/{strategy_id}_state.pkl"
  save_points: ["after_trading_end", "on_order_change"]   # 关键点落盘
guards:
  limit_price_explicit: true          # 显式限价（F8）
  limit_up_down_block: true           # check_limit 护栏（F6）
  suspension_block: true              # get_stock_status 停牌护栏
  order_idempotency: true             # 当日订单幂等（防 6 秒同步重复下单）
  fundamentals_retry: 3               # get_fundamentals 在线调用重试
notify:
  email: ""                           # send_email 异常通知（留空禁用）
report:
  fidelity_notes: []                  # 用户附加保真声明
```

### 5.4 P4 等价 diff 白名单（穷举式，新增须走框架层流程）

1. 回测专用设置函数行的门控/剔除（契约表 action 产生）；
2. 持久化注入块（`# PTRADE-DEPLOY:PERSIST` 标记包裹）；
3. 护栏注入块（`# PTRADE-DEPLOY:GUARD` 标记）；
4. 执行时点挂载产生的函数包装签名行（函数体原样内联）；
5. `g.__` 前缀改写行；
6. 头部 import 追加行。

白名单行必须带机器可识别标记；无标记且 diff 非零 → P4 FAIL（防模型/脚本夹带未声明改写）。

## 6. 状态持久化与稳定性注入模板

### 6.1 持久化层（官方模式加固版，依据 F1/F2）

- **双层**：`g.state` 纯数据容器（框架自动持久化兜底）+ 显式 pickle 落盘（`{strategy_id}_state.pkl`，initialize 中 try 恢复 / except 默认值；save_points 触发 `pickle.dump(g.state, f, -1)`）；
- **状态 schema 带版本**：`{'version': N, ...}`，版本不匹配回退默认并告警（防旧 pickle 炸新代码）；
- **重启对账**：首日 before_trading_start 注入对账段——`get_position`/`get_orders` 与 g.state 持仓记忆比对，不一致以柜台为准并 log 警告 + 状态修正（F8：账户数据 6 秒同步，柜台为真相源）；
- **可序列化白名单**：dict/list/str/int/float/bool/None/tuple/set/datetime；自定义类实例一律改 `g.__` 豁免（F1）。

### 6.2 执行时点映射（唯一语义决策，配置化）

| 回测档位 | deploy.yaml 档位 | 挂载方式 | 保真声明 |
|---|---|---|---|
| 日线 | daily_eod_1450 | handle_data 逻辑包装 → run_daily(time='14:50') | 信号仍取 D-1（E1）；成交价从回测收盘价变为尾盘实时价 |
| 日线 | daily_open_0931 | 同上 → run_daily(time='09:31') | 成交价从收盘价变为次日开盘价 |
| 分钟 | minute_keep | 保持原 handle_data | 频率语义一致；仍声明撮合差 |

回测中「日线 handle_data 盘后运行+收盘撮合」直接映射到交易盘后会变隔夜单（F8）——故日线策略强制二选一执行档位，禁止「不选」。

### 6.3 稳定性护栏

显式 `limit_price`（快照价±保护带）；`check_limit` 1/2（涨停）拒买、-1/-2（跌停）拒卖按需配置；`get_stock_status` 停牌/ST 拦截；订单幂等（state 内当日记单标记）；`get_fundamentals` 重试（次数可配）；异常 `send_email`/`send_qywx` 通知（可选）；run_daily+run_interval 累计 ≤5 静态检查（F4）。

## 7. 双端对等实现（批复修订③：CLI 裁定照准）

### 7.1 CLI（M4）

`qs-compile deploy --config deploy.yaml [--report-dir ...]`：在 `quantstudio/strategy_compiler/cli.py` 追加 deploy 子命令分支（**最小侵入**：仅新增 argparse 分支转发到内核 `run_deploy()`，不触碰既有子命令代码路径）。qs-compile 入口既有定义为 `quantstudio.strategy_compiler.cli:main`（pyproject.toml），无需改 packaging。

### 7.2 PyQt（M5）

main_gui.py 新增「转模拟盘/实盘」tab：输入源文件 + 档位选择（下拉）+ 持久化/护栏开关（复选）+ 运行按钮 + 报告展示区。tab 内部生成等价 deploy.yaml 后调用同一 `run_deploy()`。与既有「转 PTrade」tab 并列（同族工具，§2）。

### 7.3 对等哈希门禁（可机检判据）

同一输入策略 + 同一 deploy.yaml，经 CLI 与 PyQt 两端运行，产物 `.py` 的 **SHA256 必须逐位一致**（工件内禁止时间戳/路径等非确定性内容；报告文件允许含时间戳但不在门禁范围）。CI 增加 `tests/test_deploy_parity.py`：两端各跑一次固件策略，断言哈希相等。**禁止两套独立实现**——审计检查点：Qt 壳与 CLI 壳不得包含任何转换逻辑代码。

## 8. 闭环知识库管线与收敛台账（批复修订②）

### 8.1 仓库分工

| 内容 | 位置 | 理由 |
|---|---|---|
| Obsidian vault 实体（知识条目） | **QuantStudio-trading 仓** `vault/knowledge/ptrade-deploy/` | 含实盘环境细节，不入公开仓（§2.2） |
| 收敛台账（N_i/R_i + 指针） | **QuantStudio 主仓** `docs/ptrade-deploy-convergence-ledger.md` | 公开可审计的收敛过程，仅计数与指针 |
| 台账格式 | 参照 `knowledge/alignment-convergence.md`（S4 先例：主表逐轮追加 + 收敛指标 + 复发警报 + 平台期告警） | 复用既有格式惯例 |

### 8.2 知识条目 schema（Obsidian frontmatter，可机读回哺——禁止散文）

```markdown
---
id: PD-2026-0001
symptom: "现象一句话（可检索）"
root_cause: "根因一句话"
fix_target: "修复落点：文件路径 + commit sha"
fix_layer: contract_table | transformer | gate | template | kernel   # 强制框架层
evidence: "验证证据指针（trading 仓路径）"
fixture: "复现固件路径（tests/deploy_fixtures/）"
feedback:                              # 回哺方式（至少一项非空）
  contract_row: "契约表新增行描述或 null"
  gate_rule: "校验规则新增描述或 null"
  template_patch: "模板改动描述或 null"
status: open | closed
closed_round: 3                        # 闭环轮次
---
正文：复现步骤 / 影响面 / 讨论（允许散文，但 frontmatter 必须完整）
```

### 8.3 闭环流程（常态化管线）

转换运行（任何一次）→ 验证（P4/P5/横验证/模拟盘实跑）→ 发现漏洞 → 框架层修复（铁律：不碰策略源码与产物补丁）→ 知识条目入库（trading vault，frontmatter 齐全）→ **回哺**：fix_layer 对应物（契约表行/门禁规则/模板/内核）落地 → 主仓台账登记该轮 N_i/R_i + 条目 id 指针 → 全量回归门禁通过才允许闭环。

### 8.4 回哺机制（P0 先查库）

P0 预检读 vault（通过台账指针定位 trading 仓路径；trading 仓不可达时降级为警告并跳过回哺、台账记录 degraded）→ 按 symptom/id 匹配输入策略特征 → 命中即：应用 feedback（契约表行/门禁规则已在库里则天然生效）+ 已知坑规避（对 open 状态条目在报告中显式警告）。

### 8.5 收敛判据与处置（写死）

- 每轮登记：新发现漏洞数 **N_i**、复发漏洞数 **R_i**（同 root_cause 条目再次命中即复发）；
- **收敛 = 连续 3 轮 R_i = 0 且 N_i 单调递减趋零**；
- **R_i > 0 = 流程违规**：先归因（知识库缺条目 / 回哺链断裂 / 修复落点不合规）→ **先修管线再修漏洞**；
- 平台期告警：同策略连续 3 轮 N_i 不降 ⇒ 评估扩探针（参照 S4 先例）。

## 9. 首批横验证选型（批复修订③）

- 目标组合：**日线 ×2 + 分钟 ×1**；
- 候选池（已探测含分钟信号）：断板反包策略.py（分钟信号 2 处）、连板梯队龙头打板套利策略.py（3 处）、smallcap_overnight_scalp_7（1 处）；日线池：6 策略池任选（CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev / weekly_smallcap_growth / 周频小市值成长动量）；
- **确认流程**：M7 前对分钟候选实测频率档位（读策略回测配置与 handle_data 形态）；若判定全部不适用（如含 tick 依赖或打板逻辑超管线覆盖面），则**日线 ×3 替代并在台账登记理由**（照批复原文执行）；
- 横验证判据：候选策略逐一经 `qs-compile deploy` 全流水线（P0–P6）全 PASS + 产物过 P4/P5 门禁 + 模拟盘部署 runbook 走查通过。

## 10. 影响面清单

**新增（纯增益主体）**：
- `quantstudio/ptrade_deploy/`（新包）：contract_table.yaml、preflight.py（P0）、profiler.py（P1）、config.py（P2）、transformer.py（P3）、equiv.py（P4）、gates.py（P5）、deliver.py（P6）、runner.py（内核入口 run_deploy()）、templates/（持久化/护栏注入模板）；
- `tests/test_deploy_*.py`（含 parity 哈希门禁测试）、`tests/deploy_fixtures/`（复现固件）；
- `docs/ptrade-deploy-pipeline-design.md`（本文件）、`docs/ptrade-deploy-convergence-ledger.md`（台账，初版含表头与指针节）；
- QuantStudio-trading 仓 `vault/knowledge/ptrade-deploy/`（vault 骨架 + README + 首条占位说明——不随主仓推送，走 trading 仓自身流程）。

**最小侵入（两处，均有回归门禁覆盖）**：
- `quantstudio/strategy_compiler/cli.py`：追加 deploy 子命令分支（既有子命令路径零改动；冒烟测试覆盖既有子命令）；
- ~~`main_gui.py`：追加 tab 注册~~ **M5 实施勘注（2026-10-11 随 M8 回写）**：实际落点为 `quantstudio/gui/main_window.py`（+4 行 append-only：`_NAV_ITEMS` 第 10 项 + `elif row == 9` 分支返回 `DeployTab`）；`main_gui.py` 为 33 行薄启动器零改动——侵入面小于原方案，M5 验收时呈备采信（AST 断言测试固化）。

**零改动承诺**：source_import.py、backtest 引擎、既有校验器、既有测试与黄金结果——V1/V2 验收证明。

## 11. 纯增益声明（三型判定：新增能力型）

本模块为新增能力：新文件为主体，两处最小侵入点不改变既有行为路径；既有检测能力不因本模块改变报告口径；不引起任何既有功能回退——以 V1（既有测试全绿+黄金结果零变化）与 V2（6 策略 api_portability 重转全 PASS）为硬证据。若实施中出现必须改动既有行为的需求 → 立即停工剥离，按框架行为变更单独走六步流水线，不得捆绑。

## 12. 验收标准 V1–V7（命令级）

| # | 判据 | 验证方式 |
|---|---|---|
| V1 | 既有测试套件全绿 + 黄金结果零变化 | `pytest tests/ -q` 全 PASS；黄金结果对比脚本逐位一致 |
| V2 | 既有 6 策略 api_portability 重转全 PASS（零回退） | 既有转换管线回归命令，6/6 PASS |
| V3 | 双端哈希对等 | `tests/test_deploy_parity.py`：固件策略 CLI vs PyQt 产物 SHA256 相等 |
| V4 | P4 等价 diff | 固件+横验证策略逐个：白名单外 diff 行数 = 0 |
| V5 | 平台门禁全绿 | 契约表校验 + 定时任务≤5 + pickle 序列化 + import 白名单全 PASS；check_strategy 可达时官方预检 api_change_list 为空或已声明 |
| V6 | 横验证 | §9 组合（日线×2+分钟×1 或 日线×3+登记理由）全流水线 PASS |
| V7 | 闭环基线 | 台账首版落盘（表头+指针+轮次 0 登记）；vault 骨架+条目 schema 落地；回哺链路以一次真实转换演练跑通（P0 读库→应用→报告标注） |

## 13. 里程碑

| 阶段 | 内容 | 六步映射 |
|---|---|---|
| M1 | 本方案件呈审 | ①方案 |
| M2 | ZCode 审计（总调度呈审） | ②审计 |
| M3 | 内核实施（P0–P6 + 契约表种子 + 模板 + 测试固件；优先委派 zcode_code） | ③实施 |
| M4 | CLI 壳 + parity 测试 | ③实施 |
| M5 | PyQt tab | ③实施 |
| M6 | 闭环管线（vault 骨架 + 台账 + 回哺演练） | ③实施 |
| M7 | 横验证 + V1–V7 验收证据落 `docs/evidence/` | ④验收 |
| M8 | 用户确认 → 双仓库推送 → trading 副本同步门 → trading 仓 vault 落地 | ⑤确认 ⑥推送 |

## 14. 风险与边界（声明式管理，不消除）

1. **撮合语义差**：回测收盘/比例撮合 vs 真实柜台盘口——报告中显式声明，不消除；
2. **执行时点差**：§6.2 档位选择 + 保真差异声明；
3. **PBOX 版本差异**：契约表版本列；预检按券商实际版本裁剪（版本未知时按最低版本保守判定并在报告标注）；
4. **模拟盘↔实盘状态不随迁**：runbook 固化「实盘首启以柜台持仓对账重建状态」，禁止直接搬用模拟盘状态文件；
5. **check_strategy 可达性**：研究环境不可达时降级自建门禁并声明；
6. **trading 仓不可达（P0 回哺降级）**：警告+台账 degraded 记录，不阻塞转换；
7. **日线策略状态依赖**：依赖「当日盘中累积状态」的策略（如日内加仓计数）在 daily_open_0931 档重启后仅能恢复至上次落盘点——P1 画像识别此类依赖并在报告中显式警告。

## 15. 回退条件

本模块为新增文件主体 → 回退 = 删除 `quantstudio/ptrade_deploy/`、tests 新增文件、docs 两文件，并还原 cli.py / main_window.py 两处最小侵入（git 定向恢复；main_gui.py 未改动无需还原）。回退后既有管线完全不受影响（V1 判据在回退态天然成立）。触发条件：V1–V7 任一验收失败且无法在当轮修复闭环，或用户明示撤销。

## 16. 流程铁律（写死——本模块全生命周期执行）

1. **六步流水线**：方案（本件）→ 审计（M2）→ 实施（M3–M6）→ 验收（M7 证据入 docs/evidence/）→ 用户确认 → 双仓库推送；缺一步即违规。
2. **双仓库推送**：确认后 `git push origin`（quantstudio-plus / quantstudio 双 URL），推送后核对双远程 HEAD 逐位一致。
3. **主仓推送后同步门**：主仓 push 后同一工作周期内在 QuantStudio-trading 执行 `git fetch origin && git merge origin/main` + check-drift 九项 PASS + ci-smoke 共享层回归段 PASS + 台账登记。
4. **trading 侧沉淀同步铁律**：知识条目入 trading 仓 vault 后，主仓台账必须同步登记该轮 N_i/R_i 与条目指针（代码与报告与台账三同步，禁止只更其一）；trading 仓 vault 变更按 trading 仓自身流程推送，不进主仓。
5. **共享核心文件纪律**：cli.py 属共享核心文件——edit 前写前快照（stash create + store），精确文件清单 add，edit 后即时 git diff 自检；main_window.py 同（M5 实际落点，见 §10 勘注）。
6. **治理规则**：本模块周期内如需向任何 AGENTS.md 写入铁律级条目，先呈总调度备案（global 记忆 6576e07a）。

## 17. 文档同步义务（M8 随推送一并完成）

- `README.md`：新增「模拟盘/实盘部署转换」章节（定位、CLI 用法、PyQt 入口、台账指针）；
- `docs/strategy_toolbox.md` / `docs/prompt_engineering.md`：涉及转换工具族表述处同步；
- 本设计文件与台账保持与实现一致（实现与文档偏差即验收失败项）。

## 18. 审计要点清单（呈 ZCode / 总调度）

□ 修订①②③是否逐条落实（§2/§8/§7+§9）
□ 契约表种子条目与 F1–F10 查证事实是否一致、出处是否可溯
□ P4 白名单是否穷举且带机器标记（防夹带改写）
□ 对等哈希门禁是否封死「两套实现」路径（§7.3 审计检查点）
□ 闭环回哺链路是否有降级路径且降级可审计（§8.4/§14.6）
□ N_i/R_i 判据与 R_i>0 处置是否可执行（§8.5）
□ 两处最小侵入是否真最小（§10）+ 纯增益承诺验证方式是否充分（§11/§12 V1-V2）
□ 回退条件是否完整可执行（§15）
□ 流程铁律是否写死且与既有铁律无冲突（§16）
