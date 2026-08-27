# 【草案待审】实时进度报告 addendum 草稿（2026-08-21，总调度起草）

> 用途：经用户审核后插入《实时进度报告.md》。周一 SEGMENT-2 终验/黄金基线结论出来后补 §5 再定稿。
> 状态：⏳ 草案——尚未写入正式进度报告（铁律：审核通过后才更新）。

## v6.8 addendum — 治理快照/分片 hash/批1-3 数据修复收口 + 黄金基线冲刺（2026-08-21）

### 本阶段完成（附证据）
1. **分片 hash v4**（QuantStudio 稳定化/ZCode）：66/66 测试通过（T1-T9 9/9 + R5 SNAP_001 18/18 + R5 SNAP_002 18/18 + 回归 27/27 + guard 12/12）。代码 `scripts/governance_snapshot.py`；证据 `output/golden_baseline/sharded_hash_r5_snap001.json` / `_snap002.json`（2026-08-20 产出）。
2. **写锁收口**：全库 34 点接入 + 硬约束；guard 扩展名锚定匹配 + QDB 白名单。
3. **快照链**：SNAP_001/002 pinned（`data/snapshots/PINNED.json`）；SNAP_003 最终快照排期周六 23:30（方案 A，DSH 第 14 轮放行）。
4. **数据修复批 1-3**（生产库已验证）：
   - 批1：40 码 74,983 行重锚（D2-F1 closed）
   - 批2：07-01 ETF 补拉 1,974 只 + 增量同步至 08-18（D2-F2★ / D1-TODO-1 closed）——证据 `output/golden_baseline/batch2_sync_apply.json`（verify_0701_count=2047）
   - 批3：strategy_events 去重删 3 行（D2-F5 closed）
5. **登记表 v1.22**（issue_registry.md）：D2 复检裁定全收口，基线解除阻塞；closed 6 项（D2-F1、D2-F2★、D2-F5、D1-TODO-1、D3-X4、D3-X5）；另 D2-F6 定性 known-noise（非 closed，因子链完好）。
   - 批2 数字口径注：补插 1,974 只 + 存量 73 只 = verify_0701_count 2,047（qdb_codes 2,047 为 QDB 侧 07-01 全池目标，duck_existing 73 为补拉前本地已有，to_insert=2,047-73=1,974，见 batch2_sync_apply.json item1_0701 三字段互证）。

### 待需完成（按依赖顺序）
1. 周六 23:30 SNAP_003 create（自动链）→ 周日分片 verify → bind --protect → 回填 SNAP_002 独立 verify → 关闭批2 gate exception
2. 周一起：SEGMENT-2 终验 PASS（前置门）→ 黄金基线建立 → 文档同步（README/docs）→ 用户确认 → 双仓库推送（周二~四）

### 新登记待办
- **F1-F5 冻结项批复已登记（2026-08-21 用户批复，证据：docs/handoff/baseline_20260821_0040_dispatch.md）**：F1 终验 PASS 后白名单按计划退役；F2 560650.SH 占位 K 线待重锚实测通过后清理；F3 批 B 方案（不接线、随终验退役归档），前提=终验黄金门覆盖"云端 is_qfq=false 残留=0"校验（WP6.2 职责承接），待终验确认后核销；F4 周末代码冻结（范围=各任务代码分支，周一 01:05 随 SEGMENT-2 解冻）；F5 技术债 5 项推送后逐项排期。
- D2-F3 静态表缺口：批2 apply 无 inserted 证据，维持 pending（待索证）
- 技术债 5 项（推送后排期）：分钟 front 口径不一致 / unprotect journal / hash 性能 / 写入队列+并行度 / QDB 调度源+ETF wrapper+snapshot 迁时段

### 阶段进度总览影响
- "最终快照+黄金基线"阶段从"待需完成"推进至"执行中（周六窗口）"。
- 变更记录追加：2026-08-21 v6.8（本条）。
