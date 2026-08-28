# 数据源唯一化收敛·实施与验收证据（2026-08-28）

- 流水线：Step 1 计划（审计通过 + 三约束并入）→ Step 3 实施（Phase A→D）→ **Step 4 验收（复核通过 + 全库扫尾并入）**
- 回退点：`295714a71e974a9b0d928ea71e00d1d2e8fb6da7`

## 0. Step 4 复核 + 全库扫尾（总调度，2026-08-28）

- **复核通过**：create_adapter 五非 MCP 源全 BLOCKED + mcp OK（实测）；grep 四词残余 38 处多为注释/registry 声明，非可达路径；
- **全库扫尾 17 处**（总调度补）：tests 7 文件 + quantstudio 6 文件（含 `_paths.py` 权威路径模块）+ scripts 3 文件的 legacy config 路径 → mcp_only——**归入本 commit 同批推送**（同目标同主题）；
- **WP-D 全量回归终态**：9 failed 全归因——5 已修（扫尾迁移 4 文件路径）+1 已修（顺带 legacy 路径）+3 类预存在存量（①chokepoint 双写入点：**d960d33 8/3 引入**，早于本周期 18 天，与本改动零关联，登记技术债 #16 族统一入口纪律；②R5.5 robustness ledger：agent 线流程件；③fidelity 存量契约滞后：已登记）；10 errors（industry_migration_tool 同款 legacy 路径）已随扫尾修复。

## 1. 实施清单（Phase A→C）

| Phase | 文件 | 改动 |
|---|---|---|
| A GUI | `source_tab.py` | source_defs 9 卡→**单 MCP 卡**；cred_field_map 两处死分支清理（回填/保存） |
| B 配置 | `config/profiles/mcp_only/sources_config.json` | 删 5 非 MCP 段；mcp 段 + **`upstream_authority: "mcp"` 权威声明**（机器可校验） |
| B 配置 | `config/secrets.env.example` | 只留 MCP_API_KEY + ALERT_WEBHOOK（TUSHARE/JQ/QMT_PATH/CUSTOM_API_KEY 废止） |
| C 管线 | `sources/__init__.py` | registry 只注册 mcp（5 adapter 留盘停用；非 mcp raise 含恢复运维指引） |
| C 管线 | `daemon.py` L318- | QFQ 因子刷新 **MCP 短路**（对拍定谳：因子随行情常态注入，refresher 冗余；非 degraded） |
| C 管线 | `daemon.py` 守卫 | `_declared_upstream`→恒 "mcp"；MINUTE/DAILY_UPSTREAM={mcp}；**守卫文案同步**（xtquant/tushare→mcp，2026-07-21 契约废止留痕） |
| C 管线 | `daemon.py` L573 区 | tushare/per_stock 死分支加"唯一化后不可达"标注（留盘文档价值） |
| C 管线 | `config_lint.py` 自检 | 根 config → mcp_only profile（归档后 lint 适配） |
| GUI 同批 | `config_editor_tab.py` | DEFAULT_SOURCE_MAP **全表 20 项 → mcp**（含 L539 fallback "tushare"→"mcp"） |
| 测试 | `test_minute_source_guard.py` | xtquant 语义 → mcp 唯一化语义（拒绝=工厂 RuntimeError；GUI map/collector_tasks 断言同步） |
| 测试 | `test_authoritative_source_policy.py` | 根 config → mcp_only 路径；旧 tushare 权威声明契约废止断言 |

## 2. 三约束落实

| 约束 | 落实 |
|---|---|
| ①C2 前置对拍 | 探针定谳：`fetch_adj_factor` 深度绑定 tushare 协议（`_client.adj_factor/fund_adj`+ts_code），MCPAdapter 无同构接口——**不可直接换 adapter**；但 MCP `_QFQ_ADJFACTOR_TABLES` 覆盖全部行情表 → 因子随行情常态注入 qfq_aux.db → **refresher 在 MCP 下架构性冗余** → 落地为"MCP 短路返回 False（非 degraded）"，非臆测换源 |
| ②C3 回退预案 | 设计+代码注释双落盘：守卫意外拒写 → 回退=恢复旧声明（git 历史）+ 登记，不现场调参 |
| ③#19/#20 联动 | sources_config `upstream_authority: "mcp"` 权威声明写入（机器可校验）；"非 MCP 源复活"检测 = 工厂 raise + 守卫拒绝（双层），规则库登记见 §4 |

## 3. 验收结果（四项 grep 证据 + 回归 + 冒烟 + 对拍）

| 项 | 结果 |
|---|---|
| **运行时可达性终验**（权威判据=create_adapter 行为） | ✅ tushare/baostock/akshare/xtquant/a_stock_data 全 **BLOCKED**（ValueError）；**mcp OK** |
| 回归 | ✅ 数据源域 **114 passed + 1 skipped**（含迁移后 minute_source_guard/authoritative_policy；两处 daemon_lifecycle/once_exit 失败=他线域排除归因） |
| 冒烟（GUI 单卡） | ✅ source_defs 单卡断言（source_tab 渲染只产 mcp 卡） |
| 对拍（约束①） | ✅ 代码级定谳（fetch_adj_factor tushare 绑定 vs MCP 常态注入架构）——非运行时对拍（原因：MCP 因子无独立 API，架构性吸收） |

## 4. "非 MCP 源复活"检测（约束③规则库登记）

- 工厂层：`create_adapter(非 mcp)` → ValueError（含恢复运维指引）；
- 守卫层：MINUTE/DAILY_UPSTREAM={mcp}，declared_upstream 恒 mcp——任何非 mcp 写者被拒并留痕；
- 配置层：sources_config 仅 mcp 段 + upstream_authority=mcp——ConfigLint 可校验（后续可加"sources 段含非 mcp 键即 ERR"规则）。

## 5. 回退

- 回退点 `295714a`；逐文件定向 restore（source_tab/sources init/daemon/config_lint/config_editor_tab/两配置/两测试）。