# QMT 管线 M3.1 · 小六步方案（F-1 修复，2026-10-09 呈②审）

> **立项依据**：M3 ④验收实质发现 **F-1**（`docs/evidence/qmt-m3-acceptance-20261009.md` §4）；
> 用户 2026-10-09 裁定 **M3.1 立项**（方案＝证券代码模式识别 + 显式形态声明**双轨**），并定
> **正确目标形态由 M5-1 钉死后合并验收**。
> **小六步**：①方案（本件）→ ②审 → ③实施 → ④验收（**两段制**，见 §5）→（不涉⑤⑥，随 M3/M5 批）。

## ① 方案

### 1.1 缺陷定位（代码实证）

| 项 | 内容 |
|---|---|
| 缺陷函数 | `_qs_fin_to_rows`，`quantstudio/strategy_compiler/source_import_qmt.py:971`（**产物注入面 EXT 模板内**） |
| 唯一调用点 | `:1033`（`_qs_get_fundamentals` 内） |
| 消费契约（调用方实需） | `rows.get(code)` → `row.get(native_field)` 即 **`{code: {native_field: value}}`**（code 层 + 字段层，两级） |
| 机理 | 现实现以「第一层 value 是否 dict + 第二层 value 是否 dict」探测形态；而 `{code:{field:v}}` 与 `{field:{code:v}}` **结构完全同构**（两层 dict + 叶子标量），**仅凭结构不可区分** → 纯 dict 形态被误判为 field-major，致 `rows.get(code)` 全 miss → **全 NaN**（不抛错，静默错值） |
| 危害等级 | **高**（静默错值：fundamentals 面 3/6 策略消费；且 NaN 契约行与"真的缺数"不可区分） |

### 1.2 修复设计（**双轨**，用户裁定）

**轨 A · 显式形态声明（主）**
`_qs_fin_to_rows(raw, *, shape=None)` 增加显式参数：
- `shape='code_major'` / `shape='field_major'`：调用方**声明源形态**，函数按声明解析，**不做任何猜测**；
- M5-1 钉死真实形态后，`_qs_get_fundamentals` 直接传该形态 → 路径**确定**、零歧义。

**轨 B · 证券代码模式识别（兜底）**
`shape=None`（形态未知）时，用**证券代码正则**判定哪一层是 code 层：
```
_QS_CODE_RE = re.compile(r'^\d{6}\.(SH|SZ|BJ)$', re.IGNORECASE)
```
- 第一层 keys 命中 → code-major；否则探测内层 keys 命中 → field-major；
- **两层都不命中/都不确定 → 保守返回 `{}` 并打印审计行** `QS_QMT_FIN_SHAPE_UNDETERMINED`，
  **绝不猜**（现状的"猜错致全 NaN"被消除；"取不到数"与"取错数"必须可区分）。

**保留既有第三形态**：`hasattr(raw,'to_dict')` → `to_dict('index')`（DataFrame 投影，依据 `07-行情函数.md:1889`）
——该路径已有，不动。

### 1.3 纯增益论证（三问）

| 问 | 判 |
|---|---|
| ①是否影响项目其他功能 | **否**——仅改产物注入面的 1 个函数（+ 其唯一调用点传参）；PTrade 侧零触碰；`source_import.py` 零改动 |
| ②是否影响回测性能 | **否**——新增为 O(1) 正则探测（≤5 个 key 抽样），不在既有热路径 |
| ③是否影响回测精度 | **否**——本地引擎零触碰；**产物侧取值只会更对**（见判型） |

**判型声明**：**数据修正型**——结果按正确口径变化。
- **影响面**＝产物侧 fundamentals 取数面（`_qs_fin_to_rows` 及其唯一调用点）；
- **diff 只能源于修正行集**（该函数体 + 调用点一行传参），出现任何归因不了的 diff 即验收失败回退；
- **准确度只升不降**：现状纯 dict 形态 → 全 NaN（错）；修复后 → 正确取值或显式 `UNDETERMINED`（可区分）；
- **不改变**：`to_dict` 路径、空→NaN 契约行语义、`report_type='announce_time'` 钉死、毫秒时间戳转换、
  `valuation.float_value` 派生逻辑。

## ② 待②审要点

1. **双轨必要性**：轨 A 是否应与轨 B **同时落地**（M5-1 未钉死前轨 B 是唯一生效路径）——本方案判"是"（否则修复空转）；
2. **不确定时保守返回 `{}` + 审计行**是否优于"取第一个分支"——本方案判"是"（静默错值比显式缺数危险）；
3. 正则覆盖度：`\d{6}\.(SH|SZ|BJ)` 是否足够（六策略池实测形态为 `600000.SH`/`000009.SZ`；BJ 为北交所预留）；
4. 审计行命名 `QS_QMT_FIN_SHAPE_UNDETERMINED` 是否符合既有 `QS_QMT_*` 命名口径。

## ③ 实施范围（允许写入清单）

| # | 文件 | 动作 |
|---|---|---|
| 1 | `quantstudio/strategy_compiler/source_import_qmt.py` | 改：`_qs_fin_to_rows` 双轨实现 + 调用点传参（**仅此两处**） |
| 2 | `tests/test_source_import_qmt.py` | 增：三形态单测（code_major / field_major / 不确定→`{}`+审计行）+ `to_dict` 路径回归 |
| 3 | `docs/evidence/qmt-m3.1-acceptance-20261009.md` | 新建：④验收证据（含 M5-1 合并验收口径执行记录） |

**禁止触碰**：`source_import.py` / `orchestrator.py` / `portability_rules.py` / `cli.py`（M2b 已定稿）；
六策略源文件；`tests/qmt_stub.py` / `tests/test_qmt_stub_smoke.py`（M3 已提交，**仅可只读回归**）。

**Out of scope**：`get_financial_data` 的其他形态（多记录面板 `07:1890`）；fundamentals 字段映射表变更；
数值对照（M5）。

## ④ 验收判据（**两段制**，用户裁定的合并验收口径）

**段一 · 本机（M3.1 完成即验）**：
1. 三形态单测全绿：`code_major` / `field_major` / **不确定 → `{}` + 审计行**（不猜）；
2. `to_dict` 路径回归不变（DataFrame 投影仍正确）；
3. **桩冒烟回归**：`pytest tests/test_qmt_stub_smoke.py` → 仍 **6 passed**（M3 基线不破）；
4. 既有回归：`tests/test_source_import_qmt.py` 全绿（M2b 6 + minute deny 4 + 本次新增）；
5. 六策略 QMT 产物重生成后过 `validate_qmt_portability`（**6/6**）+ PTrade byte-diff 不变（同源终证法）；
6. 策略源码零改动（hash 基线）。

**段二 · M5-1 钉死后（合并验收）**：
7. M5-1 实测真实返回形态 → 以**轨 A 显式声明**接入 → fundamentals 端到端取值正确（三策略消费面：
   CANSLIM / weekly_smallcap / 周频小市值）；
8. 若真实形态非既有形态 → 扩展解析分支 + 回归，**且不得放宽段一第 1 条**（不确定仍保守）；
9. 段二结论写入 M5 证据文档 + 回填 M3.1 证据文档（**合并验收关闭**）。

**回退**：写前快照（`git stash create -u` → `store`）+ 两文件 cp 备份落 `docs/handoff/`；
单点回退＝还原 `_qs_fin_to_rows` 与其调用点。

**失败判定**：段一任一条红 / 出现归因不了的 diff（违反数据修正型约束）/ 桩冒烟回归跌破 6 passed。

## ⑤ 风险

| # | 风险 | 处置 |
|---|---|---|
| 1 | 轨 B 正则在真实形态下误判（如 code 层 key 非标准格式） | 段二由 M5-1 实测钉死；误判时走轨 A 显式声明（**声明优先于识别**） |
| 2 | 保守返回 `{}` 使"取不到数"增多（相较现状的"取错数"） | 这是**有意选择**：显式缺数 > 静默错值；审计行可观测 |
| 3 | 桩冒烟回归被本次改动打破 | 段一第 3 条硬门；桩侧桥接（`QmtStubFinData`）走 `to_dict` 路径，与本改动正交 |
| 4 | 与 M5 并行推进的时序耦合 | 段一/段二分离，段一不阻塞 M5；段二随 M5-1 到达触发 |

---

**暂停语义**：本方案呈②审；②审通过后进 ③实施（两文件内改动 + 单测），④段一验收后呈用户。
