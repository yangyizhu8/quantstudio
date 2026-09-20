# P0 缓解方案：passthrough 大表 DROP-REPLACE 触发 DuckDB index-delete fatal（整库 invalidation）

- 日期：2026-09-20｜归属：dev（六步①，**待审**）｜优先级：P0（daemon 重启前置）
- 触发链：daemon.py:820 task.get('passthrough') → _run_passthrough_task → writer.write(passthrough=True)
  → writers.py:896-906（建临时表 → DROP 原表 → RENAME，注释自称『CREATE OR REPLACE 等价』）

## 一、取证结果（本方案前实测）

| 项 | 值 |
|---|---|
| DuckDB 版本 | 1.5.5 |
| 首炸点族谱 | v1_run_20260908.log 5 处（9/08 已在，早于派单所述 9/07/9/19——族谱更长） |
| 错误全文 | Invalid Input Error: Failed to delete all rows from index. Only deleted 0 out of 2048 rows → fatal → database has been invalidated |
| Chunk 特征 | 32 列 / FLAT VARCHAR 2048 行 / 内容=股票代码——索引列数据（DELETE 清索引行失败） |
| passthrough 表量 | 69 张（整族共用同一 DROP-RENAME 通道——非个别表问题） |
| 复发节奏 | 9/19 13:22 → 9/20 09:57（重启后 37min 再犯）——确定性触发，非偶发 |

## 二、与派单描述的一处出入（需先对齐）

派单称触发点为『writers.py:896 CREATE OR REPLACE』——:896 实为 CREATE TABLE IF NOT EXISTS（无害）；
真雷在 **:905 DROP TABLE "table"**（DROP 带索引大表 → 索引全量行清除 → ART delete 缺陷触发）。
改道设计必须瞄准 DROP-RENAME 三步，而非 :896。

## 三、缓解候选（不猜，最小复现定谳）

| # | 候选 | 机理 | 风险 |
|---|---|---|---|
| C1 | DROP 前先 DROP INDEX | 无索引即无 index-delete 面 | 索引重建成本；需知索引名 |
| C2 | 原生 CREATE OR REPLACE TABLE t AS SELECT | DuckDB 1.4+ 原生语法，内部路径不走 DROP-索引清除 | 行为差异未证（约束/索引是否保留） |
| C3 | 换非 passthrough upsert（B′ 同向） | 走 :569 write 主路径（ON CONFLICT） | 语义变更：需列契约/主键；69 表逐表论证——P0 体量过大 |

P0 推荐：C1/C2 由最小复现脚本定谳（临时库，零生产接触）；C3 列为根因修复后的长期项，不在 P0。

## 四、最小复现设计（方案的一部分，审后即跑）

临时库 tmp.duckdb：
  1) CREATE TABLE t + 建索引 + 插入 5 万+ 行（模拟 2048-chunk 多段）
  2) 执行现网三步（建临时/DROP t/RENAME）→ 期望复现 fatal
  3) 依次试 C1 / C2 → 记录哪个不触发

## 五、验收标准

| # | 项 | 判据 |
|---|---|---|
| V1 | 复现成立 | 现网三步在临时库触发同错误（否则归因存疑，回炉） |
| V2 | 候选定谳 | C1/C2 至少一个在临时库不触发且数据逐位等价（行数/列序/内容 hash） |
| V3 | 改道后全量回归 | 既有套件零新增失败；passthrough 写路径测试绿 |
| V4 | 语义等价 | 改道前后同 df 双写临时库，列集/行数/内容逐位一致 |

## 六、回退条件

- 单文件改动（writers.py passthrough 分支）；回退=还原该分支；
- V1 不成立（复现不出）→ 不动产码，升级根因排查（版本升级评估另立）；
- 生产首跑再触发 → 立即回退 + 上报。

## 七、待审问题（呈裁）

1. C2 若胜出：CREATE OR REPLACE TABLE AS 在含索引表上的行为（索引是否随替）需复现脚本一并验证；
2. 69 表是否 P0 全量改道，还是仅大表（如首触 stock_minutes 类）先行？——建议全量（同一通道同一雷，改一半留一半仍会炸）；
3. 根因线（版本升级评估 1.5.5→最新）并行开，与本缓解解耦。