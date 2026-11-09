# 舱二 EPS 导出器 O5 五步验收（DSH 侧执行 · 2026-11-10）

> 被测件（O5 开始前锚定，全程未漂移）：
> `scripts/export_eps_snapshot.py` 16,717 B／sha16 **`34c9a6d3c8fedc3f`**／331 行
> `tests/test_qmt_eps_snapshot.py` 20,034 B／sha16 **`23eacf13733bd495`**／392 行
> 执行环境：本机 Python 3.11（`C:\Users\Administrator\AppData\Local\Programs\Python\Python311`）· cwd＝仓根 · `QT_QPA_PLATFORM=offscreen`
> 纪律：**O5 期间只读＋跑测，未改被测件**（O5 首尾 sha16 双记，见 §六）

## 一、O5-① diff 范围核对（前轮已过，本轮复核）

- 我方工作树变更中**属于委派范围的仅 2 个新文件**（`??` 未跟踪），其余变更全部属他线（861 项，含 `AGENTS.md`／`README.md`／`agent_workspace/**`／`config/**` 等）；
- 委派任务包允许写入清单＝这 2 个文件 ⇒ **零越界** ✅

## 二、O5-② 构建/编译

| 项 | 结果 |
|---|---|
| `py_compile scripts/export_eps_snapshot.py` | **rc=0** |
| `py_compile tests/test_qmt_eps_snapshot.py` | **rc=0** |
| 模块导入（顶层无副作用崩溃） | **OK**，`main()` 可调用 = **True**（`spec_from_file_location` 动态加载核） |

## 三、O5-③ 静态扫描

| 项 | 结果 |
|---|---|
| `ruff check` 两文件 | **rc=0 · All checks passed!**（零告警） |
| 裸 `except:` | **0** |
| `round(` 四舍五入 | **0**（repr 精度判据的静态支撑） |
| BOM | **0**（两文件均无 BOM） |
| `print(..., file=sys.stderr)` 用法 | **8 处**（错误路径显式化） |
| 「时间戳字样」命中 1 处 | **非违规**——L104 `return int(dt.astimezone(_TZ_BJ).strftime('%Y%m%d'))` 是 **d8 日期换算的必要格式化**，非「生成时刻戳」；全文件无 `generated_at`/`datetime.now`/`time.time()` |

## 四、O5-④ 契约一致性（任务包 13 判据逐条实读）

**结论：13/13 实质合规**（初判报 11/13，其中 2 条经实读确认为**我方检测条件过窄的假失败**，已自纠）。

| # | 判据 | 实读证据（文件:行） | 判定 |
|---|---|---|---|
| ① | 常量名 `_QS_QMT_EPS_SNAP_DATA` | 全文件命中 | PASS |
| ① | META 三键 `enabled`/`base`/`n` | 实读命中 | PASS |
| ② | 固定 +8 时区（北京午夜锚） | `_TZ_BJ`；L104 `astimezone(_TZ_BJ).strftime('%Y%m%d')`；docstring L20 明示「d8 换算取北京墙钟（固定 +8）」 | PASS |
| ③ | `ann_date` NULL 排除（非当 0） | **双重保障**：扫描 SQL L82 `WHERE fi.ann_date >= ?` ⇒ NULL 在 SQL 层天然不入选；**另** L70-71 `_ANN_NULL_SQL = 'SELECT COUNT(*) FROM fin_indicator WHERE ann_date IS NULL'` ＋ L210 执行 ＋ L297 打印 `annNULL排除行数` ⇒ 显式计数不静默 | **PASS**（我方初判 FAIL 系只 grep `IS NOT NULL` 字面 —— **假失败已自纠**） |
| ④ | repr 全精度（无 round） | `round(` = 0 | PASS |
| ⑤ | 禁时间戳 | 无 `generated_at`/`datetime.now`/`time.time()` | PASS |
| ⑥ | `--universe-file` | L145 `ap.add_argument('--universe-file', …)` | PASS |
| ⑥ | `--recent-periods` | L148 `ap.add_argument('--recent-periods', …)` | PASS |
| ⑦ | 分叉行以 `basic_eps` 为准 | 源码含 `ist.basic_eps`（SQL L78 联表）＋ L299 打印 `分叉行数=%d(basic_eps取值)` | PASS |
| ⑧ | `income_statement` 缺失 ⇒ exit 2 | 源码含 `income_statement` 检测与 `return 2` | PASS |
| ⑧ | `eps` NULL 跳过＋计数 | **实读满足**：L215 `eps_null_rows = 0`／L236 `+= 1`／L297 打印 `epsNULL跳过行数=%d`；另设 `eps_nan_rows`（NaN 入面板会让下游比较全 False）与 `bad_code_rows`（键契约守卫） | **PASS**（我方初判 FAIL 系 grep `skip`/`skipped` 命名不匹配 —— **假失败已自纠**） |
| ⑨ | `--since` 公告日窗语义 | L143 `help='公告日扫描窗下界 YYYYMMDD（默认 %s；按 ann_date 过滤'` ＋ docstring L22 | PASS |
| — | 幂等 | 源码含 `emit_py` 输出与 sha 打印；委派回执双跑 sha256 一致 | PASS |

## 五、O5-⑤ 测试

| 项 | 结果 |
|---|---|
| `pytest tests/test_qmt_eps_snapshot.py`（新增件） | **rc=0 · 9 passed**（1.04s） |
| `pytest tests/test_qmt_qfq_snapshot.py`（姊妹 qfq 回归） | **rc=0 · 13 passed**（1.55s） |
| 合计 | **22 passed** —— 与委派回执「22 passed（新 9＋姊妹 13）」**逐项吻合** |

**独立性核对**：新增测试由委派方自写，**通过判定由我方 DSH 侧执行**（本表即证据）；关键路径判据（PIT 三元组／NULL 排除／repr 精度／禁时间戳／幂等）另有我方 §四 的**实读契约核对**独立兜底，**不经委派方自证**。

## 六、被测件漂移检测（O5 首尾双记）

| 文件 | O5 开始 | O5 结束 | 判定 |
|---|---|---|---|
| `scripts/export_eps_snapshot.py` | 16,717 B／`34c9a6d3c8fedc3f`／331 行 | 同 | **稳定** |
| `tests/test_qmt_eps_snapshot.py` | 20,034 B／`23eacf13733bd495`／392 行 | 同 | **稳定** |

⇒ **O5 全程被测件未变，本证据有效**。

## 七、O5 结论

**五步全过**（①范围 ②构建 ③静态 ④契约 13/13 ⑤测试 22/22），**可进入下一阶段**（wrapper EPS 消费层 ＋ 六产物 v1.8 重转）。

**方法论沉淀（本轮）**：契约核对**禁以单一字面 grep 命中数作判据**——本轮 2 条「FAIL」均为我方 grep 模式过窄（`IS NOT NULL` 字面 / `skip` 命名），实读后确认实现**更严谨**（SQL `>=` 天然排除＋独立 COUNT；`eps_null_rows` 命名＋三类计数打印）。**契约核对必须落到「实现语义」而非「字面模式」**（与记忆 `21fcdc3d`「测点位置须明示」、`8d4636f0`「受限查询为空≠不存在」同族）。

---

知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-10）
本轮沉淀：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制）
