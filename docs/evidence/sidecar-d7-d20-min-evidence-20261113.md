# sidecar · D7 消费矩阵 / D20 平台能力 · 最小证据（2026-11-13 · 节点21）

> R25 指示：集中做 D7＋D20 最小证据，不再扩规格；本域先查源码/平台文档、设计隔离探针；
> **生产 DDL、强杀、掉电实验不执行**。

## 一、D7：delivery 侧（`*_front` / `*_back`）**实际消费者已在本仓定位**（推翻我此前「属他域」的判断）

此前我称「delivery 侧消费者在主仓交付链、本线不起结论」——**错误**：本会话工作区即为 QS 主仓，可直接取证。

| # | 消费者（生产路径） | 行号 | 读取形态 | 性质 |
|---|---|---|---|---|
| 1 | `quantstudio/backtest/providers/duckdb_data_access.py` | **L762** | `SELECT ... open_front, high_front, low_front ...` | **回测取数**（真实消费） |
| 2 | 同上 | **L653** | 「`open_front AS open` 等别名在 SELECT 内**就地替换列值**」 | **对策略透明地喂 qfq 价** |
| 3 | 同上 | **L120** | 注释：信号计算 `get_history(fq='pre')` 走 `query_bars_by_range`，**查 `*_front` 列** | 信号层消费 |
| 4 | 同上 | **L1424 / L1456** | `... low_back, close_back FROM {table}` | 后复权列亦被读 |
| 5 | `quantstudio/pipeline/quality_audit.py` | **L630 / L654-661** | `SELECT close, close_front, close_back ...`、`prev_front>0` 校验 | 审计消费 |
| 6 | `quantstudio/pipeline/qfq_reanchor_engine.py` | **L1744-1746** | `t.open_front IS DISTINCT FROM s.open_front` | 重锚自校验 |

**结论（可支撑判据 12 必过）**：delivery 面 `*_front` 被 **回测数据访问层以列别名方式真实消费**，
策略侧看到的就是 qfq 价 ⇒ delivery 锚（`adj_latest`/`adj_earliest` 全局快照）**是活锚，不是死字段**。
⇒ 判据 12 维持必过；且**对拍对象**应为「回测层实际读到的 front 值」而非表内原值（别名替换后取值）。

**仍未闭合**：AQ-DW 侧读路径（是否读镜像）——确属他域，须 ⑱ 举证。

## 二、D20：平台能力（本轮只到最小证据，未完成文档取证）

| 项 | 实测 | 说明 |
|---|---|---|
| 本地 duckdb | **1.5.6** | `python -c "import duckdb"` 实测 |
| QuestDB 部署位置 | **不在本机** | 全盘定向探测：`D:\miniQMT策略实盘` 下仅 `regime_adaptive_qdb`（另一项目）；无 questdb 配置/属性文件 |
| QuestDB 接入方式 | **远端 MCP** `https://124.223.159.234/mcp` | `sources_config.json`；⇒ 本地**无法**对其做 L3 持久性探针 |
| QuestDB 官方文档 | **未取到** | 按铁律 16 如实声明：**未取到对应文档，以下判断基于配置与代码事实**，不作平台契约断言 |

**最小结论**：
1. **QuestDB 侧 L3 无法在本机取证**（远端、不可执行 DDL/掉电实验）⇒ 「已确认的平台持久化契约」**仍缺**，
   但与 R25 的收敛定义一致：**不需要穷尽掉电场景**，只需拿到「WAL commit 持久性语义 + 可观测确认点」的**官方契约文本**——这属**文档取证任务**，下一轮以 Context7/官方文档完成，不需要平台权限。
2. **DuckDB 侧（主库 `data/quantstudio.db`）可做隔离探针**（仅设计、不执行）：临时目录新建库 → 建表 → 写入 →
   `CHECKPOINT` → 复查；**不做**强杀/掉电。用于确认「发布顺序 + checkpoint 调用」即可满足本合同要求的 L3，而非证明掉电安全。
3. **D20 不等于「无权限所以不能推进」**——可推进面＝文档契约取证 + DuckDB 隔离探针；不可推进面＝远端 QuestDB 实测。

## 三、探针设计（不执行）

| 编号 | 目标 | 形态 | 状态 |
|---|---|---|---|
| P-1 | DuckDB checkpoint 后回读一致（隔离目录） | 临时库 + 单进程 | **设计完成，未执行** |
| P-2 | 两表分两次 commit 的可见性窗口（证明窗口存在） | 临时库 + 两个连接 | **设计完成，未执行** |
| P-3 | QuestDB WAL commit 契约 | **文档取证**（非执行） | **未开始** |
