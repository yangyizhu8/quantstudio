# source_import pctChg 字段可移植性修复设计（2026-09-01 平台实证回归）

## 1. 问题定义

### 现象
PTrade 实跑打板策略（`连板梯队龙头打板套利策略.py` 转换产物），`get_history_batch` 对全市场 55xx 个代码逐个 skip：

```
WARNING - get_history_batch skip 301370.SZ: function get_history: invalid field ['pctChg'],
valid fields are {'price', 'money', 'high', 'close', 'open', 'unlimited', 'preclose', 'is_open',
'low_limit', 'low', 'volume', 'high_limit'}
```

→ 全市场历史数据全部不可用，策略全程 zero-position 空仓跑空（session 日志仅剩 QS_PORTFOLIO_AUDIT
positions=0，无任何候选/信号产出）。

### 根因（已实证，非猜测）
1. `_QS_HISTORY_WRAPPER`（**无条件注入**，`source_import.py` L192-258）请求侧只做
   `amount→money`、`preClose→preclose` 字段名映射，**没有 pctChg 处理**——pctChg 原样透传给平台
   `get_history` → 平台字段白名单拒绝 → `get_history_batch` shim（L3463 调 get_history）捕获异常逐码 skip。
2. `pctChg` 的「请求侧剔除 + 返回侧合成」只存在于 **trade_date 门控扩展** `_QS_HISTORY_TRADE_DATE_EXT`
   （L265-428，`_QS_SYNTHETIC_FIELDS={'trade_date','pctChg'}` + `_qs_synthesize_pct_chg` L316-333）。
   该扩展仅当 `_source_uses_trade_date(code)` 为真时注入（L3323-3325）。
   （设计与口径出处：D4-S7 `docs/ptrade-pctchg-synth-design.md` + `docs/evidence/ptrade-pctchg-synth-evidence.md`，
   2026-08-27 ZCode M1-M3 审计通过——本设计是其**覆盖缺口补齐**：合成能力从门控扩展下沉为无条件公共能力。）
3. 打板 / 断板反包策略均**不使用 trade_date** → 扩展未注入 → pctChg 可移植能力缺失。

### 影响面（通用性问题）
任何在 `get_history` / `get_history_batch` 的 `fields` 中请求 `pctChg` 且不使用 trade_date 的策略，
转换产物在 PTrade 侧**必然全量 skip**。已确认命中 2 个已发布策略：
- `连板梯队龙头打板套利策略.py`（fields 含 `"pctChg"`，L160）
- `断板反包策略.py`（FIELDS 含 `'pctChg'`，L38/L151）

非单策略缺陷 → 修复必须落框架层并具备通用性，禁止改任何策略源码。

## 2. 修复方案（框架层，策略源码零改动，纯增益）

把 `pctChg` 可移植能力从 trade_date 门控扩展中**拆出为无条件公共能力**，内嵌进 `_QS_HISTORY_WRAPPER`
（该段本就无条件注入，是 get_history 全链路的统一入口）：

1. **请求侧剔除**：`get_history` wrapper 映射字段时，把请求列表中的 `'pctChg'` 剔除（绝不发给平台）；
   若剔除后为空 → 兜底 `['close']`（与门控版 L369-370 同规则）。
   **v2 增补（2026-09-01 平台实证第二轮）**：平台返回列=请求列 → 剔除 pctChg 后须**自动注入
   `preclose` 基列**（请求含 pctChg 且无 preclose 时），否则返回侧合成缺基列（平台 pct=None 实证）。
   基础 wrapper 与 trade_date 门控版**双版本同步**（D4-S7 遗留同源缺口一并补齐）。
2. **意图记录**：模块级标志 `_QS_REQ_PCT` 记录本次请求是否含 pctChg（每次请求先复位 False，含则置位）。
3. **返回侧合成**：`_qs_to_dataframe` 在列名映射（PTrade→本地）之后：
   `_QS_REQ_PCT` 为真 且 df 无 `pctChg` 列 且 有 `close` + `preClose` 基列 →
   合成 `pctChg = (close/preClose − 1) × 100`；
   fail-soft：异常/基列缺失 → 不合成（策略 `_extract_history_field` 取列失败按数据不足跳过，不崩）。
4. **全路径覆盖**：单码返回 / dict 批量返回均走 `_qs_to_dataframe` → 全部覆盖；
   `get_history_batch` shim 内部调 wrapper `get_history` → 自动受益，无需改 shim。

### 与既有门控扩展共存（双轨不互扰）
- trade_date 门控扩展整段后注入、重定义同名 `_qs_to_dataframe` / `get_history` →
  trade_date 策略走全能力版（含 pctChg，现行为完全不变）；
- 非 trade_date 策略走基础增强版 → pctChg 能力补齐（本次目标）。
- 两版对 pctChg 的处理语义一致（剔除 + 同公式合成），无路径分叉风险。

### 本地形态等价性
本地引擎 `get_history_batch` 原生支持 `pctChg` 列。wrapper 请求侧剔除后本地返回无 `pctChg` 列 →
返回侧由 `close/preClose` 合成还原。本地库口径即 `aligner.py` L1088 `pctChg=(close/preClose−1)*100`
与合成公式**同式同语义**（±浮点级别，打板 `_is_limit_up` 容差 0.01 覆盖）→ 本地回测结果不变。

## 3. 改动范围

- `quantstudio/strategy_compiler/source_import.py`：
  仅 `_QS_HISTORY_WRAPPER` 段（L192-258）增强——新增 `_QS_REQ_PCT` 标志、`_qs_to_dataframe` 末尾
  pctChg 合成、`get_history` 请求侧剔除（约 +25 行）。
- 不动：trade_date 门控、其余门控扩展、shims、`_PTRADE_HELPERS`、`_QS_COMMON_EXT`、任何策略源码。
- 文档同步：README.md / docs/strategy_toolbox.md / docs/prompt_engineering.md 登记
  「pctChg 平台可移植能力（请求剔除 + 返回合成，无条件注入，2026-09-01 平台实证回归修复）」。

## 4. 验收标准

1. **产物能力断言**：打板 / 断板反包 重转产物含 pctChg 合成段（`_QS_REQ_PCT`）；
   策略侧 `fields` 字面量可保留 `'pctChg'`（**运行时剔除**策略，覆盖 fields 来自变量/拼接等
   非字面量场景，比编译期 AST 改写更通用）——平台**实收**字段无 pctChg 由验收 2 mock 断言。
2. **平台模拟门禁**：以日志 valid-fields 白名单构造 mock 平台 `get_history`
   （遇 `pctChg` 即抛 `invalid field`）→ 执行重转打板产物 → **0 skip**，
   QS_SCREEN_AUDIT / QS_PORTFOLIO_AUDIT 正常产出。
3. **口径断言**：合成 `pctChg` 与本地引擎原生值逐位差 ≤ 1e-6（代表码/日期抽查）。
4. **6 策略横验证**：CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev /
   weekly_smallcap_growth / 周频小市值成长动量（三层止损）重转 api_portability 全 PASS。
5. **回归**：ptrade_contract_compliance / fund_matrix_coverage / fidelity 等既有测试套件全绿；
   打板本地 R4 SHA 回归——**判定路径（2026-09-01 落实）**：合成口径逐位一致（ACCEPT3 排中律：
   合成 ≡ 本地原生 → 确定性引擎全链路输出必然一致）承担结论性证据；全窗 ≈80 min 后台 job 被宿主
   清理器终止后，以 3 交易日分钟冒烟（新产物本地可跑、daily_stats 形状正常）作运行确认；
   旧产物字节未入 git 不可复原（仅存发布哈希 adf0face…），不做同窗旧新 SHA 对比。
6. **纯增益**：非 pctChg 请求的策略（fall_reversal 等）产物行为不变
   （多注入合成段，但无请求 → 不合成 → 无观察差异）。

## 5. 回退条件

- 任一验收项失败且无法归因于测试环境 → 单文件定向回退 `_QS_HISTORY_WRAPPER`
  （git diff 重建原段），产物重转即恢复；不影响其余注入。
- 平台侧若再次出现 pctChg 相关 skip → 回退 + 复查合成路径。

## 6. 证据与提交

- 证据文档：`docs/evidence/pctchg-portability-*.md`（验收 1-6 逐项落盘）。
- 提交前经用户明确确认；双仓库推送后核对 two remotes 一致。