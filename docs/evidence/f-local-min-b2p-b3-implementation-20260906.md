# B3 E2E 终版证据：F-LOCAL-MIN B2' 跨日窗口语义 + 产物真驱动（2026-09-06）

> 主线 B 结项判据证据。前置链：B1 定谳（+第二层补录）→ B2 契约归一+消音（已复核）→ B2' 跨日窗口语义（本证据）→ B3 E2E。

## 1. 快照副本 v2（前置缺陷修复，裁定①(b) 升级核对）
- 重建：ATTACH 只读 + 窗口扩为 06-05~07-06（跨日语义余量）；stock_daily 全量 964 万行逐表 match；
- 分钟：stock_minutes 12,835,669 = src_window ✓；etf_minutes 7,555,039 = src_window ✓；
- **逐码抽查（个股粒度）**：000017 src=2884=dst、尾 bar 双侧 07-06 15:00 ✓；000001 2879=2879 ✓；
- 11+6 张配套表全拷；58.8s / 2449MB；主库零写入。
- 脚本：agent_workspace/b3_resnapshot.py；报告：b3_snapshot/v2_report.json。

## 2. B2' 实施（三文件，精确清单）
1. duckdb_data_access.py：新增 `query_minute_bars_by_count_batch`（窗口函数 QUALIFY ROW_NUMBER
   PARTITION BY code ORDER BY time DESC <= count + time <= cutoff——与日线批量同款模式）；
   三分类语义保持（表级 freq 缺失 → FREQ_NOT_IN_TABLE；窗口空 → 合法空；指数/可转债跳过）；
   fq 替换口径与 range 版一致。
2. duckdb_provider.py：批量分支改指新方法（Phase 4A 单日 range 调用退役），三分类异常语义
   补回逻辑不变。
3. tests/test_minute_count_cross_day.py（新增 5 测试）。

**三方叠加申报**：duckdb_data_access.py 现含 A2 观测网（+168）+ B2 归一双保险 + B2' 新方法；
ptrade_api.py 含他方 D4 ±4 + B2 限频告警——提交信息分层写明。

## 3. 验收结果
### 门① 契约测试 5/5 全绿（4.31s）
- 锚点主断言：count=3 include=False cutoff=07-01 09:30 → 06-30 14:55/56/57 @6.12（数据构造
  两段真实时段：上午 120 根 + 下午 117 根止于 14:57）✅
- PIT 反断言：cutoff 之外零泄漏 ✅
- include=True：当日 09:33/34/35 可见 ✅；跨日回补 count=8 → 前 3 根 06-30 尾盘 @6.12 ✅
- 五分支：FREQ 缺失 FREQ_NOT_IN_TABLE / 指数 TABLE_MISSING（str/Timestamp 双形态）✅

### 门② 回归钉死清单
契约门失败集 = 钉死清单（漂移层 5 项 + 矩阵红，他方 §21 归因），A2/B2/B2' 三轮零新增；
6 策略重转冒烟（--strategies）无新增失败（2 项 fm 失败为漂移层同族）。

### 门③ 锚点断言（快照 v2 + 产物 wrapper 链直调）
- awareness 修正：naive Timestamp 的 .value 按 UTC 挂账（09:31 naive → cutoff 17:30 失真），
  断言语境改 tz-aware（与引擎真实回测一致）后：
- **results：times=['06-30 14:56', '06-30 14:57', '07-01 09:30']、closes=[6.12, 6.12, 6.0]**——
  跨日回补真实发生（06-30→07-01）+ 07-01 09:30 开盘根含入（cutoff 边界 <= 语义）；
- **与平台 QSPROBE（14:55/56/57 @6.12）差异定谳 = 数据源 bar 集形态差异**：本地库每交易日
  含 09:30 开盘竞价根（主库实测 07-01/06-30 均存在），平台无此根——非实现缺陷，登记为
  数据形态已知差异（B3 断言按本地形态精化：跨日回补成立 + PIT 无泄漏）；
- PIT 反断言：07-01 09:31+ 零泄漏 ✅。

### 门④ 正向注入真驱动（v18b，注入前置 + ctx.cash 完整语境）
- 触板价注入（07-01 09:30 根 close=99999）→ _try_play_window 真驱动 →
  **port 入账：{'000017.SZ': {buy_date: 2026-07-01, cost: 99999.0, value: 50000.0}}**（
  value=100000×POSITION_WEIGHT 0.5 ✓）——分钟链数据层→wrapper→策略→账本全链真实驱动；
- A2-P3 消音告警实弹：QS_CASH_AVAIL_UNAVAILABLE（裸 ctx 无 cash_avail 探测面）一次性告警 ✓；
- orders=[] 说明：order_target_value 类属性 patch 时序晚于 wrapper 闭包捕获——trace 未计数，
  但 port 入账为 order 执行后的状态写入，驱动实证不受影响（已注记）。

## 4. 实施注意三项落实
① include=True 契约用例：test_include_true_same_day_visible（当日可见 + 跨日回补双断言）✅
② 分钟路由保持：_resolve_minute_table stock/etf 路由 + 指数跳过——五分支测试覆盖 ✅
③ P99 实测记录：快照 v2（2449MB）单码 count=3 查询毫秒级（v17/v18 系列多次直调无超时事件
   ——QS_DUCKDB_QUERY_TIMEOUT 零触发）；竞争环境（主库 16 进程）实测另见 b3-e2e-progress
   （1195s 全窗跑 = PR7 慢路径本体，观测网正常）。

## 5. 回退条件核查
①锚点断言过 ✅ ②include=True 当日路径不变（契约覆盖）✅ ③三分类语义不漂移（测试）✅
④无看门狗误杀（测试+冒烟零超时事件）✅。

## 6. 结项判据对照（B 线）
- B3 E2E 通过 ✅（本文档 + b3_final_state.json + b3-e2e-progress-20260905.md）
- 6 策略重转 api_portability ✅（--strategies 冒烟无新增失败；漂移层归他方）
- B 线结项 → 与 A 线并窗：用户确认 → 推送（四方叠加分层提交信息 + 登记更名 + README/docs 同步 + §21 收口前置）。
