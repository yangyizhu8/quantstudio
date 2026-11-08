## 【铁律】WAL 残留处置与双平台运维脚本（2026-10-19 总调度裁定 ｜ 2026-11-08 依据③修订·裁二甲）

1. **触发条件**：采集进程被强杀/崩溃后，或 `data/quantstudio.db.wal` > 256MB 时——先关 GUI 与 daemon，再运行项目根 `check_wal.sh`（macOS/Linux）或 `check_wal.ps1`（Windows）。脚本幂等：自动备份 + 收敛（CHECKPOINT）+ 验证；**早退 ≠ 已收敛**，彻底收敛加 `--force`（.sh）/ `-Force`（.ps1）。
2. **红线**：禁止运行期间 CHECKPOINT；禁止手工删除 `.wal`（脚本移走陈旧 WAL 前已备份；Windows 版默认只备份 WAL，主库备份用 `-BackupDb` 显式开启）。
   > **红线语境界定（2026-11-08 补记）**：本条约束的是**外部运维脚本（`check_wal.*`）对正在运行的 daemon 库执行 CHECKPOINT**。**引擎自身在 `DaemonLifecycle` 空闲期（持 `.daemon.lock`，无 collector 无连接）的周期自检不在此列**——见依据③ T2.1。两者执行主体、库状态、触发方式均不同，不构成冲突。
3. **回归门**：改动任一 `check_wal` 脚本后，必须跑 `bash scripts/check_wal_regression.sh` 全绿（合成 fixture、绝不触碰生产库；macOS 用真实工具、Linux/Git Bash 自动注入 shim）。回归矩阵是**交付工件质量门**的固定组成部分。
4. **依据**（2026-10-19 客户 Mac 实弹）：① 2,881 字节陈旧 WAL 致库打不开——体积阈值不可作为唯一判据，**探测优先**；② macOS 默认 bash 3.2 下 `$VAR` 后紧跟中文字符会误判变量名（`set -u` 报 unbound variable）——所有 bash 脚本一律 `${VAR}` 花括号界定；③ **duckdb 版本门**（2026-11-08 修订，见下）；④ `stock_daily` 日期列为 `time`（非 trade_date），脚本须自动探测。

   **③ duckdb 版本门（2026-11-08 总调度裁二＝甲照准修订）**

   - **放行集合不变**：`{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`，判定源 `quantstudio/pipeline/duckdb_version_policy.py`（**该集合本身不动**）。
   - **缺陷**：1.5.0–1.5.5 存在 ART index-delete 致命回归（`duckdb#23645`：1.5.x 由静默错删变为 FATAL，**1.5.6 未修复**）。

   - **暴露面（取代旧判断「本项目暴露面为 0」）**：
     **全部带 PRIMARY KEY 的 ART 索引均属暴露面**。旧判据「非唯一 ART = 0 / 41 个主键三臂验证不丢」**不足以豁免**——2026-11-07/08 两例实证坏的都是 **PK 的 ART**：
     - `etf_daily`：11-07 06:03:11，`Corrupted unique ART index "PRIMARY_etf_daily_0"`
     - `stock_minutes`：11-08 14:31:22，`Corrupted unique ART index "PRIMARY_stock_minutes_0"`

   - **四个实证形态**（同族缺陷已补全）：
     ① **写入期**：普通 upsert 触发 FATAL；
     ② **pending WAL 期**：另一引擎 checkpoint 后索引序列化损坏（`duckdb#23788`，regression since 1.4.0）；
     ③ **构建期**：多线程一次性构建大表 PK 索引时虚假报 `Duplicate key`（数据实际零重复）；
     ④ **污染残留期**（2026-11-08 新发现）：**失败的事务（内存爆/异常中断）在库文件留下未回收的 ART 索引块，后续对同名表重建时复用这些块 ⇒ 必然虚假报 `Duplicate key`**。

   - **第四形态恢复 SOP**（2026-11-08 `stock_minutes` 2.5 亿行实证，约 100 分钟）：
     ```
     dump（parquet ZSTD；行数 + 主键 hash 双校验）
       → DROP TABLE
       → CHECKPOINT          ← 必须！收敛残留索引块，缺此步必失败
       → CREATE TABLE（原 DDL，含 PRIMARY KEY）
       → 导入（threads=1 + preserve_insertion_order=false + 按日分批 + 每 20 批 CHECKPOINT）
       → V1/V2/V3 校验（行数 / 主键 hash / 逐日对拍 / PK 生效 / 索引一致性 full-scan=GROUP BY）
     ```
     **关键坑**：`DROP` 与 `CREATE` 之间**必须 CHECKPOINT**。六次失败（内存墙/临时目录满/虚假 Duplicate ×4）的真因即残留索引块；同一批数据在**新库成功、生产库失败**、`CHECKPOINT` 后立即成功——三者构成闭环证据。

   - **预防三件**：
     - **T2.1 周期 CHECKPOINT**：已落（`3977d7a`），daemon 在 `DaemonLifecycle` 空闲期每 `checkpoint_interval_sec`（默认 1800s）自检；首 tick 强制一次。
     - **T2.3 入口 CHECKPOINT**：已落（`3977d7a`），`once` 与 `GUI` 两入口启动时各一次（逐入口清单见 `docs/t21-t23-implementation-plan-20261108.md`；回填工具/对账工具/运维脚本/pytest **明确不加**）。
     - **T3.1 索引一致性 canary**：已实现待接入（`a38e1ee`，22 单测），以 `filtered 计数 vs full-scan 计数` 对拍作 FATAL 前的早期告警（依据 `duckdb#23645` 原文「A filtered-vs-full-scan parity check makes a reliable canary」）。

   - **1.5.6 纳入放行集合的理由改记**（原「暴露面为 0」已废）：
     **「1.5.6 为当前最新版 + 缓解 SOP 在位」**——即：无更高版本可升（1.5.6 已含 `#25693` 的 dead-node 修复但未覆盖全族），且已具备「周期自检降概率 + canary 早发现 + 约 90-100 分钟重建恢复」的常态化治理能力。

5. **适用范围**：跨会话、跨智能体、跨项目共同遵守，长期有效，除非用户明确要求修改或撤销。
