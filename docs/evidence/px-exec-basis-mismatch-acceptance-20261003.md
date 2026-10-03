# 验收证据：接线层换算价按撮合模式分流（px_exec basis fix）

> 六步流水线**第 4 步（验收）** ｜ 日期：2026-10-03 ｜ 实施方：策略线会话（DSH）
> 方案件：`docs/px-exec-basis-mismatch-design.md`（②审计 PASS + 两条件补入）
> 落点：`quantstudio/backtest/ptrade_api.py`（接线层换算价，P-D12 通用件）
> 回退点：`git stash` → `3b7e5a9f0d6be0bf3ce321a442a49746f4224b4c`（写前快照，操作前建立）
> 实施前 HEAD：`cc9076c`｜实施前 `ptrade_api.py` 工作区**干净**（无他人未提交改动，已核）

## 1. 改动内容

**新增 1 个模块级 helper + 替换 2 处调用点**：

```python
def _qs_exec_basis_px(security):
    # 优先取引擎本日撮合价（_api._prices ≡ match_prices：open→开盘 / close→收盘）
    # 取不到（_prices 为空 dict / 该标的无价）→ 回退 _QSPriceState.orig（原语义）
```

| 位置 | 改动 |
| --- | --- |
| `ptrade_api.py:2801` 前 | 新增 `_qs_exec_basis_px()` |
| `_qs_wire_order_target_value` 内（原 `:2819`） | `px_exec = _QSPriceState.orig(security)` → `_qs_exec_basis_px(security)` |
| `_qs_wire_order_value` 内（原 `:2872`） | 同上 |
| 接线层注释（原 D4-S6 区块） | 更新为说明「D4-S6 论证只在 close 模式成立」 |

**diff 规模**：`1 file changed, 33 insertions(+), 2 deletions(-)`｜**AST 解析 OK**｜**仅动 `ptrade_api.py` 一个文件**

### 1.1 对审计代码草稿的一处更正（已在③由本线指出）

审计草稿 `px_exec = self._prices.get(...)` 中的 **`self` 在接线层不可用**（该函数为模块级）；
`_bare_to_qmt` 亦为 `PtradeAPI` 的 `@staticmethod`（`:1605-1607`）而非模块级。
实施改用 `_api._prices` / `_api._bare_to_qmt(bare_code(security))`（`_api` 为模块级单例，`:2695`）。

## 2. 验收结果（V1-V6 全 PASS）

| # | 判据 | 结果 | 证据 |
| --- | --- | --- | --- |
| **V1** | 反向复现：修复前 `px_exec == 当日收盘` 且成交价 `== 当日开盘` | **PASS** | 全窗 139/139 落在收盘区间；直采 `px_exec=15.01`≡600886 当日 close，成交价 14.77≡open |
| **V2** | 修复后直采：`px_exec == 当日开盘` | **PASS** | 1 个月窗 13 笔 **13/13** 命中开盘价、**0** 命中收盘价（`probe_px_basis_v2_records.json`） |
| **V3** | 金额离散度收敛 | **PASS（超预期）** | 全窗 open 模式：**低于整手上限 31→0 笔**；**超额(>10万) 34→0 笔**；最大成交额由 **102,529 → 100,000**（恰为目标值） |
| **V4** | close 模式零回归 | **PASS** | close 模式修复后 vs 修复前（运行时还原 helper 模拟）三件套 **SHA-256 逐位一致**：`e162f5606be65996` / `6b301044ec13292e` / `861572cf1ca03997` |
| **V5** | 回归套件 | **PASS** | `run_contract_gate.py --strategies` → **CONTRACT GATE : PASS**（契约矩阵门禁 + pytest 契约套件 + 6 策略 api_portability 冒烟，白名单无触发） |
| **V6** | 差异归因：open 模式 diff 只归因换算价来源 | **PASS** | 同一份代码下：**close 模式逐位不变**（V4）＋**open 模式改变**（V3）→ 差异面被完全限制在「换算价来源」单一变量；`config.csv` 两种模式均未变 |

### 2.1 关键数值对照（open 模式，全窗口 287 日）

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| 买入笔数 | 139 | 140 |
| 低于整手上限 | **31（22.3%）** | **0** |
| 超额（> 10 万） | **34** | **0** |
| 最大单笔成交额 | **102,529** | **100,000** |
| `daily_stats.csv` SHA | `e11a864e89256363` | `d9c4d1485faed371` |
| `trades.csv` SHA | `43239c991be0d942` | `57883a49f1dd6c40` |
| `config.csv` SHA | `084f817da00e8ef8` | `084f817da00e8ef8`（**未变**） |

### 2.2 V4 实现方法说明（无文件改动）

close 模式的「修复前」对照**通过运行时 monkey-patch 还原 helper 为 `_QSPriceState.orig` 实现**（≡ 修复前代码行为），
**未修改任何文件、未回退代码**——避免了对共享核心文件的临时改动风险。

## 3. 影响面声明（复核方案件 §4）

| 维度 | 实测确认 |
| --- | --- |
| open 模式策略 | **行为改变**（预期）：换算价与成交基准对齐 |
| close 模式策略 | **逐位不变**（V4 实证） |
| `config.csv` | 两种模式均不变 |
| 记账/估值/撮合价本身 | 未触及 |
| 策略源码 | **零改动**（符合「仅限框架层」铁律） |
| 分钟 profile | 按方案件 §3A.3 **显式声明不覆盖**，并保持「取不到即回退」的不劣化保证 |

## 4. 待办（⑤用户确认后）

| # | 项 |
| --- | --- |
| 1 | **README.md + `docs/strategy_toolbox.md` + `docs/prompt_engineering.md`** 中涉及该修复的表述同步更新（六步同步内容完整性要求） |
| 2 | ⑥双仓库推送（`quantstudio-plus` / `quantstudio`），推送后核对双远程 HEAD 逐位一致 |
| 3 | 推送后 QuantStudio-trading 副本同步门（本件触及 `quantstudio/` 共享层 → **不豁免**） |
| 4 | 本策略（`csi300_slow_kd_reversal`）与 F1/F2 **同批**重跑 R4/R5×2/R5.5 + 重新发布 |
| 5 | 分钟 profile 面是否需另立方案件（须先由引擎侧确认其撮合基准价） |

## 5. 结论

**六步流水线①方案 →②审计（PASS+两条件）→③实施 →④验收（V1-V6 全 PASS）已完成。**
缺陷根因（open 模式下换算价取收盘、成交基准为开盘）已消除：**低于整手上限与超额双双归零，最大成交额精确落在 100,000**；
**close 模式零回归**（三件套逐位一致），契约门禁全绿。**待用户确认后进入⑥双仓库推送。**
