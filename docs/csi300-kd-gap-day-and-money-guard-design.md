# 修法设计：F1（数据缺口日状态语义）+ F2（money 有限性守卫）

> 状态：**设计备好，暂不动文件**（总调度裁定：与换算价修复落地后同批实施 + 一次重跑）
> 依据：ZCode 第 1 片疑点 5 + 第 2 片 F1（**两片独立同点命中**）、第 2 片 F2；**S-3 裁定**（总调度 2026-10-03）
> 落点：`agent_workspace/csi300_slow_kd_reversal/strategy.py`（策略层）
> 触发门禁：R4 + R5 双跑 + R5.5 + 重新发布（与换算价修复合并为**一次**）

## 0. S-3 裁定原文（verbatim）

```text
缺口日不计入 held_bars——S 日 bar 缺失=该标的当日无有效观察，计数跳过+留痕
（与 PIT「无数据≠假数据」原则对齐）——F1 修法依据成立：S 日缺失时跳过状态推进
（low_streak 不清零+止盈不禁用+止损不跳过——当日维持前态+逐标的留痕）
```

## 1. F1 问题定义（两片复核交叉命中）

`_held_signals` 对 `closes.size == 0` 的标的**直接跳过**（不写入 `held_sig`）→ `_execute`/`_advance_holding_state` 取到空记录 → `close_s = _finite(None, 0.0) = 0.0`，于是：

| # | 当前行为 | 性质 |
| --- | --- | --- |
| ① | `_advance_holding_state` 走 `else` 分支 → **`low_streak` 被静默清零** | 极端止损计数被数据缺口重置 |
| ② | `overbought_dead` 恒 `False` → **止盈当日静默禁用** | 信号被静默吞掉 |
| ③ | 固定止损因 `close_s > 0` 守卫被**跳过** | 风控静默失效 |
| ④ | `held_bars` 仍 +1 | **与 S-3 裁定冲突**（缺口日不应计数） |

**净效果**：数据缺口日，该标的处于 **fail-open** 状态——风控与止盈同时静默失效，仅时间止损兜底。

## 2. F1 修法（依 S-3：跳过状态推进 + 逐标的留痕）

### 2.1 `_advance_holding_state` 增加「无有效观察」前置守卫

```python
for code in sorted(held.keys()):
    sig = held_sig.get(code) or {}
    close_s = _finite(sig.get('close'), 0.0)
    if close_s <= 0.0:
        # S-3 裁定：S 日 bar 缺失 = 当日无有效观察 → 跳过状态推进（维持前态）
        log.warning('QS_GAP_NO_ADVANCE code=%s signal_date=%s' % (code, signal_date))
        continue            # held_bars 不 +1、low_streak 不清零、min_close 不变
    ...原逻辑（首观测登记 / held_bars+1 / running min 与 streak 更新）...
```

### 2.2 `_execute` 的清仓判定同步跳过无观察标的

```python
for code in sorted(held.keys()):
    sig = held_sig.get(code) or {}
    close_s = _finite(sig.get('close'), 0.0)
    if close_s <= 0.0:
        continue            # 无有效观察 → 当日不做清仓判定（维持前态），不写 sell_reasons
    ...原三重止损 + 止盈判定...
```

### 2.3 语义收敛表（修复后）

| 缺口日的行为 | 修复前 | **修复后** |
| --- | --- | --- |
| `held_bars` | +1（计数被虚增） | **不变**（S-3：不计入） |
| `low_streak` | 清零（计数被重置） | **不变** |
| `min_close`（running min） | 不变 | 不变 |
| 固定止损（8%） | 跳过 | **跳过**（无收盘价可比较） |
| 极端止损（连续 5 日新低） | 计数被清零、当日误判为非新低 | **维持前态**（不清零、不计入） |
| 止盈（超买死叉） | 静默禁用（`overbought_dead=False`） | **不做判定**（非「判定为否」） |
| 追溯留痕 | 无 | **`QS_GAP_NO_ADVANCE` 逐标的 warning** |

### 2.4 措辞歧义 → **已裁定：甲**

裁定括号内原写「low_streak 不清零 + **止盈不禁用** + **止损不跳过**——当日维持前态」。
「维持前态」与「止损不跳过」字面存在张力，本线标注请裁后，总调度 2026-10-03 裁定如下：

> **三小裁定原文（verbatim）**：
> 「F1 措辞歧义=甲（缺口日不做任何判定、状态冻结、有数据日恢复）——「止损不跳过」系我前轮裁定原文的措辞不精确；
> 甲与 S-3 主句及 PIT 原则自洽为正确读法——固定止损「跳过」是因为无收盘价可比（状态冻结的自然结果），非主动判定」

**裁定结论：甲。** 本设计 §2.1/§2.2 即按甲实施，**无需修改**。
「止损不跳过」一句确认为**前轮措辞不精确**，不再作为约束；固定止损在缺口日不执行，属**无数据可判的自然结果**，而非主动跳过风控。

| 读法 | 含义 | 后果 |
| --- | --- | --- |
| **甲（我按「维持前态」采纳，已据此设计）** | 缺口日**不做任何判定**，一切状态冻结；有数据日恢复 | 缺口日无风控动作；长期停牌股的持仓日计数被顺延（与 S-3 一致） |
| 乙 | 缺口日**仍照常判止盈/止损**，用「最后可得收盘价」代替缺失的 S 日收盘 | 风控不中断；但「用旧价当作今日价」与 PIT「无数据≠假数据」原则冲突，需另行举证 |

**读法乙已作废**（不再作为候选）：其「用旧价当作今日价」与 PIT「无数据≠假数据」原则冲突，裁定书已明确甲为正确读法。

## 3. F2 修法（money 有限性守卫）

### 3.1 问题

`_select_candidates` 中 `closes/highs/lows` 均有 `np.all(np.isfinite(...))` 守卫，**`money` 没有**：
```python
amount = float(money[-1]) if money.size else 0.0     # NaN 会原样进入排序键
ranked.sort(key=lambda item: (-item[1], item[0]))     # NaN 参与比较恒 False → 名次语义未定义
```

### 3.2 修法 → **已裁定：A**

> **三小裁定原文（verbatim）**：
> 「F2=A（非有限→剔除该候选+QS_MONEY_INVALID 留痕）——与 PIT「无数据≠假数据」同源原则；B 的置 0 在语义上是假数据」

| 候选 | 做法 | 取舍 | 裁定 |
| --- | --- | --- | --- |
| **A** | `money[-1]` 非有限 → **剔除该候选** + `QS_MONEY_INVALID` 留痕 | 与 F1 同源原则（**无有效观察则不参与判定**）；成交额缺失下无法按 C11-C 排序，剔除最诚实 | **采纳** |
| B | 非有限 → 置 `0.0`（排到末位） | 保留其被选资格；但「0 成交额」在语义上是假数据 | **不采纳** |

```python
if money.size:
    raw_amount = float(money[-1])
else:
    raw_amount = float('nan')
if not np.isfinite(raw_amount):
    log.warning('QS_MONEY_INVALID code=%s signal_date=%s -> excluded' % (code, signal_date))
    continue
amount = raw_amount
```

> 同样留作审核确认项：A/B 二选一（我倾向 A）。

## 4. 影响面（须显式声明）

| 维度 | 声明 |
| --- | --- |
| 触发条件 | 持仓标的或候选标的在信号日 S 无日线 bar（长期停牌、采集缺口、退市过渡期） |
| 本窗口频次 | 本策略 287 日窗口内**未观测到该路径触发**（`unfilled`=0、无 `QS_HELD_HISTORY_FAIL`），属**边缘防御性修复** |
| 结果变化 | 仅在「真的出现数据缺口」时改变行为；无缺口时**逐位不变** |
| 与 F2 的关系 | 同源（无有效观察则不参与判定），同批实施 |

## 5. 验收标准

| # | 判据 |
| --- | --- |
| W1 | **无缺口零回归**：本策略全窗口重跑，若窗口内无缺口触发则三件套与实施前**逐位一致**（证明修复不扰动正常路径） |
| W2 | **构造缺口单测**：mock 某持仓标的 S 日返回空 → 断言 `held_bars` 不变、`low_streak` 不变、`min_close` 不变、当日无该标的卖单、且输出 `QS_GAP_NO_ADVANCE` |
| W3 | **F2 单测**：mock `money[-1]=NaN` → 断言该候选被剔除且输出 `QS_MONEY_INVALID` |
| W4 | 无缺口日与实施前**逐位一致**（`QS_REBALANCE_AUDIT` / `QS_PORTFOLIO_AUDIT` 全量比对） |
| W5 | R4 静态校验 PASS + runtime-shape 夹具 0 failures |

## 6. 回退条件

- W1/W4 出现任何非缺口相关 diff → 立即回退（说明修复越界）；
- 单点回退：删除 2.1/2.2 的 `continue` 守卫与 3.2 的守卫。

## 7. 实施时序（总调度裁定）

**不单独实施**——与「换算价错配修复（方向甲）」落地后**同批实施**，之后做**一次** R4/R5×2/R5.5 重跑 + 重新发布，避免两轮全重跑。
