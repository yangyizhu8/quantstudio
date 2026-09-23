# R2 附卷凭证 — E1 原则落位与四格实测

> 性质：R2 设计契约的直接依据凭证（E1 是本设计时序契约的授权来源）
> 关联设计稿：`output/generated_strategies/ou_reversal_csi300_10/agent_strategy_design.json`
> 生成时间：2026-09-22

---

## 1. 用户批复（verbatim）

> **批准双落位。**

（用户批复原文，2026-09-22；对应「E1 铁律落位批准：① 文本写入项目 AGENTS.md 铁律节；② skill 双落位」两项。）

## 2. 双落位 SHA-256 凭证

| 文件 | 项目内副本 SHA-256（前 16 位） | 用户级副本 SHA-256（前 16 位） | 状态 |
| --- | --- | --- | --- |
| `skills/quantstudio-strategy-compiler/SKILL.md` | `228c530f81ff8c6d` | `228c530f81ff8c6d` | MATCH |
| `skills/quantstudio-strategy-compiler/references/no-lookahead-rules.md` | `44ba9fc9f5416db9` | `44ba9fc9f5416db9` | MATCH |

- 用户级路径：`C:\Users\Administrator\.agents\skills\quantstudio-strategy-compiler\`
- 同步脚本：`agent_workspace/ou_reversal/sync_skill_copies.py`（逐文件 copy2 + 双侧 SHA-256 比对）
- 校验器：`validate_agent_strategy.py` **零改动**（现行 `NO-LOOKAHEAD-INCLUDE` BLOCK 已实现 E1-1；未新增日线豁免域）
- 矩阵 reverify：**无需**（未触 `_QS_*` wrapper 模板串）

## 3. E1 原则与 R0 C2-A 的对应关系（防「信号后移」误读）

```text
R0 C2-A 原文：T 日收盘信号 → T+1 开盘执行
E1 落地链  ：S 日（= D-1）收盘信号 → D 日开盘成交

取 T = S = D-1、T+1 = D：
  · 数据末端  ：T 日收盘（S 收盘）—— 两者相同
  · 成交价    ：T+1 开盘（D 开盘）—— 两者相同
⇒ 两条链逐日完全一致，信号时效零损失。
```

**不得在近似表或设计说明中写入「信号后移一交易日」**——该表述源于把执行日标签（D）与信号日标签（T）换位比较，是错误自述。本设计统一采用 D/S 记法并在 `factor_definitions.date_convention` 中显式给出换算关系。

## 4. 四格实测（E1 实证材料）

**探针**：`agent_workspace/ou_reversal/probe_include_semantics2.py`
**方法**：真实 `run_backtest(...)`（daily-open-close-proxy-v1 profile，窗口 2025-07-01..2025-07-02），策略在 09:31 与 15:00 两个合成快照分别调用
`get_history(2, frequency='1d', field=['close','trade_date'], security_list='600519.SS', fq='pre', include=<T/F>, is_dict=True)`，读取返回帧末行 `trade_date`。

| 执行日时钟 | `include` | 返回末行 `trade_date` | 判定 |
| --- | --- | --- | --- |
| 2025-07-01 15:00 | `False` | 2025-06-30（D-1） | 不含 D |
| 2025-07-01 15:00 | `True` | 2025-07-01（D） | 含 D |
| 2025-07-02 09:31 | `False` | 2025-07-01（D-1） | **E1 合规取数** |
| 2025-07-02 09:31 | `True` | 2025-07-02（D） | **含 D 全日 bar = 真实未来泄漏** |

**源码互证（三段，与审核方一致）**

- `ptrade_api.py:1321-1325`（get_history 日线分支）：`anchor_date = (current_date if include else prev_date)`
- `ptrade_api.py:598`（attach_bar）：`self._prev_date = prev_date` 直写
- `backtest_engine.py:2267-2283`（proxy 快照循环）：09:31 与 15:00 两快照共用同一 `prev_day_str`

**归档注记**：实测运行环境为 `daily-open-close-proxy-v1` profile（该策略已弃用）；
**include 锚定结论对 profile 通用**——锚定由 `attach_bar` 直写 `_prev_date` 实现，与 profile 选择无关。

## 5. 本设计对 E1 四条的落点

| E1 条目 | 本设计落点 |
| --- | --- |
| E1-1 通用 API 取数 | 全部信号经 `get_history(..., fq='pre', include=False)`；源码零 `include=True` 日线调用（R4 断言 A4-2） |
| E1-2 成交价模式 | `match_price_mode='open'` 显式声明；语义版本 `0.1.0-legacy`（R4 断言 A4-3） |
| E1-3 执行层数据边界 | **零 `data[code]` 依赖**（不读任何字段）；涨跌停经前复权比值、停牌经 `get_stock_status`（R4 断言 A4-4 待确认） |
| E1-4 专门 API 边界 | `get_index_day_bar` 消费行显式限定 `trade_date ≤ S`（丢弃含 D 的末行）（R4 断言 A4-1） |

## 6. 治理链闭环记录

```text
R2 送审 → 审核方发现 15:00 含 T 声明与引擎源码矛盾（三段证据）
        → 我方四格实测复核（阻断成立）
        → 生产先例核对（first_cover 为 1m 分钟豁免域，非日线先例——双向纠偏）
        → A′ 方案起草（proxy 15:00 日线 include=True 豁免）
        → 总调度裁定：不开第二例外，E1 升格为原则层
        → E1 铁律文本定稿（七条，两处实质修订）
        → 双落位执行（SHA 一致）+ 本凭证归档
闭环结果：一次 R2 时序契约错误，沉淀为一条框架层铁律（E1）。
A′ 状态：未进入实施（六步未启动，零成本撤回，登记为治理转向而非技术否决）。
```
