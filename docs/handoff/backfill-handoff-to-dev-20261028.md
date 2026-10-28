---
title: 件2 回写 · 交 dev 执行交接件（B 窗前置，2026-10-28 20:00 前送达）
date: 2026-10-28
task: 总调度 2026-10-28 加办令（件2 回写脚本 + dry-run 结果今晚 20:00 前送达 dev）
status: 已交付（本线验证完成，待 dev 在 daemon 进程内执行）
---

# 一、交付物（脚本已在本仓）

| 项 | 路径 | 说明 |
| --- | --- | --- |
| 回写脚本 | `agent_workspace/first_board_high_open_1to2/_backfill_ratio.py`（37 行） | `python <脚本> <db> <start> <end> [--apply]`；**无 `--apply` 即 DRY-RUN（read_only）** |
| 执行预案 | `docs/handoff/backfill-window-runbook-20261028.md` | 四步 + 风险 + 回退 |
| 本交接件 | 本文件 | dry-run 验证结论 + 请 dev 执行的项 |

# 二、本线已完成的验证（四步全 PASS，实测读数）

| # | 验证 | 结果 |
| --- | --- | --- |
| 1 | **B2 副本（已补）对照** | 待补 = **0**；2026-01-05~08-13 区间总行 = **813,140** ⇒ 确认目标数 |
| 2 | **LIVE 副本 DRY-RUN（真实读数）** | `待补行数 = 813140 | 模式 = DRY-RUN`；样例 000068/000681/000838 ratio=1.000000 |
| 3 | **临时样本三连（DRY→APPLY→DRY-again）** | DRY 报 21,891 → APPLY「补列完成；剩余未补 = 0」→ DRY-again 报 **0** ⇒ **幂等 PASS** |
| 4 | **抽查** | 21,891/21,891 已补；ratio 合理（000738 = 0.995566 ⇒ 该股有除权） |

⇒ 脚本逻辑、幂等性、除零保护、时区对齐**全部经实测确认**，可直接用于主库。

# 三、主库锁状态（本线无法执行 dry-run 的原因）

```
duckdb.connect(main_db, read_only=True)
→ IO Error: Cannot open file "...\data\quantstudio.db": 另一个程序正在使用此文件
  File is already open
```

⇒ 主库被 daemon 独占（单写者 + 文件锁），**连只读连接都被拒** ⇒ 本线**无法**取得主库 dry-run 真实读数 ⇒ 需 dev 在**持库进程内**执行。

# 四、请 dev 在进程内执行（按优先级）

## 4.1 必做（B 窗前置，秒级）

```python
# ① 主库待补行数（= B 窗工作量）
db.execute("""select count(*) from stock_daily
  where time between <ms(2026-01-05)> and <ms(2026-08-13)>
    and close_front_ratio is null
    and close is not null and close <> 0 and close_front is not null""").fetchone()
# 预期：813140（若主库部分已补则小于此数；为 0 则件2 已无需回写）
```

## 4.2 同批捎带（裁事项 1=A：分钟缺口判向，秒级）

```python
# ② 5 码主库分钟覆盖（决定「刷新副本」vs「补采工程」）
for code in ('600916','000799','002328','002468','002607'):
    n_bar = db.execute('select count(*) from stock_minutes where code=? and time between ? and ?', [code, ms('2026-01-01'), ms('2026-08-14')]).fetchone()
    n_day = db.execute('select count(distinct time) from stock_daily where code=? and time between ? and ?', [code, ms('2026-01-01'), ms('2026-08-14')]).fetchone()
    # 判向：主库有分钟(>0) 且 缺口日远小于副本 ⇒ 副本陈旧；主库同样缺 ⇒ 采集缺口
```

## 4.3 可选（若 B 窗时长允许）

```
python _backfill_ratio.py <主库> 2026-01-05 2026-08-13 --apply   # 计时执行
python _backfill_ratio.py <主库> 2026-01-05 2026-08-13            # 幂等复跑，应报 0
```

**时长分岔**：< 5 min ⇒ 单区间 UPDATE 可接受（不改脚本）；5~15 min ⇒ 记录风险；**> 15 min ⇒ 申请分块改造**（另走 ⑤）。

# 五、判据与风险

| 项 | 判据 |
| --- | --- |
| 4.1 成功 | 返回整数，且 ≤ 813,140 |
| 4.3 成功 | APPLY 后「剩余未补 = 0」；复跑 DRY 报 0 |
| **风险** | 单事务大 UPDATE，中断即回滚（无残留，可重跑）；若回退需 `update ... set close_front_ratio = NULL` ⇒ **须先经 ⑤** |
| 数据安全 | 脚本只填 `close_front_ratio IS NULL` 的行，**不覆盖已有值**、不动其他列 |

# 六、回执请求

请 dev 回传：① 主库待补行数；② 5 码分钟覆盖判向结果；③（若执行）apply 耗时与幂等复跑读数。本线收到即出判向结论件与 A4 时长评估件。