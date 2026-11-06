# PD-0018 方案件 · 护栏幂等自撞（F1/F2）＋ 拆单现金累计与受理核验（F3）＋ status 类型归一（F6）

> 六步①（方案）｜日期：2026-11-06｜节点：ptrade 平台回测转模拟盘/实盘执行会话（节点⑦）
> 触发：2026-11-06 14:50 模拟盘实跑（三策略）平台侧日志与成交明细回传
> 状态：**候②审**（总调度/审核方 17:00 前）；**在裁编号：无**（本批修复本身不自决，②审可裁 F6 是否并入）
> 判型（铁律 18 分列）：**F1 纯恢复型 / F2 纯恢复型 / F3 新增检测型** / F6 纯恢复型
> 平台文档依据（铁律 16）：Context7 `/kay-ou/ptradeapi`（另本地镜像 `knowledge/platform/ptrade/`）
> 写前快照铁律 10：`source_import.py`／`guard_template.py` 属共享核心文件 ⇒ 实施轮须写前快照＋精确 add＋（若动 wrapper 模板串）矩阵哈希同 commit 追认

---

## 一、问题定义（现象 → 根因 → 量化影响）

### F1 · 幂等拦截路径 `UnboundLocalError`（P0，本批引入）

**现象（11-06 14:50 平台日志）**：全球资产轮动首次建仓的 **8 个加仓标的全部失败**——
`513500.SS`／`159941.SZ`／`513030.SS`／`159687.SZ`／`501018.SS`／`159980.SZ`／`159985.SZ`／`511260.SS`，
每个均报 `[资产配置][委托异常] … 错误=cannot access local variable '_id2' where it is not associated with a value`。
⇒ **加仓成功率 0%**（8/8 失败），首次建仓未成立。

**根因（产物 `strat_0c202b_trade.py` L804-814，框架注入的 `order_target_value` 包装）**：

```python
_ids = []
for (_code, _amt2) in orders:
    if _deploy_guard_pass(_code, 'auto'):     # 通过 ⇒ 才给 _id2 赋值
        _id2 = order(_code, _amt2)
    else:
        log.warning('PTRADE-DEPLOY 护栏拦截下单: %s (%s)' % (_code, 'auto'))
    _ids.append(_id2)     # ★ else 分支下 _id2 从未赋值 ⇒ UnboundLocalError
```

**归因（`git log -S` 实测）**：`_ids.append(_id2)` 首次出现于 **`4976f1d`（PD-0017 本批）** ⇒ **本批引入**。

### F2 · 幂等守卫键 `(标的,方向)` 与拆单语义冲突 ⇒ 第 2 笔拆单自我拦截（P0，本批引入触发条件）

**现象**：同一标的同方向，护栏参考打印 **2 次**后即「护栏拦截: 513500.SS auto 当日重复下单，幂等拦截」，
随后抛 F1 异常。计数自洽：`513500` 目标 499,729.63 ÷ 2.581 = 193,695 股 ⇒ 拆 4 笔 ⇒ 循环第 2 笔即被拦。

**根因（护栏块 L42-53）**：幂等台账键 = `(security, side)`，且**登记发生在调用时**（与是否成交无关）：

```python
_key = [str(security), str(side)]
_done = _book.get('orders_done', [])
if _key in _done:
    log.warning('护栏拦截: %s %s 当日重复下单，幂等拦截' % (security, side)); return False
_done.append(_key)          # ★ 调用即登记
```

⇒ 拆单循环内**逐笔**调用守卫 ⇒ 第 1 笔登记 ⇒ **第 2 笔被自己第 1 笔拦**。

**归因**：`git log -S` ⇒ `_key = [str(security), str(side)]` 与 `_done.append(_key)` 均首次出现于
`aee3e77`（qs-deploy 模块初版）⇒ **键粒度与「调用即登记」是既有设计缺陷**；
但**「守卫在拆单循环内逐笔调用」是本批 `4976f1d` 引入** ⇒ 本批引入的是**触发条件**，修复须同时处理两面。

**连带发现（本批既有盲区）**：`order()` 包装（L815-836）**完全不调守卫** ⇒ 走 `order()` 的路径（四象限）
**无幂等保护**（他策略同日同标的重复下单不被拦）⇒ 本批修复须**两面都修**（总调度令已明确）。

### F3 · 拆单现金预算不跨标的累计 ＋ 无委托受理核验 ⇒ **静默的部分失败**（P1；机制既有、检测面新增）

**量化闭合（逐位对上，非定性推测）**：

| 项 | 数值 | 来源 |
|---|---|---|
| 账户总资产 | 9,994,655.45（四象限）／9,994,592.65（全球资产轮动） | 平台日志 |
| 持仓 512890 1,215,000 股 ×1.235 | 1,500,525.25 | 四象限「当前数量=1215000」 |
| 持仓 518880 62,800 股 ×8.246 | 517,848.80 | 四象限「当前数量=62800」 |
| ⇒ 可用现金 | **7,976,281.40**（与平台「可用现金=7,976,281.65」一致 ✔） | 倒轧＋平台 |
| 买入需求 | 159934 1,998,448 ＋ 511010 2,984,299.60 ＋ 512890 3,496,879 = **8,479,626.60** | 目标计算行 |
| 缺口 | **503,345** ≈ 518880 当日卖出所得 517,848.80（**A 股 T+1 ⇒ 当日不可用**） | — |
| 512890 实际可买 | (7,976,281 − 2,984,300 − 1,998,448) ÷ 1.235 = **2,423,097 股** ⇒ 58 笔中 **49 笔成 / 9 笔被拒 [260200]** | 与日志 **9 条 ERROR** 逐位对上 ✔ |

**根因**：`source_import.py` 注入的 `_qs_split_order(security, value, px, cash_avail=None)`：
`budget = min(cash_avail, value)`、`total_cost` **只在单标的内累计**、`cash_avail` 取下单时刻静态可用现金
（`_qs_cash_avail()` 读 `context.portfolio.cash`）⇒ **跨标的累计不扣** ⇒ 超买 ⇒ 尾笔被平台拒。
且框架**无委托受理核验**，仍打印「提交状态=成功」⇒ **静默的部分失败**（策略以为调仓成功，实则只买到 49/58）。

**平台侧限制（铁律 16 如实标注，判定不可兑现之处必须写明）**：Context7 `/kay-ou/ptradeapi` 的
`get_all_orders` Notes 明示「**不能查询策略中待报、未报状态的委托**」⇒ 「已报」**不可由平台 API 直接证明**；
可核的只有 `get_order(order_id)`／`get_orders()` 返回的 `Order` 对象（字段 `order_id`/`filled`/`status`）。
⇒ 受理核验只能做**三态判定**（终态／已报未终态／平台未出现），「平台未出现」**保守判「未受理或平台未暴露待报态」并告警**，禁止兜底成成功。

### F6 · `Order.status` 类型随平台版本变 ⇒ 终态判定恒不成立（本轮查文档新发现，P1）

Context7 `/kay-ou/ptradeapi` README「Order Status Field Type Change (V005 vs V016/V041)」：
V005 用 **int**（`if order.status == 8`），V016/V041 用 **str**（`if order.status == '8'`）。
我方守卫的终态集合写死为**字符串** `('5','6','8','9')` ⇒ **int 型平台上恒不匹配** ⇒ 已终态委托被判「未终态」。

**后果**：D 项（陈旧挂单巡检）会**误报**「上一交易日遗留未终态委托」；若日后开启 `cancel_stale_orders`
⇒ **存在误撤已成交/已撤委托的风险**（当前撤单默认关闭 ⇒ 尚未发生）。
**处置建议**：随本批同批修复（仅加 `str(status)` 归一，语义不变）；②审可裁是否并入。

---

## 二、改动范围（**全部落转换层，策略源码零改动 · 铁律 07**）

### 落点 1 · `quantstudio/strategy_compiler/source_import.py` — 幂等判定分层（修 F1/F2）

**核心设计：幂等判定从「拆单循环内逐笔」移到「逻辑下单入口一次」；逐笔去重改用委托级流水。**

| 台账 | 键 | 用途 | 写入点 |
|---|---|---|---|
| `g.state['orders_done']` | `(security, side)` **逻辑单级** | 「当日重复**逻辑**下单」拦截（**不放宽**既有语义） | 逻辑入口 `_qs_idempotent_admit()` 一次性登记 |
| `g.state['qs_split_orders']` | `(security, side, order_id)` **委托级流水** | 拆单**逐笔**登记 ⇒ 审计全量覆盖（PD-0017 C 目标保留） | `_qs_register_split_order()` 逐笔写入 |

**目标结构（`order_target_value` 包装）**：

```python
# ① 逻辑层幂等判定：一次（跨拆单只判一次）
if not _qs_idempotent_admit(security, 'auto'):
    log.warning('QS_NOOP_ORDER api=order_target_value code=%s reason=duplicate_idempotent' % security)
    return _qs_noop_target(security, _delta, 'duplicate_idempotent')

# ② 拆单：现金预算取「跨标的累计后的可用预算」
orders, _tot = _qs_split_order(security, _delta, _px_exec, cash_avail=_qs_cash_budget())
if not orders:
    ...  # 维持既有 noop 分支（delta_below_one_lot 等）

# ③ 逐笔下单：不再过幂等（逻辑层已判）；逐笔登记委托级流水
_ids = []
_QS_SPLIT_DEPTH += 1
try:
    for (_code, _amt2) in orders:
        _id = order(_code, _amt2)            # ★ 不再引用未定义变量（F1）
        if _id is not None:
            _ids.append(_id)
            _qs_register_split_order(_code, _id)   # 委托级流水（C 保留）
finally:
    _QS_SPLIT_DEPTH -= 1
return _ids[-1] if _ids else None
```

**`order()` 路径补幂等（令：「两面都修」）**：

```python
def order(security, amount, *args, **kwargs):
    if _QS_SPLIT_DEPTH > 0:
        ...   # 内部拆单调用 ⇒ 逻辑层已判 ⇒ 跳过幂等（否则自撞）
    else:
        if not _qs_idempotent_admit(security, 'auto'):
            log.warning('QS_NOOP_ORDER api=order code=%s reason=duplicate_idempotent' % security)
            return None
        ...   # 既有拆单与登记逻辑不动
```

⇒ 三条性质同时成立：**①拆单 k 笔不互撞**；**②策略直接调 `order()` 的同日重复仍被拦**（补上既有盲区）；
**③逻辑层重复下单仍被拦**（既有语义不放宽）。

### 落点 2 · `quantstudio/ptrade_deploy/templates/guard_template.py` — 守卫四段不削弱、幂等段只读化

- 保留 PD-0016 定谳的**护栏四段**（`get_stock_status`／`check_limit`／`get_snapshot`／幂等）与**签名两参**（`(security, side)`），负向对照须逐项可查；
- **幂等段改为只读判定、不再登记**：登记统一由 `_qs_idempotent_admit()` 在逻辑层一次完成 ⇒ 消除「调用即登记」的自撞源；
- **F6 归一**：终态判定统一 `str(status)`（兼容 int/str 两版本）。

### 落点 3 · `source_import.py` — 拆单现金预算跨标的累计（F3 机制侧）

- 新增 `g.state['qs_cash_reserved']`（本策略本次运行已承诺金额）；
- `_qs_cash_budget()` = `_qs_cash_avail()`（平台实时可用，**已不含 T+1 不可用的卖出所得** ⇒ 不主动加回）− `qs_cash_reserved`；
- 每笔子委托提交后按「委托金额 × 价格」累加 `qs_cash_reserved` ⇒ **跨标的累计**，杜绝超买；
- 精度限制如实登记：平台「已报未成交」的冻结额是否即时反映在 `portfolio.cash` **依平台实现** ⇒
  保守设计 ⇒ 叠加 F3 检测面兜底（见落点 4）。

### 落点 4 · `source_import.py` — 委托受理核验 ＋ 部分失败上报（F3 **新增检测型**）

- 逻辑下单收尾处：收集本次全部子委托 id ⇒ 逐笔 `get_order(order_id)` 读 `filled`／`status`；
- **三态判定**（Context7 契约）：**终态**（`str(status) ∈ {'5','6','8','9'}`）／**已报未终态**／**平台未出现**；
- **平台未出现 ⇒ 判「未受理或平台未暴露待报态」并告警＋计入失败**；
  **任何查不到的分支必须告警，禁止兜底成成功**（通用库 Q051「静默兜底谎报」同族，见 §五）；
- 输出形态：把现有「提交状态=成功」改为**按笔数分列**（已受理 n／拒单 m／待核验 k）⇒
  **部分失败必须可见**（今日 512890 的 9 笔被拒不得再显示为成功）。

### 不改动面（负向对照须逐项证明）

- 护栏四段语义（停牌／涨跌停／参考价）**不削弱**（PD-0016 约束）；
- `initialize.__doc__`／模块 docstring 保位（PD-0016 约束）；
- PERSIST 块／E′ 清空／D 陈旧挂单巡检的**既有语义不动**（仅 F6 的 status 归一）；
- **策略源码零改动**（铁律 07）；产物 `Demo_account_Real_account/**` 由重转生成，不手改。

---

## 三、判型分列（铁律 18 · 三型判定必须分别声明）

| 项 | 判型 | 理由 | 验收独立性 |
|---|---|---|---|
| **F1** | **纯恢复型** | 去掉未定义变量引用 ⇒ 拦截/失败路径回到「不抛异常、可判读」；不改变任何成功路径语义 | 与 F2 分项验收 |
| **F2** | **纯恢复型** | 幂等判定回到「一次逻辑下单一次」⇒ 拆单 k 笔全提交；同时 `order()` 路径**补齐**幂等（既有语义不放宽） | 与 F1 分项验收 |
| **F3** | **新增检测型** | 新增「委托受理核验 ＋ 部分失败上报」⇒ 新增能力开始报告真相（今日的静默部分失败将被报出） | **单列验收，不得与 F1/F2 混办** |
| **F6** | 纯恢复型 | 仅加 `str(status)` 归一，语义不变 | ②审可裁是否并入同批 |

---

## 四、验收标准（铁律 01 第④步 · 五条负向对照 ＋ P4 等价零回退 ＋ 回归零衰减）

**新增固件**：`tests/test_deploy_f1f2f3_guard.py`（正反各向）

| 编号 | 负向对照（须**不**出现该错误行为） | 对照的正向面 |
|---|---|---|
| **N1** | 幂等拦截/失败分支**不得抛** `UnboundLocalError`；须返回可判读结果（noop/跳过）且日志含 `reason` | 11-06 失败态复现：8 标的加仓不得再全灭 |
| **N2** | 拆单 k>1 时**第 2..k 笔不得被自我拦截** | 实际提交笔数 == 拆单笔数（对照今日 513500 仅第 1 笔提交的失败态） |
| **N3** | 策略**直接**调 `order()` 同标的同方向第二次 ⇒ **仍被幂等拦**（`order()` 路径补幂等的正证） | 既有盲区闭合 |
| **N4** | 逻辑层重复下单（跨调用、同标的同方向）⇒ **仍被拦** | 既有语义不放宽（不因改键而失效） |
| **N5** | 模拟平台拒单（`[260200]` 可用资金不足）⇒ **必须报部分失败**，不得出现「提交状态=成功」 | 今日 9 笔被拒的失败态复现 |
| **N6**（随 F6） | `status` 为 int `8` 与 str `'8'` 两种形态**均须判为终态** | 版本兼容（V005 vs V016/V041） |

**其余验收项**：

- **P4 等价零回退**：三策略重转 `P0–P6 全 PASS` 且 `P4_equiv 全 PASS`（等价 diff 零回退）；
- **回归零衰减**：deploy 套件 **≥ 157 passed / 1 skipped**（当前基线），全量回归门禁；
- **产物级**：三产物 7 特征在位；**新增可观测行**（`QS_NOOP_ORDER reason=duplicate_idempotent`／部分失败分列）；
- **文档**：证据件落 `docs/evidence/ptrade-deploy-pd0018-evidence.md`；知识条目新开 `PD-2026-0018`（PD 族 · domain 待②审确认 · status closed 前须过 `kb_hook --strict`）；
- **R4 沉淀**：本轮沉淀 `PD-2026-0018`。

---

## 五、知识反哺检索留痕（铁律 21/22 · R1）

- `知识反哺：[命中] PD-2026-0016（ptrade-deploy/D2/knowledge/ptrade-deploy/PD-2026-0016.md，2026-11-06）`
  —— 采纳要点：①护栏**四段**（`get_stock_status`/`check_limit`/`get_snapshot`/`order`）与**签名两参**（`[security, side]`）已定谳 ⇒ 本批改幂等判定位置时**四段与签名不得削弱**（落负向对照）；②其「运行行为无影响、但一旦护栏需要 context（查持仓/查交易日/查计数器）就会拿 `security` 字符串当 context」的**脆弱点警示** ⇒ **F3 的受理核验与现金预算读取一律不得放进护栏块**（放 `order`/`order_target_value` 包装层）。
- `知识反哺：[命中] 代码质量经验 Q042「空 except 吞掉异常」/ Q051「状态提取的静默兜底谎报」（通用库 knowleage_base/07-经验库/代码质量经验.md，2026-11-06，只读不回写）`
  —— 采纳要点：①任何异常分支**至少 `except Exception as e` 并 logging**，禁空吞；②**永不命中的提取路径不得兜底成 PASS** ⇒ F3 的「平台未出现」分支**必须告警并计入失败**，**禁止**兜底为「成功」（今日「提交状态=成功」而平台拒 9 笔，正是该族）。
- `知识反哺：[命中] PD-2026-0017（ptrade-deploy/D3/knowledge/ptrade-deploy/PD-2026-0017.md，2026-11-06）`
  —— 采纳要点：①「验证点须先验该日是否存在下单尝试、且须具判别力」⇒ 本批验收用**回放/桩注入**构造 11-06 失败态，不依赖再次实跑；②E′ 清空语义**不动**（本批不改 PERSIST）。
- `知识反哺：[命中] 本地 PTrade API 文档镜像（knowledge/platform/ptrade/，2026-11-06）`
  —— 采纳要点：作为铁律 16 的**本地依据源**与 Context7 交叉核对（`get_orders`/`get_all_orders`/`Order` 字段与 status 类型差异）。
- `知识反哺：[无命中] knowledge/contracts、knowledge/qmt、knowledge/platform/qmt、knowledge/probes、knowledge/playbooks（2026-11-06）`
  —— 该四族检索面无「护栏幂等/拆单现金/委托受理核验」相关条目。

## 五之二、平台文档依据（铁律 16 · 产出须标注）

| 契约 | 依据（Context7 `/kay-ou/ptradeapi`） | 本方案用法 |
|---|---|---|
| `get_orders(security=None)` → `list[Order]`（`order_id`/`security`/`amount`/`filled`/`price`/`status`） | 山西/国盛文档 + `docs/api-reference-en/objects.md`（Order 对象） | 受理核验读 `filled`/`status` |
| `get_all_orders(security=None)` → `list[dict]`（`symbol`/`entrust_no`/`amount`/`entrust_bs`(1买2卖)/`price`/`status`/`filled_amount`）；**仅交易模块**；Notes「**不能查询策略中待报、未报状态的委托**」 | 国盛/东莞文档 | 账户级占用核对（可选）；**不可**用于证明「已报」 |
| `Order.status` **类型随版本变**：V005=int（`status == 8`）｜V016/V041=str（`status == '8'`） | README「Order Status Field Type Change (V005 vs V016/V041)」 | **F6 归一依据**；终态判定一律 `str(status)` |

---

## 六、影响面

| 面 | 影响 |
|---|---|
| 转换层（`source_import.py` 三包装 ＋ `guard_template.py`） | 幂等判定位置、键双台账、拆单现金累计、受理核验、status 归一 —— **本批全部改动落此** |
| 产物（`Demo_account_Real_account/**` 三策略） | 由重转生成；**行为差异**：全球资产轮动的 8 标的加仓将**真正提交**（今日全灭）；四象限 `order()` 路径将**首次受幂等保护** |
| 策略源码 | **零改动**（铁律 07） |
| 既有验收口径 | PERSIST/E′/D 语义不动；护栏四段与两参签名不动（PD-0016 约束） |
| ⚠ **前置依赖提示** | **F1/F2 修复会使被今日缺陷压制的下单「真下单」**——全球资产轮动 8 标的将真实占用账户现金，而 F5（三策略共用账户 99005258、各按账户总资产算目标、目标合计≈296%）**尚未裁定** ⇒ **建议 F5 候裁（16:00 呈批面，倾向乙＝开 PTrade「策略仓位隔离」）先行或与本批同窗落地**，否则修复效果会把「静默失败」变成「真实超买/互抢」 |

---

## 七、回退条件（铁律 03/10）

| 触发 | 处置 |
|---|---|
| 任一负向对照（N1–N6）失败 | **回退本批**（`git revert` 本批笔；写前快照 + 精确 add + 精确回退） |
| P4 等价 diff 非零 / 出现回退 | 回退；按铁律 10 追认矩阵哈希（若动了 wrapper 模板串，`check_fund_matrix --check --reverify` 同 commit） |
| deploy 套件红（含既有基线红） | 回退；基线红须区分「既有红」与「本批引入」 |
| 产物级 7 特征或新增可观测行缺失 | 回退 |

---

## 八、候②审要点（请审核方裁定）

1. **F6 是否并入同批**（我方倾向：并入，仅加 `str(status)` 归一，风险低、且消除「误撤已成交委托」的潜在风险）。
2. **F3 新增检测型的验收边界**：三态判定的「平台未出现」分支按**保守告警**处理（可能产生噪声告警），是否接受？
3. **跨标的现金累计的实现取舍**：保守方案（本策略已承诺额扣减 + 受理核验兜底）vs 精确方案（读 `get_all_orders` 账户级占用，但平台不暴露待报态 ⇒ 精确不可得）⇒ 我方倾向**保守方案**。
4. **F5 前置关系**：是否要求 F5（账户隔离）先落地再实施本批（我方倾向：**先落 F5**，理由见 §六 依赖提示）。
