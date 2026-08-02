# MCP 采集任务扩展 — 表映射探测与配置（修复3）

> 状态：**探测脚手架已就绪，待真实环境探测回填**。
> 本文件记录探测方案、候选映射模板与 gap 记录。真实云端表名以
> `scripts/mcp_probe_tables.py` 的探测结果（`docs/mcp_migration/probe_result.json`）为准。

## 背景

MCP 权威源当前支持的表见 `quantstudio/pipeline/sources/mcp_adapter.py` 的
`_MCP_SUPPORTED` / `_CANONICAL_TO_QUESTDB`。为扩展估值/指数成分/行业/股本变动等
8 张新表，需先探测云端 QuestDB 真实源表名（MCP server 源表名可能与 QuantStudio
canonical 名不同），再回填配置。

## 步骤1：探测（先做）

运行探测脚本（需先配置 MCP API Key，见「采集任务」Tab 顶部下拉切 MCP 模式 →
「数据源」Tab MCP 卡片填写 Key → 保存写 `config/secrets.env`）：

```bash
python scripts/mcp_probe_tables.py
```

脚本对以下 8 张候选表逐一 `query_snapshot(limit=1)`，先试 canonical 名、再试候选名：

| canonical 表名            | 候选 QuestDB 源表名 | 探测预期            |
|---------------------------|---------------------|---------------------|
| stock_daily_valuation     | stock_daily_basic   | 可能叫 stock_daily_basic |
| index_constituents        | index_weight        | 可能叫 index_weight |
| sw_industry               | sw_daily            | 可能叫 sw_daily     |
| industry_classification   | sw_classify         | 可能叫 sw_classify  |
| industry_membership       | sw_weight / ths_member | 可能叫 sw_weight 或 ths_member |
| stock_float_share         | stock_float_share   | 可能不存在          |
| stock_namechange          | stock_namechange    | 可能不存在          |
| stock_delist              | stock_delist        | 可能不存在          |

探测结果写入 `docs/mcp_migration/probe_result.json`，状态分 `EXISTS` / `MISSING`。

## 步骤2：配置（探测后做，对 EXISTS 的表）

对探测到存在的表，依次：

1. `mcp_adapter.py` 的 `_CANONICAL_TO_QUESTDB` 加 canonical→真实 questdb 映射；
2. `_MCP_SUPPORTED` 加 `(table, "daily")` 条目；
3. `config/profiles/mcp_only/collector_tasks.json` 加对应 task；
4. `config/profiles/mcp_only/alignment_rules.json` 加 mcp 映射（单位/PIT/列名）；
5. 通过 `ConfigLint` 校验。

云端不存在的表记录为 **gap**，不进配置。

## 步骤3：验收

每张新表端到端 smoke：取数1行 → align → validate → write → 无报错。
首次全量回填预期：无 mcp 水位行，首轮从 `start_date` 全量拉。

## gap 记录模板（探测后填）

```
- 云端不存在的表：<canonical 列表>
- 原因：<如云端未提供该数据集>
- 处置：<不进配置 / 走传统源替代>
```

## 当前进展

- [x] 探测脚本 `scripts/mcp_probe_tables.py` 已就绪
- [x] 本探测/配置文档框架已就绪
- [ ] 真实环境探测（需 MCP_API_KEY + 网络）
- [ ] 探测结果回填 4 处配置（步骤2）
- [ ] 端到端 smoke 验收（步骤3）
