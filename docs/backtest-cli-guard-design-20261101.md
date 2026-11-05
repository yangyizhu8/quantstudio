---
title: 方案 · 回测 CLI 防护（未知参数 fail-fast + 有效数据根与停服提示 + daemon 状态判据修正）
date: 2026-11-01
status: 六步流水线 ① 方案（RC-001a 落地草案，待 ② 审；未过审禁实施）
decision_ref: 总调度 2026-10-30/11-01 裁定——RC-001a 并入 11-04 框架项呈批、草案照准先做
knowledge_refill: [命中] QD-2026-0015（qd/D1/knowledge/qmt/QD-2026-0015.md，2026-10-30）——覆盖闸三型判据（本次在其前置链路上增 CLI 防护，属同族前置检查）
---

# 一、问题定义（两起实证事故）

| # | 事故 | 实测证据 |
| --- | --- | --- |
| 1 | `run_ptrade_strategy.py --help` 无此选项，**未知参数被静默忽略** ⇒ 直接按默认配置启动 76 交易日回测，并触发 `CollectorStopContext` **停掉主 daemon** | 2026-10-30 20:08:08 启动；`daemon.log` 20:08:10 `[DaemonLifecycle] 已清理本进程 status 文件` |
| 2 | daemon 状态判据不精确：`-m quantstudio.pipeline.daemon` 同时命中 `--mode forever`（常驻）与 `--mode once`（一次性任务） | 2026-11-01 14:33 我据 `-m ... daemon` 判定「RUNNING」，拉取 `CommandLine` 后实为 `--mode once --task mcp_stock_minutes` ⇒ 常驻 daemon 实为 NOT RUNNING |

**共性**：都是「**状态/参数判定不精确导致有副作用的动作被静默执行**」——参数非法不报错就叫副作用动作跑起来；进程身份不区分模式就报错状态。

# 二、改动范围与落点（策略产物零改动）

| # | 落点 | 改动 |
| --- | --- | --- |
| 1 | `quantstudio/backtest/run_ptrade_strategy.py` 主入口 | **未知参数 fail-fast**：解析前比对白名单，出现未知 `--xxx` 即以退出码非 0 拒绝并列出未知项（不进入任何前置动作） |
| 2 | 同上（进入 `CollectorStopContext` **之前**） | 打印一行：`[root] 有效数据根=<DATA_ROOT>`；若将停常驻 daemon 则追加 `将停主 daemon`，若数据根非默认主库目录则追加 `（副本根，不停主 daemon）` |
| 3 | daemon 状态判定处（运维脚本/本线回执判据） | 判据改为：命中 `-m quantstudio.pipeline.daemon`（或路径形态）**且** `--mode` ∈ {`forever`, 缺省}；`--mode once` 计为「任务进程」，不计入 daemon 状态 |

> 策略源码零改动（`output/generated_strategies/**`、`quantstudio/backtest/strategies/**` 不动）。

# 三、判据（三条）

| # | 判据 | 期望行为 |
| --- | --- | --- |
| ① | 任意未知参数（含 `--help` 若未实现） | **fail-fast**，退出码 2，stdout 明示未知项；**不得**进入前置校验/停采集/回测 |
| ② | 启动提示行 | 必打「有效数据根」；必打「是否将停常驻 daemon」，且与实际行为一致（副本根 ⇒ 不停主 daemon） |
| ③ | daemon 状态判据 | 仅 `--mode forever`/缺省 计入；`--mode once` 单独列出为任务进程 |

# 四、影响面与纯增益判定（铁律 18）

- 型别：**新增检测型**（新增能力开始报告真相：非法参数从「静默忽略」变「显式拒绝」）。
- 合法参数行为**完全不变**（白名单即现有参数集）；日线/分钟 profile 均零行为改变。
- 新增一行 stdout 提示：不影响结果产物（回测产物不含该行）。
- 黄金结果逐位一致：合法调用路径与现状完全相同 ⇒ 硬门为「同窗回测结果 sha256 一致」。

# 五、验收标准

| # | 项 | 标准 |
| --- | --- | --- |
| 1 | 未知参数 | `--help` / `--foo` 均 fail-fast（退出码非 0），且**未产生任何回测产物、未停采集**（以 `stop_request` 文件不存在为证） |
| 2 | 提示行 | 默认根与副本根两种场景各打一行，内容与实际停服行为一致 |
| 3 | daemon 判据 | 构造 `--mode once` / `--mode forever` 两例，判定分别正确 |
| 4 | 黄金一致 | 同窗回测结果 sha256 与改动前一致 |
| 5 | 回归 | 既有测试全绿 |

# 六、回退条件

- 未知参数 fail-fast 误伤既有合法参数（如拼写变体/别名）⇒ 立即补白名单；若无法穷举，降级为「未知参数 WARN + 3 秒倒计时可 Ctrl-C 中断」，保留可中断性。
- 提示行影响产物比对 ⇒ 改走 stderr（与 `[coexist]` 同级）。
- 验收不过 ⇒ 整体回退，不留半挂。

# 七、未决与风险

| # | 风险 | 处置 |
| --- | --- | --- |
| 1 | 白名单需与 **_parse_flag 调用点** 保持同步，防漂移 | **⚠ W1 修订（2026-11-03 实测，R344 核实）**：入口**无 argparse** —— `run_ptrade_strategy.py:543-560` 为手写 `_parse_flag(sys.argv[1:], '--xxx', default)`，**≥12 处散落调用**、无 parser 实例 ⇒ 原「argparse 反射」**不可行**。改为：**AST/正则扫描 `_parse_flag` 调用点自动生成白名单**（扫描器从代码提取，禁手写两份 ⇒ 单一事实源保持）|
| 2 | `--mode once` 任务与常驻 daemon 可能同时存在 | 判据分别列出，不混为一行 |
| 3 | 与 RC-001b（`DATA_ROOT` 模块级常量）耦合 | 本方案只读 `DATA_ROOT` 做提示，不改其求值时机；RC-001b 独立裁定 |

# 八、实施顺序（六步）

① 本方案（待 ② 审）→ ② 独立审核，未过禁实施 → ③ 实施 → ④ 验收（含黄金 sha256 一致）→ ⑤ 用户确认 → ⑥ 双仓推送。

> 呈批：并入 2026-11-04 框架项同轮（RC-001a 优先）。


---

## 修订 R1（2026-11-02，dev 核对案建议增补，总调度转发采纳）

### 新增：换库根形态验证用例（防御性覆盖）

> 背景：V4 换库根形态虽已在方案阶段排除，但**该形态的验证用例必须保留**——防止将来实现改动引入形态回归而无人察觉。

| # | 用例 | 输入 | 期望 |
| --- | --- | --- | --- |
| V-R1 | 默认数据根 | 不设 `QUANTSTUDIO_DATA_ROOT` | 提示行输出 `<默认 data 目录>`；若将停常驻 daemon 则标注「将停主 daemon」 |
| V-R2 | 副本数据根 | `QUANTSTUDIO_DATA_ROOT=<副本目录>` | 提示行输出**该副本绝对路径**；标注「（副本根，不停主 daemon）」或按实际行为如实标注 |
| V-R3 | 不存在的数据根 | `QUANTSTUDIO_DATA_ROOT=<不存在路径>` | **fail-fast 明确报错**（不得静默回退默认根）；退出码非 0 |
| V-R4 | 相对路径数据根 | `QUANTSTUDIO_DATA_ROOT=./tmp_copy` | 提示行输出**解析后的绝对路径**（非原始相对串） |
| V-R5 | 数据根切换一致性 | 同一进程先后两次以不同根启动 | 提示行与 `_paths.DATA_ROOT` 反映各自根；**不得串用**（`DATA_ROOT` 为模块级常量，须确认每次启动为新进程） |

### 验收标准增补

原 5 项验收扩展为 **6 项**：新增「**换库根形态 5 用例全过**（V-R1~V-R5）」，与既有 5 项并列作为硬门。

### 与合并判据的关系（交叉引用）

- V-R3/V-R4 直接依赖**数据根解析单源**（`quantstudio/_paths.get_data_root()`）⇒ 与「联接键归一 + 日键复用」合并判据同族（**口径必须单源**）。
- 若合并判据批 0 落地审计脚本，V-R3/V-R4 可作为其**集成用例**复用。

---

## 修订 R2（2026-11-05，W1 落件执行——总调度 R345/G1 照准）

### W1：入口解析机制实测（推翻「argparse 反射」）

| 项 | 实测 |
| --- | --- |
| 解析机制 | **手写** `_parse_flag(sys.argv[1:], '--xxx', default)`（`run_ptrade_strategy.py:543-560`） |
| argparse | **不存在**（grep `parse_known_args|parse_args(|add_argument` 零命中；再 grep `sys.argv|argparse` 定位到 `_parse_flag`） |
| 调用点数量 | **≥12 处**（`--match-price` / `--compare` / `--ptrade-dir` / `--output` / `--output-report` / `--slippage` / `--profile` / `--etf-t0` / `--cost-basis` / `--fidelity` / `--eps-basis` / `--no-coexistence` / `--coexistence-stop-timeout` / `--coexistence-boundary-timeout` …） |
| 结论 | ①无 parser 对象可反射 ②未知参数天然被静默忽略（= 事故 1 根因）⇒ **白名单须从调用点扫描生成** |

### 实施口径（W1 修订后）

| # | 项 | 内容 |
| --- | --- | --- |
| 1 | 白名单来源 | `ast` 解析 `run_ptrade_strategy.py`，收集 `_parse_flag(` 第 2 参数以 `--` 开头的字符串字面量 ⇒ 集合（只读生成，不落盘手写稿） |
| 2 | 校验时机 | `main()` 内、**任何前置动作之前**（含 `CollectorStopContext`）比对 `sys.argv` 中 `--` 开头的 token（跳过值位） |
| 3 | 兜底 | 扫描失败/零命中 ⇒ **不得放行**（fail-closed：报「白名单生成失败」退出码非 0），防扫描器失效后静默退化为无防护 |
| 4 | 测试 | 单测覆盖：已知参数全通过 / `--help`、`--foo` 拒绝 / 扫描器失效时 fail-closed |

### 验收增补（并入 §五 第 6 项）

- 白名单由扫描器生成的集合**逐项列出**，与 `_parse_flag` 调用点集合**逐位一致**（机检：两集合 diff 为空）。
