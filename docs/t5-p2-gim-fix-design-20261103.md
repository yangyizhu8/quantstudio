# T5 差异 P2 三项修复方案（草稿 · G / I / M）

> 状态：**方案草稿，未实施**（总调度 2026-11-03 23:5x 授权「等⑤期间先出 P2 三项（G/I/M）方案草稿，只出不实施」）
> 关联：`docs/ISSUES.md` `ISS-T5-G`／`ISS-T5-I`／`ISS-T5-M`｜`docs/evidence/api-surface-behavior-verification-20261028.md`（W5 终态）
> 知识反哺：`知识反哺：[命中] QD-2026-0010（qd/D1/QD-2026-0010.md，2026-10-12）`——本地引擎口径类条目的判域与「端测已知分歧」登记范式；
> 　　`知识反哺：[无命中] D3 产物内注入 API 面（G/I/M 均为**本地适配层**行为，非产物内注入，按 README §2.1 第 2 条不属 D3），2026-11-03`

---

## 一、问题定义（ISSUES 原文摘要）

| 编号 | 严重 | 现状 | 平台侧（文档） |
|---|---|---|---|
| `ISS-T5-G` | P2（返回值语义） | `get_stock_name` **本地以代码充当名称**（`ptrade_api.py:2537`，docstring 记「DuckDB 无 name 字段」） | 返回真实中文名；查不到 `value=None` |
| `ISS-T5-I` | P2（返回面） | `get_instruments` 仅返基础证券元信息，期货不支持（`ptrade_api.py:2481`） | 返合约信息（含 FutureParams）；实盘涨跌幅不可用 |
| `ISS-T5-M` | P2（未来函数面） | `get_stock_blocks` 恒返 `None`（`ptrade_api.py` 内 docstring 记「DuckDB 暂无板块表」） | 「获取当下数据，回测时注意未来函数」 |

## 二、根因定位（本轮新取证，**推翻两条既有假设**）

### 2.1 数据面实测（**只读副本** `data/backup_import_20261101_040935/quantstudio.db`，`read_only=True`）

```
表总数 = 101
含 name     -> ['stock_namechange']
含 blocks   -> []
含 industry -> ['industry_classification', 'industry_membership', 'sw_industry']

stock_namechange  行=14,950  列=['code','change_date','name_before','name_after','status_after','update_time','data_source']
  样例 = ('000530', 832291200000, '连大冷Ａ', '大冷股份', 'NORMAL', …)
industry_membership  行=7,738  列=['classification_system','classification_version','industry_level','industry_code','code','effective_from','effective_to','update_time','data_source']
industry_classification 行=62  列=[…,'industry_code','industry_name','industry_level','parent_industry_code','effective_from','effective_to',…]
sw_industry  行=0
```

### 2.2 三条根因结论

| 编号 | 既有假设 | **实测真相** | 结论 |
|---|---|---|---|
> **G 证据补强（2026-11-04 追加取证）**：`DuckDBReferenceDataProvider`（`providers/duckdb_provider.py:297`）**已实现**
> `_name_pit_map(as_of_ms)`（`:414`）／`get_security_name(code, as_of)`（`:430`）／`get_security_name_map(as_of)`（`:437`），
> 且 `get_industry(code, date)`（`:446`）走 `query_industry_membership` 并「**绝不回退 legacy `sw_industry` 快照**」。
> ⇒ `ptrade_api.get_stock_name` 的修复**零新增查询逻辑**，只需改调 `self._reference.get_security_name(...)`／`get_security_name_map(...)`。
> **这是「接线缺口」判定的第二条独立证据**（第一条＝`duckdb_data_access.py` 已有 PIT SQL）。

| **G** | 「DuckDB 无 name 字段」 | `stock_namechange` **14,950 行含 `name_after` 真实中文名**；且 **PIT 查询已存在**：`duckdb_data_access.py:459-472`（`change_date <= query_ms` 最近一条）；`backtest_engine.py:2217`／`ptrade_api.py:2701` 记 D-6（2026-10-09）「传 as_of → 股票简称由 stock_namechange 做 PIT 推导」 | **不是能力缺口，是接线缺口**：引擎内 `data[code]['name']` 已按日注入真名（`ptrade_api.py:278` 注释），但 **`get_stock_name()` API 未接同一路径** ⇒ **同一仓库两个面口径不一致** |
| **I** | 「仅返基础元信息」 | `contract_name` 同样是 `self._to_ptrade_code(bare)`（**名称位填代码**，与 G 同病）；`exchange` 取值本次控制台显示为 `???`（**判定为控制台编码显示问题，非代码缺陷**，实施前须以 python UTF-8 直读复核） | 修复面 = `contract_name` 接 PIT name ＋ 缺字段面按 T5 证据件补齐 ＋ 期货不支持显式化 |
| **M** | 「未来函数面 P2」 | **库内无任何板块表**（`含 blocks -> []`）⇒ 当前恒 `None`，**无数据即无未来函数可泄漏**；真实风险是**静默失效**（策略依赖板块池却在本地恒空）。补充取证：provider 层 `get_blocks` **未覆写**（落 `providers/base.py:187` 默认 `return None`）⇒ 现象根源同为**未接线**，非数据缺失 | **【已裁 2026-11-04】改判成立**：M 由「未来函数面 P2」**改判为「静默失效面 P2」**，并附**接线硬门**（见 §四-3）——总调度裁「定性更准」 |

> **重要边界**：G/I 的修复**不新建能力**，只把已存在的 PIT 查询接到公开 API 面 ⇒ 改动小、可回退、易验收。
> M 与 L 的「行业/板块接线」属**新增能力**，本方案只给硬门约束，**不建议在本批实施**。

## 三、改动范围（精确到文件:行，实施时才动）

| 序 | 文件 | 位置 | 改动性质 |
|---|---|---|---|
| 1 | `quantstudio/backtest/ptrade_api.py` | `get_stock_name`（现约 `:2537`，docstring＋两行返回） | 接线：调用既有 PIT 查询取 `name_after`；**无记录时 fail-closed 退化为代码**并留 docstring 声明 |
| 2 | `quantstudio/backtest/ptrade_api.py` | `get_instruments`（现约 `:2481`） | `contract_name` 接同一 PIT name；期货分支显式「不支持」返回面（保持 `{}` 或改 `None`，**须用户裁**，见 §五-裁定项） |
| 3 | `tests/test_ptrade_api_stock_name_pit.py` | 新建 | G 的正负用例：①有改名记录→返 `name_after`；②无记录→退化代码；③as_of 早于首次记录→退化；④列表入参逐项一致 |
| 4 | `tests/test_ptrade_api_instruments_surface.py` | 新建 | I 的字段面断言：`contract_name` 为真名、期货显式不支持、字段集与 T5 证据件对照表一致 |
| 5 | `docs/ISSUES.md` | `ISS-T5-G`／`I`／`M` 三行 | 状态与定性更新（M 改判候裁后执行） |
| 6 | `knowledge/qmt/QD-2026-0018.md` | 新建（R4 沉淀） | 「能力在库、适配层未接线」族：症状/根因/落点/固件/回哺 |

**禁触面**：`quantstudio/pipeline/**`（他线在途）、`quantstudio/delivery/**`、`quantstudio/backtest/backtest_engine.py` 的撮合逻辑、
`_QS_QMT_*_EXT` 模板串（D3 面，本批不涉）。

## 四、影响面与判型（铁律 18）

### 4.1 纯增益三型判定

| 编号 | 判型 | 理由 | 强制要求 |
|---|---|---|---|
| **G** | **数据修正型** | 返回值由「代码字符串」变为「真实中文名」＝可观察结果变化 | ①显式声明影响面（§4.2）；②黄金/回归 diff **只能归因于** `get_stock_name` 相关行集；③准确度只升不降（名称正确率↑）；④出现任何**其他** diff 即验收失败并回退 |
| **I** | **数据修正型 ＋ 新增能力型（字段面补齐部分）** | `contract_name` 同上；新增字段属「开始报告真相」 | 拆分验收：`contract_name` 按 G 口径；新增字段按「不引起既有功能回退」口径 |
| **M** | **不实施**（改判＋硬门约束） | 库内无板块表，改码无对象 | 只作文档与硬门登记，不产生代码 diff |

### 4.2 G/I 的影响面（必须先申报再实施）

- **会变的行为**：任何用 `get_stock_name`（或 `get_instruments()['contract_name']`）做判断的策略——
  如「名称含 ST/*ST 过滤」「名称含特定关键字选股」「按名称生成产物文件名」——其**选股集合或产物命名可能改变**。
- **不会变的行为**：撮合/费用/滑点/T+1/涨跌停、`data[code]['name']`（引擎已注入真名，本批不动）、
  六策略 QMT 转换产物（`output/qmt_import/*.py` 的 SHA16 基线 `7f31a86b…` 等六值**必须逐位不变**）。
- **硬门**：实施后重跑 `scripts/qmt_reconvert_six.py`，六个 SHA16 与
  `docs/evidence/qmt-conv-correctness-baseline-20261103.md` 逐位对照，**任一变化即回退**（因 G/I 不应改变转换产物）。

### 4.3 接线硬门（M 改判的配套约束，**永久条款**）

> 凡将来**接入**板块（`get_blocks`）或行业反向查询（`get_industry_stocks`）能力时，必须同时满足以下三条，缺一不得合并：

1. **必须走 PIT 双时态**：只用库内已具备的 `effective_from` / `effective_to`（`industry_membership` 7,738 行、`industry_classification` 62 行均为此形态）做 `as_of` 过滤，
   **禁止裸查「当下」**（违反即引入未来函数，与 `ISS-T5-M` 原始担忧同源）；
2. **必须 fail-closed**：沿用既有纪律「**绝不回退 legacy `sw_industry` 快照**」（`providers/duckdb_provider.py:453`／`duckdb_data_access.py:2326` 原文），
   表缺失时抛能力缺口错误而非静默返回空；
3. **必须显式声明静默面**：`get_blocks` / `get_industry_stocks` 的 docstring 与 ISSUES 条目须写明「无数据时返 `None`/`[]`，
   策略若依赖该项须自行降级」——把**静默失效**变成**可读失效**。

## 五、验收标准（实施轮用）

| 门 | 判据 | 方法 |
|---|---|---|
| V1 | G 正负用例全绿（4 例） | `pytest tests/test_ptrade_api_stock_name_pit.py` |
| V2 | I 字段面用例全绿 | `pytest tests/test_ptrade_api_instruments_surface.py` |
| V3 | 转换产物零漂移 | 六策略 SHA16 == 基线六值（逐位） |
| V4 | 相关既有测试无新增失败 | `pytest tests -q` 与基线 `5cd6327` 读数对比（裁一口径：**无新增失败**） |
| V5 | 黄金零差异（若触及引擎路径） | 若实施中被判定触及回测主链，跑 `--profile minute-bar-v1` A/B 与 11-01 锚三方核 |
| V6 | 知识反哺 | `kb_hook.py --strict` 对 `QD-2026-0018` 不合规 0 件 |

## 六、回退条件

| 触发 | 手段 |
|---|---|
| V3 六值任一漂移 | `git show <baseline>:quantstudio/backtest/ptrade_api.py > …` 逐文件还原（禁整仓 reset）；回退点＝实施轮开工前的 `git stash create` 四步配方 |
| V4 出现任何**新增**失败 | 同上回退，并把该失败登记 ISSUES |
| 名称面出现「准确度下降」（有记录的 code 反而返代码） | 立即回退（说明 PIT 查询口径接错） |

## 七、待裁项（M 已裁；余 2 项＝**②审时定**）

1. ~~**M 是否改判**~~ ⇒ **已裁（2026-11-04）**：改判成立，落 §四-3 接线硬门，且**不改 ISSSUE 已关闭条目**（L 同理）。
2. **I 的期货返回面**：维持 `{}`（当前）／改 `None`（与 `get_stock_name` 查不到返 `None` 语义一致）／抛 `NotImplementedError`（最显式但可能打断策略初始化）——**裁定时点＝②审（用户直读本件后，随实施批前裁）**。
3. **G 是否加保真开关** `QS_STOCK_NAME_PIT`（默认开、可关回代码）：成本 ＋1 env 分支，收益＝零风险回退——**裁定时点＝②审（同上）**。

## 八、变更日志

| 时间 | 内容 |
|---|---|
| 2026-11-04 00:2x | v1.1 两裁落地：M **改判照准**（静默失效面）＋新增 §四-3 **接线硬门**（PIT 双时态／fail-closed／显式声明静默面）；G 补强证据（provider 侧 `get_security_name*` 已存在）；期货返回面与保真开关转 **②审时定** |
| 2026-11-03 23:5x | v1.0 草稿：根因取证（`stock_namechange` 14,950 行／`industry_membership` 7,738 行）、推翻「无 name/无行业表」两条既有假设、给出 G/I 接线式修复与 M 改判建议、判型与回退 |
