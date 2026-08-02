# CodeBuddy 任务书 · 第 1 轮：P0 基线冻结 + G0 上游可行性门禁 + P1A Source Registry 独立重构

> **本文件是给 CodeBuddy 的可直接执行任务书。** 严格遵守 `AGENTS.md` 三条铁律。
> 方案依据：`D:\miniQMT策略实盘\私募工作文件\QuantStudio-MCP全数据源替代实施方案-智能体执行版.md`（v1.1 Final），首轮授权范围 = §24.1 的 **P0 + P1A**。
> 本轮**不启动 P1B/P2/P3**，不连接任何真实 MCP，不修改四张价格表 authority。

---

## 0. 绝对禁止事项（违反任意一条立即停止并汇报）

1. **禁止 stage/commit/push/PR**。本轮所有工作只在本地完成，完成后按 §10 模板汇报，等待用户**修复后明确确认**才能同步。
2. **P1A 的实际代码 diff 中不得出现任何 `mcp` 字样**（文件名、变量、注释、配置均不可）。P1A 是与 MCP 完全无关的技术债清理。
3. **禁止修改 `BaseSourceAdapter` 基类**（`quantstudio/pipeline/sources/base.py`）。
4. **禁止修改四张价格表的 authority 锁定**：`daemon.py:426-432`（`MINUTE_AUTHORITY`）、`daemon.py:440-446`（`DAILY_AUTHORITY`）。本轮只读取它们做基线证据，不改动。
5. **禁止修改任何 Adapter 的函数签名、默认值、返回字段、返回类型、列顺序、index、dtype、空值行为、异常行为**。P1A 只重构"注册真相源"的收敛，不改 Adapter 行为。
6. **禁止在 P1A 顺便加入批迭代 `iter_table_batches`、MCP SDK、MCP 配置、MCP 命名**。
7. **禁止为通过测试自行放宽容差**。仅允许现有测试契约明确规定的浮点容差。
8. **P0/G0 探测只允许小体量/单日/样本工件**，写入 `output/mcp_migration/P0_baseline/` 独立目录，**不得写生产 Canonical 库**，不得在日志或探测工件中记录任何 token/密钥。
9. **G0 探测不得连接生产 MCP 写生产库**。本轮 G0 只是"可行性探测"，产出的是探测报告和结论，不是数据迁移。
10. 发现任何不在本轮范围的既有缺陷，**另立记录并在汇报中单列**，不得顺便修复。

---

## 1. 当前代码库真实起点（已核查，任务以此为基准）

- Git HEAD = `07c3817`，分支 = `main`，工作区有未跟踪文件：`.workbuddy/`、`bench_artifacts/_pf_full.err`、`docs/evidence/qfq_raw_admission_fullmarket_20260730/`、`docs/qfq-raw-admission-fullmarket-20260730.md`、`scripts/preflight_raw_fullmarket.py`。**P0 必须先冻结这些未跟踪证据**，不得覆盖或混入。
- 三套重复的 Adapter 注册真相源（P1A 要收敛的目标）：
  1. `quantstudio/pipeline/sources/__init__.py:18-27` —— `create_adapter()` 内部硬编码 `registry` 字典；
  2. `quantstudio/pipeline/source_capabilities.py:13-20` —— `ADAPTER_CLASSES` 字典 + `KNOWN_SOURCES`；
  3. `quantstudio/pipeline/config_lint.py:70` —— `_REGISTERED_ADAPTERS = {"tushare","baostock","akshare","xtquant","a_stock_data"}`。
- 消费者清单（P1A 重构后必须保持这些调用点可观察行为一致）：
  - `daemon.py:43` `from .sources import create_adapter`；`daemon.py:1598` `create_adapter(source, cfg)`；`daemon.py:1609` `_resolve_source_chain`（内部用 `adapter.supports_task`）；`daemon.py:2042`。
  - `config_lint.py:25` `from .source_capabilities import supports_task`；`:91` `_REGISTERED_ADAPTERS`；`:142/:163` `supports_task(...)`。
  - GUI：`gui/tabs/config_editor_tab.py:20,34,463`；`gui/tabs/task_tab.py:22,30,533`。
- `sources_config.json` 现状：8 个源声明，其中 `tushare/baostock/akshare/xtquant/a_stock_data` 5 个 `enabled=true`（均**有**注册 Adapter）；`joinquant/efinance/custom_api` 3 个 `enabled=false`（**无**注册 Adapter）。**P1A 重构必须保持这 3 个 disabled 未注册源的行为不变**（ConfigLint 第 91 行的放行语义：enabled=false 的未注册源不报错）。
- `collector_tasks.json`：19 个 task，含 `qfq_orchestrator` 块。P0 要导出这份配置的冻结副本。

---

## 2. 任务分阶段执行顺序

按下面 **阶段 A → B → C → D → E** 的顺序执行。每个阶段结束有一个**自检检查点**，但**不需要中途停下来等用户**——一口气做完五个阶段，最后按 §10 模板一次性汇报。检查点只是让你自查质量，不是审核中断点。

```
A. P0A 基线冻结（只读，不改生产代码）
B. P0B G0 上游可行性探测（只读，写 output/ 独立目录）
C. P1A-1 新建 Source Registry（不改现有调用点）
D. P1A-2 收敛三套真相源到 Registry（关键风险阶段）
E. P1A-3 等价性回归 + 文档评估
```

---

## 3. 阶段 A：P0A 基线冻结（只读）

### A1. Git 与工作区状态冻结
- 在 `output/mcp_migration/P0_baseline/git_state.txt` 记录：分支、HEAD SHA、`git status --short` 全量输出、`git log -1` 、未跟踪文件清单（含上述 QFQ 全市场证据目录）。
- **不要 git add / commit / stash 任何东西**。只读取记录。

### A2. 配置与代码基线冻结
- 复制以下配置到 `output/mcp_migration/P0_baseline/` 作为冻结快照（用复制，不改原件）：`config/data_config.json`、`config/sources_config.json`、`config/collector_tasks.json`、`config/alignment_rules.json`。命名加 `.P0_frozen` 后缀。
- 运行 `python -m quantstudio.pipeline.config_lint`（或等价入口），把完整 stdout/stderr 存到 `output/mcp_migration/P0_baseline/config_lint.json`（用 `--json` 或手工结构化，至少含 errors/warnings 两个列表）。

### A3. 价格表 authority 证据固化
- 在 `output/mcp_migration/P0_baseline/price_authority_evidence.md` 记录：
  - `daemon.py:426-432` 的 `MINUTE_AUTHORITY`（`stock_minutes/etf_minutes -> xtquant`）原文 + 行号 + 守卫返回 False 的逻辑；
  - `daemon.py:440-446` 的 `DAILY_AUTHORITY`（`stock_daily/etf_daily -> xtquant`）原文 + 行号；
  - `daemon.py:448-462` 的通用 `authoritative_source` / `allow_fallback` 守卫逻辑；
  - 这是**行为契约基线**，P1A 重构不得改变这些守卫的触发条件和返回值。
- 证据文件必须标注"本轮只读记录，未改动"。

### A4. 表/水位/来源库存导出
- 对生产 DuckDB（通过现有连接方式，不要新建连接方式）执行只读查询，导出到 `output/mcp_migration/P0_baseline/`：
  - `table_inventory.csv`：每张表的行数、最早/最晚业务时间（按表主键时间字段）；
  - `watermark_inventory.csv`：`source_watermark`（或等价水位表）全部内容，含 server/source/table/freq/last_date/status；
  - `source_inventory.csv`：各表实际写入的来源分布（`SELECT table, source, count(*) GROUP BY`，按实际 schema 调整列名）。
- 查询必须是**只读 SELECT**，禁止任何 INSERT/UPDATE/DELETE/CREATE/ALTER/DROP。若现有代码无现成只读查询入口，写一次性 `scripts/p0_baseline_inventory.py`（放 scripts/ 下，但**不**纳入本轮提交范围——在汇报里标注它是探测脚本）。
- 若数据库连接失败或某表不存在，如实记录失败原因，不要伪造数据。

### A5. QFQ 基线冻结
- 在 `output/mcp_migration/P0_baseline/qfq_baseline/` 记录：
  - 当前 QFQ 相关代码文件 SHA（`git ls-files | grep -i qfq | xargs sha256sum` 或等价）；
  - `collector_tasks.json` 中 `qfq_orchestrator` 块的冻结副本；
  - raw/front/back 三套复权口径的摘要统计（从库中按代表性样本代码抽样，如 `000001.SZ`、`600519.SH` 全区间，导出每个口径的行数、min/max/mean close、首末日），存 `qfq_summary_sample.csv`；
  - anchor/reanchor 状态（若库中有相关表或字段，如实记录；没有则记录"无独立 anchor 表"）。
- **不要重新跑 QFQ 全量重建**——只读快照。若用户已有 `docs/qfq-raw-admission-fullmarket-20260730.md` 等证据，引用并标注，不要重复计算。

### A6. 代表性策略黄金结果固化
- 选择至少 2 个代表性策略（建议从 `quantstudio/backtest/strategies/` 现有策略中选一个股票日线策略 + 一个 ETF 轮动/估值策略；分钟策略若存在也加一个），在**当前代码、当前数据**下跑一次回测，把关键指标存到 `output/mcp_migration/P0_baseline/strategy_golden/`：
  - 每策略一个 `<strategy_name>.json`，含：策略名、代码 SHA、回测区间、起止现金、最终净值、年化收益、最大回撤、交易次数、拒单次数、每日净值序列的 SHA256（用于后续 P6-A 逐项对比的快速指纹）。
- 如果现有策略跑不起来或耗时过长，**如实记录失败原因并降级**为更小的代表性区间，不要为了凑数而放宽或简化策略逻辑。

### A7. 分钟覆盖分布统计（方案 §2.4 / D2 要求）
- 对 `stock_minutes` 和 `etf_minutes` 统计覆盖分布，导出 `minute_coverage_distribution.csv`，至少含：
  - 每张分钟表的全表最早日期、最晚日期、总行数、distinct code 数；
  - 每证券的最早日期分布（distinct code × min(date)），用于判断"完整覆盖起点 vs 部分覆盖起点 vs 缺口区间"；
  - 每日证券数分布（按日 distinct code 计数的 min/p25/p50/p75/max + 全市场证券总数参照），用于判断哪些日子是"全市场完整"、哪些是"部分覆盖"。
- 这是为了给出方案 D2 要求的"完整覆盖起点 / 部分覆盖起点 / 缺口区间"边界，**禁止用日线或 5min 伪造 1min**。

### A8. 生成 mcp_dataset_requirements.json
- 在 `config/mcp_dataset_requirements.json` 生成方案 §4 的覆盖矩阵（机器可读），每个数据域含：canonical_table、minimum_requirement、priority（P0/P1/P2）、current_status（已有/缺失/部分）。
- 这是**新增配置文件**，不覆盖现有配置。

---

## 4. 阶段 B：P0B G0 上游可行性探测（只读，写独立目录）

> **重要**：本轮 G0 是"探测"，不是"接入"。你不需要真的连上一个 MCP Server——如果当前没有可用的候选上游，G0 的职责是**如实记录"无可探测候选"并给出 NO_GO 倾向**，而不是伪造探测结果。

### B1. 候选路径清单
- 在 `output/mcp_migration/P0_baseline/upstream_candidates.json` 记录你**实际可访问**的候选路径（如实，不要编造）：
  - 第三方现成 MCP Server（如果你能访问到 endpoint，记录其 id/transport/声称的 datasets；访问不到就记"无可用 endpoint"）；
  - 可由自托管 Gateway 封装的 API/公开文件/数据库（如 AkShare/Tushare 的裸 API，如实记录它们是否构成"独立上游"还是仍是 xtquant/Tushare 薄包装）；
  - 可周期获取的离线/增量数据包（如有）。
- 若三类候选**都不可用**，如实记录每类的不可用原因。

### B2. 五项最低能力探测（如可探测）
方案 §P0B 要求探测五项。**仅当你能访问到至少一个候选上游时才执行真实探测**；否则在 `upstream_feasibility_probe.md` 如实写明"本轮无可用候选上游，无法完成实测探测"，并把每项探测标记为 `NOT_PROBED` + 不可用原因。
- 五项：①全市场证券列表+资产类型 ②单日全市场日线批量 ③单交易日全市场/大批量 1min 导出能力（验证是否只能严重限流逐只查）④已知公司行为样本（公告日/登记日/除权日/比例是否足以重建复权）⑤上游血缘+批量许可+本地持久化许可。
- 真实探测时：只拉小体量/单日/样本，写入 `output/mcp_migration/P0_baseline/` 临时子目录，**不写生产库**，不记录密钥。

### B3. G0 结论
- 在 `output/mcp_migration/P0_baseline/baseline_report.md` 给出 G0 三选一结论：`GO / CONDITIONAL_GO / NO_GO`，并附判定依据：
  - `GO`：至少一条关键上游路径具备现实可行性（独立、可批量、可持久化、覆盖分钟）；
  - `CONDITIONAL_GO`：部分域可行但分钟/公司行为/许可仍有阻断；
  - `NO_GO`：不存在独立可批量可持久化的现实路径。
- **不要为了让项目"继续推进"而抬高结论**。如实是关键——铁律 1 明确 G0=NO_GO 时 P1A 仍可完成。

---

## 5. 阶段 C：P1A-1 新建 Source Registry（不改现有调用点）

### C1. 新建 `quantstudio/pipeline/sources/registry.py`
- 实现**唯一**的 Adapter 注册中心，职责（方案 §6.1）：
  - 唯一注册 Adapter class（`register(name, cls)` / 装饰器或模块级 `_REGISTRY` dict）；
  - `create_adapter(source, config) -> BaseSourceAdapter`（行为与现有 `sources/__init__.py::create_adapter` **逐字节等价**，包括 `ValueError` 消息格式）；
  - `known_sources() -> tuple[str,...]`（返回顺序与现有 `KNOWN_SOURCES` 一致：`tushare, baostock, akshare, xtquant, a_stock_data`）；
  - `adapter_class(name) -> type[BaseSourceAdapter] | None`；
  - 提供 capability discovery 入口（`capability_matrix()` / `supported_sources(table)` / `supports_task(source, table, freq)`），行为与现有 `source_capabilities.py` **等价**。
- **约束**：Registry 只认识 Adapter 注册契约，**不认识 MCP**（无任何 mcp 字样、不预留 mcp 字段、不引入 MCP SDK）。Registry 不修改 `BaseSourceAdapter`。

### C2. 此阶段**不**改动现有三套真相源
- C 阶段只**新增** `registry.py`，`sources/__init__.py`、`source_capabilities.py`、`config_lint.py` 暂不动。
- C 阶段为 Registry 写单元测试 `tests/test_source_registry.py`（方案 §17.1），覆盖：
  - 5 个已注册源的 `create_adapter` 返回正确类型；
  - 未知源抛 `ValueError` 且消息与现有格式一致；
  - `known_sources()` 顺序一致；
  - `capability_matrix()` 与现有 `source_capabilities.capability_matrix()` 在所有 `KNOWN_TABLE_FREQS` 上**逐表逐源结果一致**（用 `assert matrix_new == matrix_old`）；
  - `supports_task` 返回 `(bool, str)` 与现有实现一致。

---

## 6. 阶段 D：P1A-2 收敛三套真相源到 Registry（关键风险阶段）

> **这是本轮风险最高阶段**。必须用等价性测试兜底。建议在改每一处之前，先跑一遍 §E 的回归基线（基于现有代码），记录"改前"快照，改完再跑"改后"对比。

### D1. `sources/__init__.py`
- 让 `create_adapter` 委托给 `registry.create_adapter`（或直接 re-export），保持对外签名、异常消息、`__all__` 完全不变。
- 保留对 5 个 Adapter class 的 import 和 `__all__` 导出（GUI、测试可能直接 import 这些 class）。

### D2. `source_capabilities.py`
- `ADAPTER_CLASSES` / `KNOWN_SOURCES` / `capability_matrix` / `supported_sources` / `supports_task` 改为从 Registry 取真相源。
- **保持所有公开符号名和返回结构不变**（GUI 在用 `capability_matrix()` 返回的 dict 结构）。
- 若为兼容现有 import（如其他模块 `from .source_capabilities import ADAPTER_CLASSES`），保留这些符号但让它们指向 Registry 的真相源，行为等价。

### D3. `config_lint.py`
- `_REGISTERED_ADAPTERS` 改为从 Registry 动态取（`set(registry.known_sources())`），行为等价。
- 第 91 行 `enabled_sources - _REGISTERED_ADAPTERS` 的语义必须保持：**enabled=false 的未注册源（joinquant/efinance/custom_api）不报错；只有 enabled=true 但未注册才报错**。重构后用测试验证这条边界。

### D4. GUI 调用点
- `gui/tabs/config_editor_tab.py` 和 `gui/tabs/task_tab.py` 若直接依赖 `source_capabilities` 的符号，**优先保持它们的 import 不变**（通过 D2 的兼容层）；只有当 import 必须改时才改，且改后行为等价。
- GUI 不在本轮写新测试，但必须确认 GUI 模块能 import 成功（`python -c "import quantstudio.gui.tabs.config_editor_tab"` 不报错）。

### D5. daemon.py
- `daemon.py:43` 的 `from .sources import create_adapter` 和 `:1598` 的 `create_adapter(source, cfg)` —— **如果 import 路径不变就不用改 daemon**。本轮目标是"收敛真相源"，不是"改 daemon 的 import 风格"。只有当 D1 让 `sources/__init__.py` 不再导出 `create_adapter` 时才需要动 daemon，而 D1 要求保持导出，所以 daemon 应当**无需改动**。
- 在汇报里明确说明 daemon 是否改动（应为"未改动"）。

---

## 7. 阶段 E：P1A-3 等价性回归 + 文档评估

### E1. 等价性回归测试（铁律 2 的验收）
- 跑全量 pipeline 相关测试：`pytest tests/ -k "source or adapter or config_lint or capability or daemon or authoritative or backtest_data_source or config_source_registration" -v`（按实际 test 命名调整，目标是覆盖所有消费注册表的路径）。
- **必须全绿**。任何红的测试：如果是因重构引入的行为差异 → 修复重构（不是改测试）；如果是测试本身硬编码了旧内部结构 → 单列说明，但不得擅自放宽断言。
- 重点验证这几个已有测试（从 tests/ 列表里识别）必须继续通过：`test_config_source_registration.py`、`test_capability_model.py`、`test_authoritative_source_policy.py`、`test_backtest_data_source_priority.py`、`test_config_lint*`（若有）。

### E2. 可观察行为等价性快照对比
- 在 D 阶段改动前，跑一次并保存"改前"基线：`capability_matrix()` 的完整 dict、`known_sources()`、5 个源的 `supports_task` 在所有 `KNOWN_TABLE_FREQS` 上的结果矩阵。
- D 阶段改动后，跑"改后"快照，用 `assert改后 == 改前` 逐项对比，存到 `output/mcp_migration/P0_baseline/p1a_equivalence_before_after.json`。
- 这是铁律 2 要求的"可观察语义精确一致"证据。

### E3. ConfigLint 边界专项测试
- 新增或补充测试，明确覆盖：
  - enabled=true 未注册源 → ERROR；
  - enabled=false 未注册源（如 joinquant）→ 不报错（行为保持）；
  - 5 个 enabled=true 已注册源 → 正常通过。

### E4. 文档评估（只评估，本轮范围外的文档改动单列）
- 评估这些文档是否需要因 P1A 改动：`README.md`、`docs/data-pipeline-contract.md`、`docs/data-quality-checks.md`、`docs/strategy_toolbox.md`、`docs/prompt_engineering.md`。
- P1A 是内部重构，**理想情况下对外文档无需改动**（因为可观察行为不变）。但如果某文档明确描述了"三套注册表"的内部结构，则在汇报里指出需要更新，并在用户确认后同步（属于铁律 1 的文档同步要求）。

---

## 8. 验收标准（全部满足才算本轮完成）

1. P0A 全部产物（§3 A1-A8）生成在 `output/mcp_migration/P0_baseline/`，且不含伪造数据（连接失败如实记录）。
2. G0 结论给出（GO/CONDITIONAL_GO/NO_GO），依据如实。
3. `quantstudio/pipeline/sources/registry.py` 新建，无任何 `mcp` 字样（用 `grep -ri mcp quantstudio/pipeline/sources/registry.py` 必须无输出）。
4. 三套真相源收敛到 Registry，且 `grep -rn "_REGISTERED_ADAPTERS\|ADAPTER_CLASSES\b" quantstudio/` 在改动后，硬编码字面量应只出现在 Registry 或被 Registry 取代（如有兼容保留，注明）。
5. **P1A 全部代码 diff 的 `git diff` 输出中 `grep -i mcp` 无任何命中**（包括注释）。
6. `BaseSourceAdapter` 基类零改动（`git diff quantstudio/pipeline/sources/base.py` 为空）。
7. 四张价格表 authority 守卫零改动（`git diff quantstudio/pipeline/daemon.py` 中 `MINUTE_AUTHORITY`/`DAILY_AUTHORITY` 段为空，理想情况 daemon.py 整体未改）。
8. 等价性回归测试全绿。
9. 改前/改后可观察行为快照逐项一致（`p1a_equivalence_before_after.json`）。
10. 未执行任何 stage/commit/push/PR（`git status` 显示改动仍在工作区）。

---

## 9. 不要做的事（边界澄清）

- 不要生成 P1B 的任何文件（`quantstudio/pipeline/mcp/` 目录本轮**不创建**）。
- 不要生成 `config/mcp_servers.json`、`config/profiles/mcp_only.json`（这些是 P1B/P9 范围）。
- 不要实现 `iter_table_batches` / `RawBatch`（P3 范围）。
- 不要实现 `granularity.py`（P2 范围，且 P1A diff 不能有 mcp，颗粒度对齐也非本轮）。
- 不要改 `alignment_rules.json` 的 schema 定义（P1A 不动数据契约）。
- 不要"顺便"修复 §1 中提到的既有缺陷（balance_statement 水位 warning、pending_repair 等）——只记录。
- 不要删除 `joinquant/efinance/custom_api` 三个 disabled 源声明。

---

## 10. 完成后汇报模板（按此结构汇报，不要 stage/commit）

```markdown
## 阶段
P0（基线冻结 + G0）+ P1A（Source Registry 独立重构）

## 本地代码变更
- 新增文件：registry.py、test_source_registry.py、p0_baseline_inventory.py（探测脚本，未纳入提交范围）、mcp_dataset_requirements.json
- 修改文件：sources/__init__.py、source_capabilities.py、config_lint.py、（GUI? daemon? 如实说明）
- 行为变化：无（P1A 为等价重构）/ 如有则单列
- API/Schema/生命周期变化：无

## P0 产物清单（output/mcp_migration/P0_baseline/）
- 列出所有生成的文件 + 每个文件的关键摘要（行数/结论）

## G0 结论
- GO / CONDITIONAL_GO / NO_GO
- 判定依据：（逐条对应五项探测 + 候选路径可得性）

## 数据与迁移影响
- 表：未改动（P0 只读）
- 水位：未改动
- 数据源：注册表收敛，5 个已注册源行为等价；3 个 disabled 未注册源行为保持
- 是否需要重建：否

## 测试证据
- 等价性回归命令：...
- 结果：X passed, Y failed（必须全绿）
- 改前/改后快照对比：逐项一致 / 差异点：...
- ConfigLint 边界测试：enabled=true未注册→ERROR ✓；enabled=false未注册→放行 ✓

## 文档评估
- 需更新：...（P1A 理想情况无）
- 无需更新：...

## 已知风险与既有缺陷（未修复，单列）
- balance_statement 水位 warning：...
- pending_repair：...
- 其他：...

## 回退方式
- registry.py 等价委托，回退只需 git checkout 改动的 3-4 个文件

## 待用户确认的 Git 同步范围（未执行）
- 代码：registry.py、sources/__init__.py、source_capabilities.py、config_lint.py、test_source_registry.py
- 配置：mcp_dataset_requirements.json
- 文档：...
- P0 产物（output/）是否纳入提交：待用户决定

## 等待确认
本地工作已完成，尚未 stage/commit/push/PR，请用户明确确认是否同步。
```
