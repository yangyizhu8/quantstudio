# WP7-E3 450 修复计划（front 285 + raw 165 重灌 → 二次补跑）

> 版本：v1（2026-08-12，reasonix 起草；ZCode 研究轮次上限后由审核方直接拟定）
> 背景：两 run 合计 completed 4719/5487（86%）；450 个待修复 = front 285 +
> raw 165。C-6 释放决策已定（现在释放 + 修复并行，互不阻塞）。

## 一、根因（已定性）

- **front 285**：云端在灌数后更新了 7 月分钟数据（96.8% 差异集中 2026-07）→
  本地 stored 分钟 front 陈旧 → rebase 校验 dev>1e-3 失败；
- **raw 165**：部分证券日线 raw 与云端不一致（33 个 <50 行、44 个 50-500 行、
  88 个 >500 行；600266≈全历史 2058 行、689009=1389 行）→ 非局部窗口问题。

## 二、修复路径：重灌 stored 数据（纯数据操作，不改框架逻辑）

**入口（已核实）**：daemon 采集路径 `daemon.py:1437`：
`fetch_table(table, start, end, freq, codes=[...])` → aligner `_apply_qfq`
（aligner.py:913-978：front=raw×adj_i/adj_latest，分钟 bar 按交易日连接日频因子）
→ validator → `_stamp_and_write` 覆盖写库。

**🔴 关键要求（codes 批量，禁止逐证券）**：
- ❌ 禁止：450 次 `codes=[code]` 单证券调用（450 × 全市场 export = I/O 灾难）；
- ✅ 正确：**一次 `fetch_table(codes=<450 清单>)`**（单次全市场 export → 本地过滤
  450 个）或 daemon 临时任务 `codes=<450 清单>`；
- 执行方式二选一：① daemon 临时任务（collector_tasks 加临时任务，codes=清单，
  full_range 窗口）→ `--mode once --task <临时任务>`；② 直写重灌脚本（import
  fetch_table + aligner 链，复用 daemon 单证券函数体逻辑）。

## 三、两个阶段

**阶段 1：raw 165 日线重灌**（stock_daily，全历史 2018-01-01 起，full_range）
- 覆盖 165 个证券的日线 raw OHLC（以云端当前快照为准）；
- 重灌走线1还原（fetch_table 内 _restore_qfq_if_required）→ stored raw 与云端一致。

**阶段 2：front 285 分钟重灌**（stock_minutes 268 + etf_minutes 17）
- 窗口：stock 分钟 = 主库分钟范围（近 1 个月 2026-07-01 起）全量重灌（不只 7 月
  窗口——顺带覆盖 5 月 9 个同类差异，重灌量小）；
- ETF 17 = etf_minutes（近 3 个月窗口）。

**重灌后验收（必查）**：
- 600266（2058 行）、689009（1389 行）、000546（66 行）：重灌后 stored 与
  `fetch_table` 返回值逐行一致（dev=0）；
- 抽样 5 个 front 证券：stored 分钟 front 与云端一致（重灌后 precheck 应通过）。

## 四、二次补跑

1. 重灌完成后：新 `bootstrap-plan`（450 个清单）→ 新 run；
2. 补跑（shard LRU 已优化，~100/h）→ 450 个 completed；
3. 验收：450 completed + 守恒（两 run + 新 run 合计 completed = 5487）+ 门禁复查。

## 五、调度避让

- 重灌写主库（当前自由），避开 06:00 daemon 增量 / 16:00 ETL 窗口（重灌短窗口
  锁可接受，其他任务错峰）；
- 与 C-6 释放 / 观察期 2+2 并行互不阻塞。

## 六、监控纪律（三次失真教训）

- 全程以 `qfq_bootstrap_item` 实态为准（SQL 查询），不用日志标记外推；
- 每阶段回报：completed/failed/blocked/pending 实态计数 + 耗时。

## 七、C-6 状态（并行线）

- 释放决策已定：现在释放 + 修复并行；
- C-6 最后一步（release 实际执行）等用户最终确认（preflight 回报后）。

## 验收标准（总）

1. 450 重灌后 stored 与云端逐行一致（600266/689009/000546 必查）；
2. 二次补跑 450 completed、零 failed/blocked；
3. 全量守恒：completed 合计 = 5487，无 pending 残留；
4. `bootstrap_completed()` 返回 True；
5. 全程铁律：不改 fetch_table/aligner/validator/write 逻辑，纯数据重灌。
