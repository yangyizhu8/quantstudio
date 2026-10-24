## 【铁律】schema 指纹严格比对库：加列＝契约变更（2026-10-21 总调度备案）

1. **识别条件**：凡对**被指纹校验的表**（`source_watermark` / `trade_calendar` / QFQ 15 表等）
   做任何 DDL（加列/删列/改类型/改 NOT NULL/改 PK）——只要该表参与
   `verify_fingerprint(..., reject_extra=True)` 逐字比对，**加一列即判 `partial_or_mixed`**，
   `DuckDBWriter._init_tables` 安全闸随即**拒绝启动**，采集全线停摆。
2. **强制动作（六件套同 commit，缺一不得执行）**：
   ① 契约期望列集（`qfq_schema_contracts.py` 的 `TARGET_*_FINGERPRINT` 与 `*_DDL`）；
   ② 版本常量（`qfq_reanchor_schema.SCHEMA_VERSION`）；
   ③ `detect_schema_status` 新状态分支（**新旧两版同时放行**，防升级窗口任一侧停摆）；
   ④ `writers.py` 安全闸放行集；
   ⑤ 迁移 runner（预检旧态 → 迁移 → 复检新态 → 幂等 → `--dry-run` → 失败回退复检）；
   ⑥ 测试（状态矩阵 + 迁移幂等 + 失败回退 + 双版本写入兼容）。
3. **迁移硬门**：迁移脚本必须**默认拒执行**，需显式确认开关（如
   `--allow-schema-contract-change`）才动作；脚本内注明事故依据。
4. **窗口纪律**：迁移期间不仅 daemon 停，**ops 类 CLI（`--mode once`、GUI 跑任务、
   任何会 `create_writer`/`init_schema` 的入口）一律禁跑**——中间态会触发
   `_WriterSchemaMigrationRequired` 或写半态数据；窗口内只允许只读探测、迁移脚本、迁移后验证。
5. **依据**（2026-10-20 实弹，firefly 水位线案）：单独 `ALTER ... ADD COLUMN last_run_id`
   成功加列、24 行不变、dry-run 通过——随后 `--mode once` **被 schema 安全闸拒绝启动**；
   `detect_schema_status()` 实测 `complete_2_1 → partial_or_mixed`，`DROP COLUMN` 后即
   恢复 `complete_2_1`。**定谳：加列不是元数据操作，是契约变更。**
6. **适用范围**：跨会话、跨智能体、跨项目共同遵守，长期有效，除非用户明确要求修改或撤销。

