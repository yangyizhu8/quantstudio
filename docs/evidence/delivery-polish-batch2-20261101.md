# 收尾改进批次（2026-11-01 用户批）· 两项体验修正

**纪律红线**：实施期间 `stock_minutes` 增量进程（pid 32628）仍在生产库运行 ⇒
本轮全部测试一律隔离 `QUANTSTUDIO_DATA_ROOT` + `QS_WRITE_LOCK_DIR`，**零触碰生产库**。

---

## 1 任务页「读失败误显为无」修复（GUI 渲染层）

**缺陷（用户实测）**：库被采集占用时，任务页把**空查询**渲染成水位「无」/ 数据止点「—」/ 状态「未知」，
险些诱导误点「⏮ 重置水位」。

**实现（只改 `task_tab` 渲染层，未动 `DbHelper`）**：
- 新增 `_busy_hint()`：只**读** DbHelper 已有的 `_last_busy_at`（A4 属性），
  窗口 `BUSY_HINT_WINDOW_S`（30 s，从 `quantstudio.gui.db_helper` 只读导入）；
- 新增 `_busy_placeholder(original, busy)`：`busy` 且原值属 `（无/—/未知/空）` ⇒
  **`读取中（库被采集占用）`**；其余原样返回；
- 三列同款：水位线、数据止点、水位状态；tooltip 分别说明
  「这是读不到，不是没有水位。请稍后刷新再判断，勿据此重置水位」。

**测试两向**（`tests/test_delivery_task_tab_busy.py`）：
- busy + 空（四种空标记）⇒ 占位符 ✅
- 正常读取（真实日期）⇒ 原值不变；非 busy 时「无/—」也不改写 ✅
- `_busy_hint`：30 s 内 busy ⇒ True；超窗 ⇒ False；无 db_helper ⇒ False ✅

## 2 `trade_calendar` 的 BEHIND 噪声消除

**问题**：日历表数据天然延伸到未来（排期到年底），"水位落后于 max(cal_date)" 无诊断意义，
却永远挂在「需关注」里稀释真信号。

**实现**：
- 新增排除性标签 **`CALENDAR-NOT-APPLICABLE`**（`constants.TableState`），并纳入
  `EXCLUSIONARY_STATES`；`CALENDAR_TABLES = {"trade_calendar"}`；
- `stats.collect_table_stats`：命中日历表 ⇒ 状态直接判 `CALENDAR-NOT-APPLICABLE`
  （`row_count` / `max_date` / `watermark_last_date` 仍照常记录，信息不丢）；
- `stats.attention_tables`：日历表**不进**「需关注」；`can_incremental` 不受其影响；
- **manifest 同步**：`contracts/manifest-v1.schema.json` 的 `state` 枚举追加该标签；
- **spec 同步**：FR-018 追加该排除性标签的说明（2026-11-01 用户裁定），复验报告随之输出。

**测试**：
- 日历表状态 ∈ 排除性标签、∉ 有害态；`CALENDAR_TABLES` 含 `trade_calendar` ✅
- 日历表「落后」不影响 `can_incremental`，且不出现在 `attention_tables` ✅

---

## 3 读数

| 项 | 读数 |
|---|---|
| 测试 | **170 passed / 0 failed / 1:48**（隔离两变量下执行） |
| 新增用例 | 5（busy 三向 + 日历两向） |
| SC-013 | **PASS**（36 保护路径；唯一变化 `writers.py` 判 `OTHER_LINE`，非本会话） |
| 生产库 | **零触碰**（本轮所有 pytest 均在隔离 `DATA_ROOT`/`LOCK_DIR` 下运行） |

## 4 影响面提示（用户侧）

- 任务页在采集进行中打开/刷新：水位三列会显示「读取中（库被采集占用）」而不是「无」——
  **这是"读不到"，不是"没有水位"**；稍后刷新即恢复真实值。
- 复验 / manifest 里 `trade_calendar` 不再是 `BEHIND`，而是 `CALENDAR-NOT-APPLICABLE`；
  「需关注」清单因此从 5 张降到 4 张（`etf_daily` / `etf_minutes` / `stock_daily` / `stock_minutes`）。
