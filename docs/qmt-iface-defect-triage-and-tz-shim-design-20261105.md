# QMT 真机六项缺陷分诊 ＋ N2（时区 KeyError）修复方案

> 六步流水线 **①方案**（候②审）｜起草：2026-11-05 20:4x（+08:00）
> 上游：R315 上报（V6 五维对照 DIFF·other=22）→ R316 三裁照准（B 类结构性对照／A 类 known_gap／D 类先修 N2）
> 关联：`docs/qmt-status-halt-criterion-fix-design-20261105.md`（R313 已实施 `c905282`）｜`output/qmt-fidelity-20261105/align_report.{json,md}`
> 判型：**N2＝纯恢复型**（恢复「财务因子正常计算」，回到损坏前语义）；N1/N3/N4/N5/N6＝**新增检测型或待定位**（分诊见 §四）

---

## 知识反哺检索留痕（铁律 21 / R1）

```
知识反哺：[无命中] knowledge/**（按 kb-info.yaml families 分族 + domain 过滤：qd 族全部 21 条 + 跨族全文检索
          关键字 Asia/Shanghai / tz_convert / tzdata / UnknownTimeZone / 时区），2026-11-05
```

**检索结论**：知识库内**无时区/tdzdata 相关历史条目**——本案为**新族首现**，须沉淀（R4 见 §七）。

---

## 一、问题定义（六项，全部有真机证据）

真机跑数：2026-11-05 17:07~17:16，产物 `8261c6866e2d9dc3`（114,043B·GBK），窗口 2026-06-01..09-30（86 交易日）。

| # | 现象（真机原文/读数） | 首次出现 | 影响面 |
|---|---|---|---|
| **N1** | `[系统]WARNING:下单量 0小于1,可用资金:100000,…,操作:open,代码:301205.SZ` | 06-01 首日 | 首日 5 笔委托仅成交 4 笔（明细缺 `301205`） |
| **N2** | `[canslim_breakthrough] get_fundamentals failed: 'Asia/Shanghai'` | 06-01 起**每次调仓必现** | **EPS 因子（C/A）全缺 ⇒ 综合分少两项 ⇒ 选股偏离** |
| N3 | 06-01 买入 5 笔后 `QS_PORTFOLIO_AUDIT … positions=0` | 06-01 | 持仓统计滞后（敞口/资金比失真） |
| N4 | `gross_exposure=0.0000` 恒 0（有持仓时亦 0） | 全程 | 敞口指标不可用 |
| N5 | `300214` 在 `ratio=-11.07%` 才卖出（策略 stop=7.5%） | 07-31 | 止损触发偏移；需与本地 STOPDBG 对照定性 |
| N6 | 每调仓日 ~12s；`QS_QMT_HISTDBG2/HISTDBG3` 生产输出 | 全程 | 86 日 ~4 分钟；日志噪声 |

---

## 二、N2 根因（**已证实**，三重吻合，非推测）

### 2.1 根因链

```
策略 L216-219  try:
                    fin_df      = get_fundamentals(passed, 'eps', fields=[...])
                    eps_factors = _compute_eps_factors(fin_df)     ← 同一 try 块内
策略 L220-221  except Exception as exc:
                    log.warning(f'… get_fundamentals failed: {exc}')   ← 文案误导（见 2.3）
策略 L391-392  _compute_eps_factors 内：
                    (pd.to_datetime(df['end_date'], unit='ms', utc=True)
                       .dt.tz_convert('Asia/Shanghai')             ← ★ 真实抛点
                       .dt.strftime('%Y%m%d'))
```

### 2.2 三重吻合证据

| 吻合点 | 证据 |
|---|---|
| **① 同块** | `_compute_eps_factors(fin_df)` 与 `get_fundamentals(...)` 处在**同一个 `try` 块**（策略 `L216-219` 逐字） |
| **② 异常类型** | `tz_convert('Asia/Shanghai')` 在缺时区数据环境抛 `pytz.UnknownTimeZoneError`，该类**继承 `KeyError`** ⇒ `str(exc)` == `"'Asia/Shanghai'"`，与日志**逐字一致** |
| **③ 后果** | `except` 分支 `eps_factors = pd.DataFrame(index=passed)` 为**空表** ⇒ `factors['composite']` 缺 C/A 两项 ⇒ 排序口径改变 |

### 2.3 附带发现：日志文案误导（须沉淀）

日志写 `get_fundamentals failed`，但**`get_fundamentals` 本身成功**、失败在**因子计算**。
⇒ 该文案会把后续排查者**直接引向取数层**（我第一轮也据此把 N2 归为「取数/时区面」）。属**归因陷阱**，须入条目标签字（§七 R4）。

### 2.4 诚实边界（未直证项）

**未直证**：QMT 内置 Python 到底缺「pytz 数据」还是「zoneinfo/tzdata」。本机有完整 pytz ⇒ **无法在本机复现**。
⇒ 方案必须先**探测**再选垫片（§三 P0），不得盲选。

---

## 三、N2 修复方案（框架层，策略源码零改动 · 铁律 07）

### 3.1 落点

`quantstudio/strategy_compiler/source_import_qmt.py` → `_QS_QMT_MODULE_HEADER` 模板串（wrapper 加载期执行一次，早于任何策略调用）。

**禁止**：改策略 `L392`、改 `get_fundamentals` 返回契约（epoch 毫秒语义不变）。

### 3.2 分级垫片（P0 探测 → 按结果选 P1/P2/P3）

| 级 | 动作 | 生效条件 | 风险 |
|---|---|---|---|
| **P0 探测**（必做） | 打印一次 `QS_QMT_TZ_PROBE pytz=Y/N zoneinfo=Y/N resolve=Y/N(<异常类名>) tzcache=<n>` | 恒执行 | 无（只读） |
| **P1** | `pytz._tzinfo_cache['Asia/Shanghai'] = pytz.FixedOffset(480)`（对 `PRC`/`Asia/Chongqing`/`Asia/Harbin`/`CTT` 同法） | `import pytz` 可用 | 低：pytz 是 pandas 首选路径 |
| **P2** | patch `pandas._libs.tslibs.timezones.maybe_get_tz`，对上述中国时区别名抛错时返回固定 `+08:00` | P1 无效且 pandas 走 zoneinfo | 中：须验证 patch 对 Cython 调用链生效 |
| **P3** | patch `zoneinfo.ZoneInfo` 构造器（同名返回固定偏移） | P2 仍无效 | 中：影响面更广，须限域名白名单 |

**等价性论证**：中国自 1991 年起**无夏令时**，`UTC+8` 固定偏移与 `Asia/Shanghai` 在本次回测窗口（2026 年）**完全等价** ⇒ 降级为固定偏移**不改变任何日期结果**（此为 P1-P3 共同前提）。

### 3.3 反向设计（不得静默降级）

垫片三个级别**全失败**时，必须打印 `QS_QMT_TZ_SHIM failed`（fail-loud），**不得**把 `tz_convert` 的异常吞掉或返回原对象——否则又退化成「零告警静默失效」（QD-2026-0021 同族病）。

---

## 四、六项分诊（N1/N3/N4/N5/N6）

| # | 初判归属 | 根因状态 | 本轮动作 | 后续 |
|---|---|---|---|---|
| **N2** | wrapper 时区垫片 | **已证实**（§二） | **本件主方案，候②审 → 实施** | 修后重跑真机 → 看 D 类 22 笔是否收敛 |
| N1 | wrapper 下单量/手数换算（`order_target_value` → 股数取整） | **未证实**（首日 `target_value=19800` 却算 0） | 不实施，仅登记 | 独立小批：需读 wrapper 下单量换算 + 手数下限逻辑，先复现 |
| N3 | 组合审计口径（`QS_PORTFOLIO_AUDIT positions`） | **未证实**（疑当日成交未入 `positions`） | 不实施，仅登记 | 与 N4 合批：查审计行取值时点（盘前/盘后） |
| N4 | 同上（`gross_exposure` 恒 0） | **未证实** | 不实施，仅登记 | 同上（N3+N4 合批） |
| N5 | 策略止损/撮合语义 | **未证实**（需本地 STOPDBG 同窗对照） | 不实施，仅登记 | 本地跑同窗取 STOPDBG 序列 → 逐日比对 → 定谳「撮合语义 vs 策略缺陷」；若撮合语义 ⇒ 转 V6-判据 v2 解释项 |
| N6 | wrapper 诊断行开关＋性能 | 现象确证 | 不实施，仅登记 | 把 `QS_QMT_HISTDBG2/HISTDBG3/HISTFOLD` 改为 **env 开关**（默认关），并入下一批小改 |

**分诊原则**：一张方案件只解一个已证实根因（N2），其余五项**不搭车**（铁律 08 要求「根因未证实不得动手」；铁律 01 要求方案范围收敛）。

---

## 五、改动范围（允许写入清单）

| 文件 | 改动 | 类型 |
|---|---|---|
| `quantstudio/strategy_compiler/source_import_qmt.py` | `_QS_QMT_MODULE_HEADER` 加 `_qs_tz_shim()` ＋ 调用；`_QMT_HASH_SCOPE` 不变（常量清单本体不动，值随模板变） | 框架层 |
| `tests/test_qmt_tz_shim.py` | **新建**：P0 探测行格式 / P1 注册生效 / 别名覆盖 / 全失败 fail-loud / 等价性（tz_convert 后日期不变） | 测试 |
| `docs/evidence/fundamentals-contract-matrix.yaml` + `.md` | `qmt_wrapper_hash` 同 commit 追认（铁律 10⑥） | 追认 |
| `docs/evidence/qmt-conv-correctness-baseline-20261103.md` | 六产物 SHA16 **v1.4** 换锚留痕 | 基线 |
| `knowledge/qmt/QD-2026-0022.md` | **新建**：时区数据缺失致因子静默失效 ＋ 日志文案归因陷阱 | 知识 |

**禁止写入**：策略源码、`ptrade_api.py`、其他模板串、`docs/ISSUES.md`（他线 M 态）。

---

## 六、影响面

| 面 | 评估 |
|---|---|
| 回测行为（本地） | **零影响**：垫片只在时区解析失败时介入；本机 pytz 完整 ⇒ P0 探测后**不注册**（走原路径） |
| QMT 产物 | 六产物 SHA16 **必然换锚**（模板串变）⇒ v1.4 基线同 commit |
| 契约 | `get_fundamentals` 返回语义（epoch 毫秒）**不变**；`_QMT_HASH_SCOPE` 常量名清单不变 |
| 性能 | 一次性探测（<1ms），无逐 bar 开销 |
| 多策略 | 六个策略凡用 `tz_convert('Asia/Shanghai')` 全部受益（通用修复，非 CANSLIM 专用） |

**反向影响（须防）**：若某策略本意用**非中国时区**（如美股 UTC），白名单外不降级 ⇒ 不受影响（白名单仅中文中国时区别名）。

---

## 七、验收标准（V1-V7）

| 门 | 判据 | 手段 |
|---|---|---|
| V1 | P0 探测行格式正确（含 pytz/zoneinfo/resolve 三态） | 新建单测（模板渲染后 exec，mock 三种环境） |
| V2 | P1 注册后 `pd.Timestamp('2000-01-01', tz='Asia/Shanghai')` 成功；别名 5 个全可用 | 单测 |
| V3 | 等价性：`utc→tz_convert` 后 `strftime('%Y%m%d')` 与原生路径**逐位一致** | 单测（对 2026 全窗日期抽样 ≥100 点） |
| V4 | 全失败路径 **fail-loud**（打印 `QS_QMT_TZ_SHIM failed`，不静默） | 单测（mock 三层全抛） |
| V5 | 六策略重转 ALL PASS（enc=gbk）＋ 六值 v1.4 入册 | `scripts/qmt_reconvert_six.py` |
| V6 | 矩阵 `--check --reverify` PASS（同 commit）＋ QMT 测试面全绿 | `check_fund_matrix.py` / pytest |
| V7 | **真机复跑**：`get_fundamentals failed: 'Asia/Shanghai'` **归零** ＋ 财务因子有值 | 用户侧冒烟 |

**V7 补充判据（D 类收敛）**：真机重跑后重做五维对照 ⇒ **D 类（qmt_only/local_only）应显著收敛**（预期由 22 笔降至 ≤5 笔；若未收敛，说明 D 类另有主因，须另立条目）。

---

## 八、回退条件

| 触发 | 回退动作 |
|---|---|
| V1-V4 任一红 | 撤销垫片（git 还原该 commit），重新设计 |
| V5 六产物任一 portability FAIL | 同 commit 回滚 |
| V7 真机报错新增（非原 N2） | 立即回滚并列新条目（不得带病上线） |
| P2/P3 级垫片导致未知副作用 | 降级到 P1-only（仅注册，不 patch） |

**回退手段**：`git revert <本次 commit>`（单 commit 可整体回退）；产物由 `qmt_reconvert_six.py` 重转即恢复（产物不入库，无需回滚）。

---

## 九、风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| R1：QMT 环境既无 pytz 又无 zoneinfo ⇒ 三层垫片全失败 | 中 | fail-loud ＋ 真机先跑 P0 探测（**下一步第一步**），据探测结果再决定是否启用 P2/P3 |
| R2：patch `maybe_get_tz` 对 Cython 调用链无效 | 中 | 单测覆盖（V2 必须真实走 `tz_convert`，不得只测 patch 存在性） |
| R3：白名单外的历史日期（1991 年前中国有 DST）被误降级 | 低 | 白名单仅对本次回测窗口生效？**否**——不设日期限制，但**记录**：1991 前数据若涉中国时区会有 1h 偏差（本项目回测窗口为 2026，不涉） |
| R4：修好 N2 后 D 类差异仍不收敛 | 中 | 已预设「D 类另有主因」分支（§七 V7 补充判据） |

---

## 十、实施顺序（候②审照准后）

1. **真机 P0 探测**（用户侧或我出探测用最小产物）→ 确定 pytz/zoneinfo 可用性
2. 按探测结果定 P1 / P1+P2 / P1+P2+P3
3. 写垫片 ＋ 新建单测（V1-V4）
4. 六策略重转 ＋ 矩阵追认 ＋ 基线 v1.4（V5/V6）
5. 真机复跑（V7）→ 五维对照 → 判 D 类是否收敛
6. **D 类收敛后才定稿 V6-判据 v2**（R316 裁定顺序）

---

## 附：本案须沉淀的知识点（供 R4）

1. **时区数据缺失致因子静默失效**：`tz_convert('Asia/Shanghai')` 在缺 tzdata 环境抛 `KeyError`，被宽 `try` 吞 ⇒ 因子整段空表 ⇒ 选股偏移且**无告警**（只有误导性 warning）。
2. **日志文案归因陷阱**：`except` 块里的日志标签写的是「上游调用名」，真实抛点可能在**同块的后续计算**——排查者按日志名定位会走错层（本案实测：我第一轮据此误判为「取数/时区面」）。
3. **宽 try 块的静默失效模式**：`try: A(); B()` 把 B 的失败记成 A 的失败，且 A 成功时**掩盖 B 失败**——「成功路径上的失败」最难察觉。
