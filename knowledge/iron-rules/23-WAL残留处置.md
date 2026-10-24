## 【铁律】WAL 残留处置与双平台运维脚本（2026-10-19 总调度裁定）

1. **触发条件**：采集进程被强杀/崩溃后，或 `data/quantstudio.db.wal` > 256MB 时——先关 GUI 与 daemon，再运行项目根 `check_wal.sh`（macOS/Linux）或 `check_wal.ps1`（Windows）。脚本幂等：自动备份 + 收敛（CHECKPOINT）+ 验证；**早退 ≠ 已收敛**，彻底收敛加 `--force`（.sh）/ `-Force`（.ps1）。
2. **红线**：禁止运行期间 CHECKPOINT；禁止手工删除 `.wal`（脚本移走陈旧 WAL 前已备份；Windows 版默认只备份 WAL，主库备份用 `-BackupDb` 显式开启）。
3. **回归门**：改动任一 `check_wal` 脚本后，必须跑 `bash scripts/check_wal_regression.sh` 全绿（合成 fixture、绝不触碰生产库；macOS 用真实工具、Linux/Git Bash 自动注入 shim）。回归矩阵是**交付工件质量门**的固定组成部分。
4. **依据**（2026-10-19 客户 Mac 实弹）：① 2,881 字节陈旧 WAL 致库打不开——体积阈值不可作为唯一判据，**探测优先**；② macOS 默认 bash 3.2 下 `$VAR` 后紧跟中文字符会误判变量名（`set -u` 报 unbound variable）——所有 bash 脚本一律 `${VAR}` 花括号界定；③ duckdb 1.5.x 与钉版 1.4.x 不兼容（`Conflict on tuple deletion`，版本门必须 1.4.x）；④ `stock_daily` 日期列为 `time`（非 trade_date），脚本须自动探测。
5. **适用范围**：跨会话、跨智能体、跨项目共同遵守，长期有效，除非用户明确要求修改或撤销。

