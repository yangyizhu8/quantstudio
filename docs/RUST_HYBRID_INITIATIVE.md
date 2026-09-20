# RUST 混合纯增益改造 · 立项卷宗

> 本文档是跨会话交接的权威上下文载体。新会话首动作：读取本文档与 `docs/PROJECT_STATE.md` 恢复上下文。
> 创建：2026-09-20 · 由分析会话（008 报告作者）在副本构建轮落盘 · 基线 main@366596d

---

## 0. 一页速览

- **项目**：QuantStudio 回测引擎 Rust 混合改造（B+D 段下沉 Rust/PyO3，策略回调与数据层留 Python）
- **目标**：分钟档回测提速（门槛 ≥2×），四不影响（其他功能 / 双端对齐精度逐位 / GUI 全功能 / 数据管线）
- **验收口径**：逐位一致（G3.5 双跑门标准）
- **当前阶段**：六步①方案件起草（未开始）——本卷宗即为方案件起草的输入
- **数据隔离铁律**：`QUANTSTUDIO_DATA_ROOT` 环境变量指向影子数据根；生产库（48.8GB）绝不触碰
- **回并方向**：主仓 fetch 副本分支、主仓侧合并（浅克隆不影响此路径，已验证设计成立）
- **远程策略**：不上 GitHub（2026-09-20 总调度裁定：上云经主仓六步⑥双推，副本自建远程取消）
- **同步纪律**：方案定稿时与每段实施开始前，从主仓 fetch+merge 一次；行号偏差以同步后状态为准

## 1. 立项令原文（2026-09-20 用户）

> 【立项令：Rust 混合纯增益改造（六步①启动）】
>
> 总调度审计结论已随卷转达：008 报告技术判断采信（五层拆解/缝干净/三数值硬点）；round() 走 PyO3 回调正式定案（复刻风险归零）；「门禁复用」修正——引擎级双跑对比器是新工件，方案中必须设计与预算。
>
> 工作指示（按序）：
>
> C: 副本构建：git clone 主仓至 C 盘独立目录（实测余量 349G 充足；主仓 .git 20G——可用 --depth 1 浅克隆省空间，并回方向定为主仓 fetch 副本分支、在主仓侧合并，浅克隆不影响此路径）；数据隔离用 QUANTSTUDIO_DATA_ROOT 指向影子数据根（既有机制，参照批一测试与生态沙箱用法）——48.8GB 生产库绝不入副本；分支开发（建议 feat/rust-hybrid）。
> 方案件（六步①正式版），必含：①函数级迁移顺序（建议数据面热循环先行（bar 迭代骨架+bar_prices）→ 撮合内核 → 账户状态/待决队列，每段过逐位门才进下段）；②引擎级双跑对比器设计（Python 原版 vs Rust 内核对同一数据逐位比对 trades/nav/QS_FILL_AUDIT 事件流——新建，注明为何 L1-L4 不可复用）；③三处数值契约（round=PyO3 回调已定案；f64 逐序翻译禁 FMA/fast-math；int 截断同构复刻）；④Phase 0 实测热点表（只读 profile，替换报告中全部估算倍数；分钟档为主、日线档顺带入表作对照与 A-1 残差验证）；⑤分里程碑门禁与回退方案；⑥并回主仓流程（六步⑤用户确认+⑥双推+同步门）。
> 铁律合规：任何 .rs 编写前查 D:\miniQMT策略实盘\rust教程\ 校准语法（本地无 pyo3 章节已知，PyO3 语法以 Context7 /pyo3/pyo3 为准并标注依据）。
> 方案完成呈总调度审（六步②），审计通过才动产码。

**补充指示（交接轮批复，2026-09-20）**：C 盘目标目录 `C:\QuantStudioHybrid`；基线分支 = 主仓主线 `main`；三工件随首提交入库；影子数据根按仓内既有机制照抄。C 盘克隆项目独占一个 GitHub 公共仓库，仓库名 `QuantStudioHybrid`（remote 待用户提供完整 URL 后添加）。

**总调度裁定（2026-09-20，随交接验收下达，修正上述 GitHub 一项）**：

1. **混合副本不上 GitHub，远程一项取消**——回并已验证走主仓本地 fetch（`git ls-remote C:\QuantStudioHybrid` 通，见交接轮验收），最终代码经主仓六步⑥双推上云，副本自建远程完全没有必要；取消后 31 个策略源码与引擎设计的公开暴露风险就此消除（优于转 private 或延迟推送）。日后若需云端备份，另立私有仓再议。
2. **主仓 395 个未提交文件不强制清理**——多会话在途纪律不变，各件按自身六步落地；漂移风险以段门同步规则消化：方案定稿时与每段实施开始前，副本从主仓同步一次（`git fetch origin main && git merge origin/main`，origin 即主仓本地路径 `D:\miniQMT策略实盘\QuantStudio`），行号偏差以同步后状态为准。冲突时不擅自解决，停下申报由用户裁决。

## 2. 技术基线：008 报告核心结论（源码行号均为副本内路径）

### 2.1 关键发现：跨界之缝是干净的

- `attach_bar` 是 O(1)：仅 8 个字段引用交换 + 缓存失效，无 pandas 操作（`quantstudio/backtest/ptrade_api.py:586-604`）
- `DataDict` 已是惰性：`data[code]` 被策略访问时才构建 BarData（`ptrade_api.py:258-308`）
- ~~热浪费集中在引擎侧：iterrows 构价（`backtest_engine.py:2432-2435`）、bar 分组（`_load_minute_snapshots`）、strftime（`:2426`）~~
  **【勘误 2026-09-20（总调度批准登记）】**上条经客户实测修正：iterrows 构价占比 **4.46%**（35.5577s = 34.7761+0.7816，归档实录；此前 4.36% 作废）；**B 段主导假设作废**——Phase 0 实测大头为数据访问查询链（锚点场景 86.35%，详见 docs/RUST_HYBRID_DESIGN.md §4）；strftime 证伪（0.00s）；**迁移顺序以 Phase 0 实测为准**（方案件 §1 已按实测份额重排）

### 2.2 原理五层拆解

1. **数据平面下沉**：bar 快照 pandas 分组 → Arrow 列存（一次/日）；每 bar 价格 dict → Rust HashMap 从列一次构建；iterrows 对象洪流归零；pandas↔Arrow↔Rust 零拷贝
2. **控制平面保留**：attach_bar / DataDict / Context / 80 个 API 门面 / 策略回调全部原样留 Python
3. **GIL 边界协议**：每 bar 跨界仅 3 次调用（attach_bar → run_daily 调度 → handle_data）；PyO3 `Python::attach` 持 GIL 回调、`py.detach` 数据准备段释放 GIL（依据 Context7 /pyo3/pyo3：parallelism 指南）；异常语义复刻 `backtest_engine.py:2444-2448` 的 log-and-continue
4. **数值保真契约**：见 2.4 三处硬点
5. **门禁复用即证明**：既有测试矩阵 + 新建引擎级双跑对比器（见 2.5 修正）

### 2.3 函数级边界清单

**下沉 Rust：**

| 函数 / 状态 | 位置 | 桥接 |
|---|---|---|
| bar 迭代骨架（循环 + bar_prices 构建 + strftime 段） | `backtest_engine.py:2425-2448` | 循环主体改 Rust；每 bar 留 3 个 Python 回调点 |
| 撮合内核 _execute_buy/_execute_sell + _apply_slippage + _stamp_tax_rate | `:1094-1198`、`:1053`、`:826` | 订单 API 路由到 Rust 内核句柄（pyclass） |
| 账户状态 Account/Position（T+1、ETF T+0 解锁） | `:1135-1146` | 单一真相源移 Rust；Python Portfolio 视图按需物化（`_get_ptrade_positions:2476` 改为拉取） |
| 待决队列 _drain_pending_orders/_create_pending_order（next_open 模式） | — | 事件回放 Python 审计 |
| 日终净值 + _build_daily_pctchg_map/_build_match_prices（向量化段） | `:2458-2470` | 批量计算，结果回填 Python result 对象 |

**保留 Python：**

| 函数 / 状态 | 位置 | 理由 |
|---|---|---|
| attach_day（含 preload——DB IO 属 A 段） | `ptrade_api.py:539-584` | 每日一次，不变 |
| attach_bar（O(1)）/ DataDict/BarData/Context | `:586-604`、`:224-330` | 由 Rust 每 bar 回调，签名不变 |
| 80 个 API 门面 + scheduler.dispatch_if_match + 策略四回调 | — | 策略可见面零变化 |
| 数据层全部（duckdb_data_access/provider）、GUI、管线、编译器 | — | 完全不碰 |

**桥接（PyO3）**：Rust→Python（with_gil + call）/ Python→Rust（pyclass 内核句柄）/ trade_records·审计事件批量回填（依据 /pyo3/pyo3：trait-bounds 与 calling-existing-code 章节）。

### 2.4 三处数值契约（逐位一致口径）

| 硬点 | 源码位置 | 定案方案 |
|---|---|---|
| ① Python round() 十进制半偶舍入 | 涨跌停价 `ptrade_api.py:251-252`；公司行为 `backtest_engine.py:864,940,948` | **走 PyO3 回调 Python round（立项令定案，复刻风险归零）**；涨跌停价每日每标的仅算一次，性能无关紧要 |
| ② f64 乘加链运算顺序 | 费用链 `:1106-1125`；avg_cost `:1138`；pnl `:1183` | 逐序翻译；禁 FMA / fast-math / 自动向量化重排（Rust 默认不重排，合约写死） |
| ③ int() 向零截断 | round_to_lot `libs/shared_ashare_rules.py:50-52`；`int(buy_value/price)` `:1101` | Rust `as i64` 同为向零截断，同构复刻 |

### 2.5 门禁修正（立项令）：引擎级双跑对比器是新工件

既有 L1-L4 保真对比器是「本地引擎 vs PTrade 桌面」平台间对比，**不可复用**于「Python 原版引擎 vs Rust 内核引擎」的同平台逐位回归。方案件必须新建设计：同一数据集双跑 → trades / nav 序列 / QS_FILL_AUDIT 事件流逐位比对。既有 241 测试与契约门 CI 仍然复用（作为回归底线）。

### 2.6 四不影响判定（008 报告结论）

| 项 | 判定 | 依据 |
|---|---|---|
| 其他功能正常实现 | ✓ 无条件 | 策略 API 门面/调度器/回调契约/审计事件流全部保留 |
| 双端对齐精度（逐位） | △ 条件成立 | 三处数值契约复刻 + 引擎级双跑对比器验收 |
| GUI 全功能 | ✓ 无条件 | GUI 调用 Python 入口签名不变；进度回调由 Rust 经 PyO3 触发（`:2473-2474` 契约保留） |
| 数据入库管线 | ✓ 无条件 | 零接触——Rust 内核只消费已加载快照 |

### 2.7 迁移顺序（立项令建议，方案件①按此展开）

数据面热循环先行（bar 迭代骨架 + bar_prices）→ 撮合内核 → 账户状态/待决队列。**每段过逐位门才进下段；每段开始前先执行段门同步（fetch+merge 主仓 main，见铁律 9），行号以同步后为准。**

## 3. 基线与差异声明

- **副本基线**：main@366596d（`fix(qfq): DUCKDB_COLS[qfq_bootstrap_item] 补尾 3 列`），真浅克隆（--depth 1 --no-local --single-branch），单提交历史
- **主仓工作树差异**：主仓在 366596d 之上有 **395 个未提交文件**（agent_workspace 产物、docs/handoff、config/profiles、skills 工作区、**quantstudio/backtest 约 16 个**、data/snapshots 等）。**副本不含这些改动**——若新会话发现引擎代码与 008 报告行号有偏差，优先核对是否因主仓未提交改动所致，必要时请用户决策是否先在主仓提交再增量同步
- **生产库隔离**：主仓 git 未追踪 .db/.duckdb 大文件（预检已验证）；副本工作树 23.0 MB，无生产数据

## 4. 数据隔离机制（照抄既有用法）

`QUANTSTUDIO_DATA_ROOT` 环境变量（机制实证：`quantstudio/pipeline/daemon.py:3416,3429-3434`、`snapshot_lock.py:77`、`task_resume.py:51`）：

- daemon 运行时 manifest 记录该变量值
- 所有写路径（writer_db / batch_audit / quarantine / locks / logs）强制校验落在该根下（预写校验）
- **用法**：新会话运行任何触及数据的测试/回测前，设置 `QUANTSTUDIO_DATA_ROOT` 指向影子数据根目录；影子数据根的建立参照仓内批一测试与生态沙箱既有用法（Phase 0 profile 时落定具体路径并写入 PROJECT_STATE.md）

## 5. 铁律清单（违反任何一条即停）

1. **Rust 双源纪律**：任何 .rs 编写前查 `D:\miniQMT策略实盘\rust教程\` 校准语法；本地无 pyo3 章节（已知），PyO3 语法以 Context7 `/pyo3/pyo3` 为准并标注依据；输出标注文档依据，无标注视为未查
2. **数据隔离**：QUANTSTUDIO_DATA_ROOT 影子根；48.8GB 生产库绝不触碰、绝不入副本、绝不入 git
3. **每轮 Plan-Mode**：每轮工作先出八项计划等用户批；六步②④⑥是用户确认节点
4. **写前备份**：每轮实施前建立还原基线（分支/备份）；仅改本轮计划清单内文件
5. **证据化申报**：完成申报必须附命令与输出；倍数估算必须标注「估算」直到 Phase 0 profile 替换
6. **计划外问题零越权**：只登记 docs/ISSUES.md，不顺手修
7. **每段过逐位门才进下段**（迁移顺序纪律）
8. **总调度审**：方案件完成呈总调度审（六步②），审计通过才动产码
9. **段门同步**：方案定稿时与每段实施开始前，`git fetch origin main && git merge origin/main`（主仓在途改动入副本基线）；冲突时不擅自解决，停下申报由用户裁决（2026-09-20 总调度裁定）

## 6. Phase 0 实测热点计划（方案件④的执行预案）

- **性质**：只读 profile（cProfile/py-spy 采样分钟档主循环），产出实测热点表
- **目的**：替换 008 报告全部估算倍数（报告中所有 ×均标注「估算待实测」）；分钟档为主、日线档顺带入表作对照与 A 段残差验证
- **前置**：影子数据根就位（§4）；选择代表性策略与时间窗
- **产出**：热点表落 PROJECT_STATE.md，方案件④引用

## 7. 远程策略（2026-09-20 总调度裁定，取代原 GitHub 方案）

- **裁定：混合副本不上 GitHub，原「独占公共仓库 QuantStudioHybrid」方案取消**
- 理由：回并已验证走主仓本地 fetch（`git ls-remote C:\QuantStudioHybrid` 通，交接轮验收证据）；最终代码经主仓六步⑥双推上云——副本自建远程无必要；取消后 31 个策略源码与引擎设计的公开暴露风险消除
- 新会话待办中「添加 remote」一项作废；日后若需云端备份，另立私有仓再议

## 8. 六步流程状态板

| 步 | 内容 | 状态 |
|---|---|---|
| 前置 C | 副本构建 + 交接工件 | ✅ 完成（2026-09-20，本卷宗即其产物） |
| ① | 方案件起草（六节俱全） | ⬜ 待新会话启动 |
| ② | 总调度审 | ⬜ |
| ③ | 实施（分段过门） | ⬜ |
| ④ | 用户确认 | ⬜ |
| ⑤ | 并回主仓 | ⬜ |
| ⑥ | 双推 + 同步门 | ⬜ |

---

## 9. 变更日志

| 时间 | 变更 | 发起人 |
|---|---|---|
| 2026-09-20 | 创建：立项令 + 008 结论 + 基线声明 + 交接 | 分析会话（交接轮） |
| 2026-09-20 | 总调度裁定落档：GitHub 远程取消（§7 重写）；段门同步纪律入铁律 9（§2.7/§5 同步更新） | 总调度（用户） |
| 2026-09-20 | 勘误登记（批准）：§2.1 iterrows 4.46% 修正、B 段主导假设作废、迁移顺序以 Phase 0 实测为准；§6 热点表落点偏离登记（总调度裁定④：表入方案件子④ + 证据入 docs/evidence/phase0_hotspots.md + PROJECT_STATE 指针，替代本文件落位） | 实施主体会话（簿记） |
