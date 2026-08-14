# WP7-E3 C-4 完成后的 approve 审计清单模板

> 本文件是 C-4 bootstrap-run 完成后的操作清单（模板）。
> 实际 failed/dead_letter 清单在 C-4 完成后从主库导出填充。

## 1. 背景

C-4 bootstrap-run 重跑（Bug 1+2 修复后），run_id: `bs_07b91d6bea`。
修复内容：
- Bug 1: `_filter_date_window` 分钟列裁剪失效（只认 date/trade_date，分钟表跳过裁剪 → extra 行）
- Bug 2: 网格化窗口 row_limit（5M）截断 → 动态窗口缩小（10→2 天）

预期：大量 STOCK 由 blocked → completed；3 个 failed ETF 由 A+ 批准机制处理。

## 1.1 测试盲区记录（跟进②）

服务端截断场景（Bug 2 的触发条件）在 staging 测试**无法覆盖**，原因如下：

1. **row_limit 截断只在大表全市场导出时触发**：
   - 服务端 `export_dataset` row_limit=5M（分钟表），全市场 10 天分钟数据 ~1200 万行 → 截断。
   - staging 测试窗口小（单证券/少量证券），行数远低于 5M，永远不会截断 → 盲区。
2. **验证手段**：只能通过生产全市场 bootstrap 的 missing 行检测（C-4 重跑前 blocked 清单）反推截断。
   - 修复后 `safe_window = max(1, int(5_000_000 / (daily_rows * 1.2)))`（stock_minutes → 2 天窗口），
     使每个 export 批次行数 < 5M，避免服务端截断。
3. **回归验证**：C-4 重跑后若 blocked/missing 归零 → 修复有效；若仍有 → 需重新审视
   `_EXPORT_ROW_ESTIMATE` 的估算是否偏小（daily_rows 低估 → safe_window 偏大 → 仍截断）。
4. **残留风险**：窗口缩小（10→2 天）增加网格批次数（10 个 grid batch），拉长全量缓存重建期；
   这是 C-4 速率偏慢（缓存重建期 <65/h）的直接原因之一。速率裁决后若需 T-B2 并行化，
   只并行"读缓存+写库"阶段，不并行 export 拉取（避免服务端连接数超限）。

## 2. 完成检查（C-4 结束后执行）

```bash
# 检查 run 状态
python -m quantstudio.pipeline.qfq_orchestrator_cli --db data/quantstudio.db \
  --aux-db data/qfq_aux_mcp_gen1.db --config-dir config/profiles/mcp_only \
  --override source_generation=mcp-gen1 --override cutover_id=b6_formal_20260807_v2 \
  --override generation_mode=dynamic bootstrap-status  # 若有此命令
```

## 3. 导出 failed / dead_letter 清单

```sql
-- 从主库导出（C-4 结束后锁释放）
SELECT code, asset_type, status, error_msg, finished_at
FROM qfq_bootstrap_item
WHERE run_id = 'bs_07b91d6bea' AND status IN ('failed', 'dead_letter')
ORDER BY asset_type, code;
```

## 4. 分类处理

### 4.1 failed ETF（预计 3 只：Tushare daily vs minute API 固有差异）
- 根因：daily API 与 minute API 收盘价差异（0.5-1.3%），Tushare 固有差异，无法修复
- 证据：Trae 实测本地=云端 0.00% 差异；3 只 failed ETF 均为该差异导致
- 处理：`bootstrap-approve --run-id bs_07b91d6bea --asset-type ETF --reason "..."`

### 4.2 failed STOCK（若有）
- 逐个核对 error_msg：
  - 若为超时/网络抖动 → retry-due 回收重试
  - 若为数据固有差异 → approve
- 处理：`bootstrap-approve --run-id bs_07b91d6bea --asset-type STOCK --reason "..."`

### 4.3 dead_letter（若有）
- 逐个核对根因，确认非系统性缺陷
- 处理：`bootstrap-approve --run-id bs_07b91d6bea --reason "..."`

## 5. blocked supersede（159739 定性）

- 背景：blocked 大量 STOCK 的根因是 Bug 1+2（分钟列裁剪失效 + row_limit 截断）
- 修复后重跑：blocked 应转为 completed 或 pending 重跑
- 若仍有 blocked（159739 等）→ 需定性：是 Bug 修复不完整还是新问题
- 检查：`SELECT code, blocked_reason FROM qfq_bootstrap_item WHERE run_id=... AND status='blocked'`

## 6. C-5 audit 检查项

- [ ] run 状态 clean 或已批准全部 failed/dead_letter
- [ ] 无未处理的 blocked（或已定性）
- [ ] `_check_minute_cov_raw` 覆盖检查通过
- [ ] cross_table_overlap postcheck 通过
- [ ] export 缓存命中率正常

## 7. 填写说明

- 实际清单导出后粘贴到本文档 `## 3` 之后
- 每条 approve 的 reason 必须具体（根因结论），禁止笼统
- 完成 C-5 后在进度报告 v6.7.31+ 更新

## 8. GitHub 同步清单（框架层修复，须用户确认后执行）

按 AGENTS.md 铁律，以下改动属"本地回测框架层修复"，同步 GitHub 前必须经用户确认。

### 8.1 待提交文件（11 个修改 + 新增）

```text
M quantstudio/pipeline/daemon.py                  # A4 增量变更检测闭环
M quantstudio/pipeline/mcp/client.py              # 修复 MCPTransportError import + query_updated_since
M quantstudio/pipeline/qfq_orchestrator_cli.py    # bootstrap-approve 命令
M quantstudio/pipeline/qfq_reanchor_schema.py     # A+ approved 列
M quantstudio/pipeline/qfq_resident_orchestrator.py  # bootstrap_completed() A+ 门 + approve_bootstrap_items
M quantstudio/pipeline/qfq_schema_contracts.py    # A+ 指纹更新
M quantstudio/pipeline/sources/mcp_adapter.py     # Bug1+2 修复 + export 缓存网格化 + 流式分片
M tests/test_mcp_export_cache.py                  # 扩展
M tests/test_mcp_streaming.py                     # 扩展
M tests/test_qfq_bootstrap_gates.py               # A+ 6 个新测试
?? quantstudio/pipeline/update_detector.py        # A4 新增
?? tests/test_update_detector.py                  # A4 新增 6 测试
```

### 8.2 需同步的文档（README + 引用文档）

按铁律，"同步内容必须完整"：
- [ ] `README.md`：涉及 MCP 数据源/取数链路/框架修复的表述（L7/18/19/29-38/59 等）
- [ ] `docs/strategy_toolbox.md`：如涉及数据适配层 API 表述
- [ ] `docs/prompt_engineering.md`：如涉及取数/数据源表述
- [ ] 新文档 `docs/mcp_migration/wp7e3-*.md`（A4 执行文档、approve checklist 等）按需纳入

### 8.3 提交建议

- 一个提交包（或按主题拆分：A4 / A+ / Bug1+2 三组）
- 用户确认后执行 `git add + commit + push`（remote: yangyizhu8/quantstudio）
