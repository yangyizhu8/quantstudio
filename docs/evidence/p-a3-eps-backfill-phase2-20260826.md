# P-A3 二期验收证据：真实库 eps 跨表回补（2026-08-26）

> 实施方：跨表回补接手会话（六步流水线第 6 步推送完成后启动）
> 裁定时点：总调度批准（两笔推送完成后今日午后执行；避开明晨 03:00 同步）
> 双远程推送：quantstudio-plus / quantstudio 均 @ `4430788`（ls-remote 核对一致）
> 回退点：`c9cfffb543f2c84f303d1f4283d81e46d30e2529`（`git stash create -u`，记录于
> 私募工作文件/QuantStudio-MCP全数据源替代任务文件/P-A3-phase2-rollback-20260826-143257.txt）

## 1. 执行前置检查

| 项 | 结果 |
|---|---|
| 快照写锁 | 无 `.write_lock`（空闲） |
| 并行主库写者 | 无 daemon/incremental/ETL 进程（仅 2 个 run_ptrade_strategy 回测进程，只读回测） |
| QS_AUTO_BACKFILL_EPS | 未设置 → writer 自动回补 gate 保持默认关（二期为人工 CLI 路径，无影响） |
| 原值备份 | 3,189 行 CSV 落盘（sha256 `6a83f69be5d1ca6bfb63bf46de4a6096a72455cac8de59f8b8352e3e132d7cbb`） |

备份文件：`docs/evidence/p-a3-eps-backfill-phase2-20260826-143438-orig-backup.csv`
（列：code/end_date/fi_ann_date/fi_eps_orig/fi_data_source/ic_ann_date/ic_basic_eps；
fi_eps_orig 全为 NULL=3,189 行；fi_ann vs ic_ann：相等 3,188 / fi<ic 1 / fi>ic 0）

## 2. 执行与结果

```
python scripts/backfill_eps_gap.py --check      → gap=3189（预期基准，门禁 error 语义 exit=1）
python scripts/backfill_eps_gap.py --backfill    → dry-run: rows_updated=3189 ann_date_adjusted=1 affected_codes=2235
python scripts/backfill_eps_gap.py --backfill --apply
    → apply: rows_updated=3189 ann_date_adjusted=1 affected_codes=2235
      pairs=fin_indicator.eps<-income_statement.basic_eps
```

- dry-run 与 apply 行数**完全一致**（3,189 / 2,235 码），受影响码数与行数无漂移。
- **幂等**：apply 后二次 `--backfill`（dry-run）→ rows_updated=0 ann_date_adjusted=0 affected_codes=0 ✅

## 3. 验收项（全部 PASS）

### 3.1 门禁闭环
```
python scripts/backfill_eps_gap.py --check → gap=0 (OK 免疫闭环) exit=0
```

### 3.2 打标与影响面
| 项 | 实测 | 预期 | 判定 |
|---|---|---|---|
| backfill_eps_source 打标行 | 3,189 | 3,189 | ✅ |
| 打标涉及码数 | 2,235 | — | ✅ |
| eps NULL 剩余总数 | 1,638 | 4,827−3,189=1,638（不可回补区） | ✅ |
| 回补行 ann 分布 | fi=ic 3,189 / fi>ic 0 / fi<ic 0 | ann_date=max(fi,ic) | ✅ |

### 3.3 000063 抽检（值=basic_eps、ann_date=max）
```
('000063', end_date=2026-03-31, ann=1777046400000, eps=0.27,
           src='income_statement.basic_eps', ic.basic_eps=0.27, ic.ann=1777046400000)
→ eps == basic_eps ✅  ann == ic_ann（max）✅  打标 ✅
```

### 3.4 000858 抽检（不可回补区，保持 NULL 语义正确）
- 000858 最新行（2026Q1，ann=1777478400000）eps=NULL；
- income_statement **无同 (code,end_date) 行**（备份 CSV 中 000858 不在 3,189 候选集，ic_hit=0）；
- → 属 1,638 不可回补区（次新/收入无该报告期），**按设计保持 NULL，不造数据** ✅
- （对照组：000858 2025 年报行 fin.eps=2.3068 == ic.basic_eps=2.3068，双表已一致）

### 3.5 PIT 实证
- 回补行 `ann_date < ic_ann` 违规数 = **0**（3,189 行全部 ann >= 源表公告日）
- ann_date_adjusted=1 定位于 fi_ann < ic_ann 的 1 行（抬高至 max）；备份 CSV 原值分布
  相等 3,188 / fi<ic 1 与调整计数精确吻合

### 3.6 最新公告行残差（口径归因）
| 口径 | 最新行 eps NULL 码数 |
|---|---|
| ann_date 最大（本验收） | 55（全部 income 无行，不可回补） |
| end_date 最大（消费路径） | 30（全部 income 无行） |

- 设计基线 62（2026-08-25 快照）→ 今日 55/30：**收敛方向**。差量 7 码源于
  8/25→8/26 源端新公告推进（部分码最新公告行已非 NULL），非回补遗漏（gap=0 已证）。
- 门禁 `check_eps_backfill_gap=0` 为权威闭环：当前库不存在「eps NULL 且 income
  同 key basic_eps 非空」的任何行。

## 4. 结论

**P-A3 二期真实库回补验收通过**：3,189 行精确落库、gap→0、幂等、打标可查、
000063 抽检通过（值=basic_eps、ann_date=max）、000858 属不可回补区保持 NULL
（收入无同 key 行，语义正确）、PIT 违规 0、原值备份 sha256 在案、回退点 c9cfffb 可逆。

待办（与总调度协调时点）：
1. week10 保真重跑：先复现 4313/15 基线（需回补前库快照/基线 worktree 对照）
   → 回补后 L6_eps 15→≈20、L3v 4313→向 4373 收敛 → 残差逐项归因
   （or_yoy 无源 48 码 / 平台口径 / 次新 62 码）
2. 合并基线重验一次（P-A3 二期 + 双端对齐 B2 + D3 全部落地后统一双跑+三类归因分解，
   勿单独重跑——与总调度/稳定化协调时点）
3. 证据：本文档 + 进度报告 addendum 追加

## 5. 变更记录
- 2026-08-26 14:32-14:45：--check → stash c9cfffb → 备份(sha256) → dry-run → apply → 验收全绿。