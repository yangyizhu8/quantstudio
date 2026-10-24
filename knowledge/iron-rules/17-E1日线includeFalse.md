## 【铁律】日线策略信号取数：执行日以 include=False 读取前一交易日日线（E1，2026-09-22 定稿）

> 确立：2026-09-22（用户裁定）。跨会话、跨智能体、跨项目长期有效，除非用户明确要求修改或撤销。

1. 任何以日线为信号源的策略（含股票/ETF/指数择时），信号计算一律经**通用历史 API**
   （`get_history` / `get_price` / `get_history_batch`）以 `include=False` 读取前一交易日（D-1）日线
   ——取数 API 恒不返回「当前回调所在交易日的日线」，从 API 口径层杜绝未来函数，
   不依赖策略作者自觉，不依赖 profile/时点选择。
2. `include=True` 的日线取数在所有生成策略中**禁止**（校验器 `NO-LOOKAHEAD-INCLUDE` 已实现该 BLOCK）；
   不存在以「当日已收盘」为理由的例外。
3. 成交价模式由策略语义裁定（`close` / `open` 均合法）；当策略语义为「开盘执行」时，必须显式声明
   `match_price_mode='open'`——沿用默认 `close` 会使成交价漂移到 D 日收盘，属实质执行价差偏差。
4. `data[code]` 当日 raw 快照仅限**执行层判断**（涨跌停/停牌），**不得作为信号输入**；
   信号价格一律来自 `get_history(..., fq='pre', include=False)`。
5. 实证依据：2026-09-22 四格实测（执行日 15:00/09:31 × include True/False，
   证实 `include=False` 锚定 `prev_date`、`include=True` 在执行日 09:31 语境含当日全日 bar）。
   实测环境为 proxy profile；include 锚定结论对 profile 通用——锚定由 `attach_bar` 直写 `_prev_date` 实现
   （`ptrade_api.py:598`），与 profile 选择无关。
6. 本条跨会话、跨智能体（dsh/CodeBuddy/workbuddy/ZCode 等）、跨项目共同遵守，长期有效。
7. **专门 API 边界**：`get_index_day_bar` 等 profile-aware 专门 API 保留其已审定契约
   （daily-bar-v1 含当前日 D；恐慌抄底先例），不因本条变更——契约变更须走框架流程。
   凡成交时点早于当日收盘的策略（如 match open），消费该 API 时必须**显式取 D-1 行**
   （`count>=2` 取 `[-2]`，或断言末行 `trade_date==D` 后弃用）：
   D 日开盘成交而信号消费 D 日收盘指数行，构成真实未来函数泄漏。

> 用户批复（2026-09-22）：「批准双落位。」（项目 AGENTS.md 铁律节 + skill 双副本同步）
> 落位凭证：`output/generated_strategies/ou_reversal_csi300_10/R2_E1_EVIDENCE_APPENDIX.md`


