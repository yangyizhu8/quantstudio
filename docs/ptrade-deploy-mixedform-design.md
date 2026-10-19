# PD-0007 方案：qs-deploy 支持「诊断型 stub handle_data + 单 run_daily 挂载」混合形态（六步①方案）

> 立项依据：PD-0006（run_daily 源形态）验收中发现的上游**既有**形态边界——混合形态被 P2 显式拒绝，
> 致 `CANSLIM突破成长选股策略` 与 `vol_regime_mom_rev` 无法部署化（**6 铁律策略中的 2 个**）。
> 状态：①方案（待②审计）——**未实施、未提交**。
> 关联：`docs/ptrade-deploy-rundaily-design.md`（PD-0006）、`docs/ptrade-deploy-convergence-ledger.md` §4.1。

## 1. 问题定义

**现象**：对 `output/ptrade_export/CANSLIM突破成长选股策略/*_ptrade.py` 与
`output/ptrade_export/vol_regime_mom_rev/*_ptrade.py` 运行 `qs-compile deploy` →
**P2_config FAIL：「混合形态暂不支持——handle_data 与 run_daily 挂载并存，挂载对象歧义」**（PD-0006 引入的显式拒绝）。

**为何是缺陷而非单纯边界**：这两个源的 `handle_data` **不是决策体，而是诊断型 stub**——真正的调仓决策在
`run_daily` 挂载的自定义函数里。P2 只按「符号存在性」判定混合形态，未区分
「handle_data 有实质决策」与「handle_data 仅诊断日志」。故当前行为把**语义上等价于纯 run_daily 源**
的产物一并拒绝了，属**能力缺口**（不是安全拒绝——安全拒绝应针对真有双驱动的源）。

**复现命令**：

```bash
py -3.11 -c "from quantstudio.ptrade_deploy import run_deploy; print(run_deploy('tests/deploy_fixtures/hv/hv_vol_regime.yaml').to_dict()['stages']['P2_config'])"
# → status=FAIL, reason 含「混合形态暂不支持」
```

## 2. 实测取证（2026-10-19，只读）

### 2.1 vol_regime_mom_rev（58KB，mtime 2026-08-27 01:15）

| 行 | 内容 | 判定 |
|---|---|---|
| L1053 | `def initialize(context):` | 注册点在 initialize 内（PD-0006 D1 识别范围） |
| L1058 | `    run_daily(context, monthly_rebalance, time='15:00')` | 单挂载 ✓ |
| L1089-1092 | `def handle_data(context, data):` docstring「Bar-level hook（daily-bar-v1 close 模式；决策在 run_daily 15:00）」+ `_ensure_runtime_state()` + `log.debug(...)` | **诊断型 stub**（无取数/无下单/无决策） |
| L1319 | `def monthly_rebalance(context):` | 真决策体（挂载目标） |

### 2.2 CANSLIM突破成长选股策略（69KB，mtime 2026-08-27 01:15）

| 行 | 内容 | 判定 |
|---|---|---|
| L1158 | `def initialize(context):` | ✓ |
| L1162 | `    run_daily(context, daily_logic, time='14:55')` | 单挂载 ✓ |
| L1166 / L1171 | `before_trading_start` / `after_trading_end` | 正常生命周期 |
| L1176 | `def handle_data(context, data):` | 存在（诊断型 stub，同族形态） |

### 2.3 全域扫描（`output/ptrade_export/**/*_ptrade.py`）

「`^def handle_data` 存在 且 `^\s+run_daily(` 存在」→ **仅 4 个文件命中，去重后 2 个策略**
（CANSLIM、vol_regime_mom_rev；vol_regime 另有 2 份 `_verify*` 副本）。**mtime 全部 08-27**。
其余策略为纯 `handle_data` 形态或纯 `run_daily` 形态（如 tech_etf_mvo_rotation、bbi_etf_rotation）。

> **对 PD-0006 记录的事实更正**：PD-0006 证据 §3 与台账 §4.1 将混合形态成因记为
> 「上游 convert-pipeline **在途重写**（2026-10-11 实测）」——**实测 mtime 08-27，与在途改写不符**；
> 正确表述：混合形态是**上游既有产出形态**（自 2026-08-27 起）。PD-0006 的 P2 **拒绝行为本身仍正确**
> （保守 fail-closed，防双驱动坏产物），需更正的是**成因表述**。

### 2.4 D1′ 判据全域预演（2026-10-19，只读分析，不写仓库）

对 `output/ptrade_export/**/*_ptrade.py`（45 个文件；3 个无 handle_data）套用 D1′ 判据：

| 判定 | 数量 | 说明 |
|---|---|---|
| **STUB**（放行候选） | **3** | `vol_regime_mom_rev`（主目录 + `_verify_*` 副本）+ `CANSLIM突破成长选股策略`——即**全部混合形态源**（去重后 2 个策略） |
| NOT_STUB（维持拒绝） | 39 | 全部因**黑名单命中**：`order_target_value` / `get_history` / `get_history_batch` / 经私有 helper 间接 `get_Ashares` |

**假阴性 1 例**：`_verify8_vol_regime_mom_rev` 变体被判 NOT_STUB（「语句数 4 > 3」）——实因该产物把
docstring 写在 `_qs_capture_ctx(context)` **之后**（非首句），而原剥离逻辑只处理首句。
→ **判据精化**：裸字符串表达式语句（docstring）**任意位置**均剥离；私有 helper（`_qs_capture_ctx` /
`_ensure_runtime_state` 类上下文捕获与状态初始化）经二级校验后入允许集。精化后该变体亦判 STUB（可执行语句 3 条）。

**结论**：判据在 42 个含 handle_data 的源上**零误放行**（39 个真决策体全部维持拒绝），且覆盖全部
4 份混合形态产物——D1′ 具备上产条件，风险 R1（误放行）由本预演实证压降。

### 2.5 平台文档依据

依据 Context7 `/kay-ou/ptradeapi`（Ptrade山西.md「run_daily-按日周期处理」，PD-0006 已录）：
`run_daily(context, func, time)` 仅可在 `initialize` 调用、func 仅收 context、日线回测恒 15:00 执行。
**平台文档不涉及「stub handle_data」**——该形态是**上游转换管线的产出约定**，非平台 API 语义；
故本项判据不以平台文档为依据，而以**实测形态 + 上游产出约定**为依据（如实说明）。

## 3. 设计决策

### D1′ 诊断型 stub 判据（**黑名单优先 + 允许集双判据**，fail-closed；2026-10-19 预演后精化）

`handle_data` 判为**诊断型 stub** 需**全部**满足：

1. **黑名单优先**（任一命中 → 立即判「非 stub」，拒绝；**含二级校验**——私有 helper 体内命中同判拒绝）：
   `order*` / `order_target*` / `get_history*` / `get_price*` / `get_*` / `filter_stock_by_status` /
   `set_*` / `run_daily` / `run_interval` / `passorder` / `batch*` / 任何 `order`、`cancel` 类调用；
2. **噪声剥离**：纯字符串表达式语句（docstring）**任意位置**均剥离（实证：上游存在把 docstring 写在
   helper 调用**之后**的产物，见 §2.4）；剥离后**可执行语句 ≤ 3** 条（次要护栏，实质判据为第 3 条）；
3. 每条可执行语句属允许集：
   - `Pass` / `return` / `return None`；
   - `Expr(Call)` 且 callee 解析名 ∈ `{log.debug, log.info, log.warning, log.error, print}`；
   - `Expr(Call)` 且 callee 为 `_` 前缀模块级 helper（**状态初始化 / 上下文捕获**类，如
     `_ensure_runtime_state` / `_qs_capture_ctx`）**且该 helper 自身体不含黑名单调用**（二级校验，防间接决策）；
4. **不含状态推进赋值**：任何 `Assign` / `AugAssign` / `AnnAssign`（含 `g.*`）一律判「非 stub」
   （保守起见本轮不设豁免）。

**不确定性一律判「非 stub」→ 维持拒绝**（fail-closed）。
**实证**：本判据在 42 个含 handle_data 的源上零误放行、覆盖全部 4 份混合形态产物（§2.4）。

### D2′ stub + 恰 1 挂载 → **走 PD-0006 挂载路径**

- 部署执行体 = 挂载目标函数（与 PD-0006 同机制：体内联为 `_deploy_daily_exec` + 源注册定点移除 + 按档位时点重挂）；
- **stub `handle_data` 原样保留**（诊断日志不丢失，语义零变化）；
- 源时点（15:00 / 14:55）记入 provenance（D2 沿用）。

### D3′ 多挂载：**拒绝不变**（PD-0006 D3 单挂载限制不动）。

### D4′ 判据失败（handle_data 有实质语句）→ **仍拒绝**，但错误信息**可诊断**：
列明命中黑名单的调用名与行号（替代当前仅报「挂载对象歧义」）。

### D5′ 等价性归一化（本项**最关键的实现细节**）

PD-0006 第 5 类白名单假设「源无 handle_data → 产品 wrapper 归一为 `handle_data`」。混合形态下
源**同时存在** stub `handle_data` 与挂载目标，产品亦有两个函数：

| 侧 | 函数 | 归一化映射 |
|---|---|---|
| 源 | `handle_data`（stub，原样） | ↔ 产品 `handle_data`（同名同体，逐语句等价；不参与白名单） |
| 源 | 挂载目标 `monthly_rebalance` / `daily_logic` | ↔ 产品 `_deploy_daily_exec`（归一到**源挂载目标名**，非 `handle_data`） |
| 源 | initialize 内 `run_daily(...)` 注册 | 双侧对称剔除（PD-0006 已有机制） |

**风险点**：若沿用 PD-0006 的「wrapper→handle_data」归一，将与保留的 stub `handle_data` **撞名**，
等价比较会产生伪 diff。故 equiv 需按「源是否已有 handle_data」**分流归一化目标名**（profile 已含
`run_daily_mounts`，可直接判定）。

## 4. 改动范围（预估，5 文件级）

| 文件 | 改动 |
|---|---|
| `profiler.py` | 新增 `_is_diagnostic_stub(fn_node, tree)` 判据（D1′）；profile 增键 `handle_data_stub: bool` + `handle_data_stub_reasons: list`（判据证据，供 P4/报告） |
| `config.py` | P2 混合形态分支细化为三段：stub+单挂载 → 放行；stub+多挂载 → 拒绝（D3′）；**非 stub** → 拒绝 + 黑名单行号（D4′） |
| `transformer.py` | 挂载路径触发条件由「无 handle_data」放宽为「无 handle_data **或** stub+单挂载」；stub 保留；provenance 不变 |
| `equiv.py` | D5′ 分流归一化：按源是否已有 handle_data 选 wrapper 归一名（挂载目标名 vs `handle_data`）；stub 同名同体按常规逐语句比较 |
| `tests/test_deploy_rundaily.py`（或新增 `test_deploy_mixedform.py`） | 判据正/负例 + CANSLIM/vol_regime 真实产物 7 阶段 PASS + **三路 parity 覆盖混合形态** |

runner 预计零改动（profile 透传）。deliver.py 报告可增列「源形态：handle_data stub + 单挂载」一行（可选）。

## 5. 影响面与纯增益审计（三型判定）

- **类型：新增能力型**（verdict 变化——原 FAIL 的两个策略可转 PASS，属新增能力开始报告真相，非既有行为漂移）；
- **既有功能零衰减要求**：deploy 套件 35 用例全绿 + V6 池既有口径不回退；
- **风险 R1（最高）**：stub 判据**误放行**（把真有决策的 handle_data 判为 stub）→ 产出**静默降级**的部署件
  （部署护栏包在错误的执行体上）。缓解：黑名单优先 + 二级 helper 校验 + 真实产物负例回归；
- **风险 R2**：equiv 归一化分流写错 → 伪 diff（表现为 FAIL，非静默错误，风险等级低）；
- **性能**：纯 AST 静态判定，无运行时开销（不影响回测性能）。

## 6. 验收标准

1. 新增用例 ≥ 8：stub 判据正例（vol_regime/CANSLIM 真实形态）+ 负例（含 `order_target_value` /
   `get_history` / 二级 helper 间接下单 → 判非 stub）+ 多挂载拒绝 + D4′ 错误信息含行号；
2. `hv_canslim` / `hv_vol_regime` 真实产物 **7 阶段全 PASS**（含 P4 等价零回退）；
3. **三路 parity**：同一 deploy.yaml 经 内核 / CLI / PyQt 三入口产物 SHA256 逐位一致
   （覆盖 run_daily 与混合形态两类源；PD-0006 实测已证 run_daily 侧 = `385da382…`，本项补进回归闸）；
4. 既有 35 用例全绿 + `check_fund_matrix.py --check` 哈希一致；
5. 台账轮 4 登记（N₄/R₄）+ vault 条目（PD-2026-0007）+ 三处引用文档同步（README/strategy_toolbox/
   prompt_engineering 的源形态段由「多挂载/混合形态 fail-closed」细化为「诊断型 stub 混合形态受支持；
   实质 handle_data + 挂载 → fail-closed」）。

## 7. 回退条件

- 任一新用例红、或既有用例回退、或 P4 出现非白名单 diff → **立即回退**（`git revert` 本批 commit）；
- 若实施后发现 stub 判据在生产源上误判（真实产物被误放行）→ 回退并改为**默认拒绝 + 显式白名单源清单**。

## 8. 知识反哺检索留痕

```
知识反哺：[命中] 平台转换铁律-文档签名不可信须建运行实测形态库（07-经验库，2026-10-19）
知识反哺：[命中] 时点防错-下游消费上游定时产物的时点差（11-方法论与工程体系，2026-10-19）
知识反哺：[命中] 小市值ETF动量大白马策略（01-策略，2026-10-19）
```

采纳要点：
- **首条**（07-经验库）：「探针先行 / 实测优先 / **测试闸固化**（关键形态差异写成 pytest 回归闸，
  防未来重转倒退）」→ 直接支撑本方案 §2 的实测取证路径与 §6 验收第 3 项（把 run_daily/混合形态
  的 parity 与 stub 形态写进回归闸，而非一次性实测）；
- **次条**（11-方法论）：「下游消费上游定时产物须留时点差、判据锚定产物而非当前时间」→ 支撑
  D2′ 的**挂载时点重挂**（源 15:00/14:55 → 档位时点）与 provenance 记录，避免部署件在错误时点驱动；
- **第三条**（01-策略）：run_daily + 时间判断的错峰调度实践 → 支撑「挂载目标是真决策体」的语义判断。

## 9. 六步流程与排期

①方案（本文件）→ **②审计（待：ZCode/总调度）** → ③实施 → ④验收 → ⑤用户确认 → ⑥随批统推。
建议与 PD-0006 的尾巴（trading vault 出仓）解耦：本项独立成批，不与 PD-0006 混合。
