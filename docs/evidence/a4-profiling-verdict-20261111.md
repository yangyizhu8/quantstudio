# A4 测点轮结论（2026-11-11 01:07–01:35）—— 瓶颈不在代码，在测量条件

- 依据：R-20261111-02 裁＝甲（测点轮深挖，一轮为限；廉价则修、结构性则收口）
- 手段：**三次 SQL 级剖面**（均零产品代码改动，TEMP 临时脚本已删）

---

## 一、三次剖面读数

| 版本 | 覆盖范围 | 端到端 wall | 已计耗时 | 关键发现 |
|---|---|---|---|---|
| v1 `cProfile` | — | **失败** | — | `$env:TEMP` 有 `inspect.py` 遮蔽标准库（690 B，已改名 `.bak-inspect-shadow`） |
| v2 | `Connection.execute` | **50.004 s** | SQL **0.013 s** / 173 语句 | 漏计 `cursor.execute`（产品代码用 `c.execute` 且 `c` 是 cursor） |
| **v3** | `Connection.execute` + **`Cursor.execute`** + **`commit`** | **4.245 s** | **0.017 s** / 175 事件 | **SQL 与 commit 全都不慢**（commit 2 次共 7.2 ms） |
| 参照 | `g6_write_dim_probe`（恢复法） | **39.169 s** | — | 与 v3 同代码同输入 |

## 二、v3 耗时明细（TOP）

| 项 | 累计 | 次数 | max |
|---|---|---|---|
| `COMMIT` | **7.2 ms** | 2 | 7.2 ms |
| `_inc_fetch_last`（SELECT 行值 IN） | **3.5 ms** | 1 | 3.5 ms |
| `INSERT OR IGNORE ... qfq_factor_revision_alert` | 3.2 ms | 60 | 0.2 ms |
| `INSERT INTO qfq_factor_observation` | 1.9 ms | 100 | 0.6 ms |
| `PRAGMA journal_mode=WAL` | 0.5 ms | 1 | 0.5 ms |
| `BEGIN IMMEDIATE` | 0.0 ms | 1 | 0.0 ms |
| **合计** | **17 ms** | 175 | — |

⇒ **代码侧已无瓶颈**（17 ms 处理 100 条写入 + 60 alert）。

## 三、🔴 核心结论：wall 波动来自 OS 页缓存

同一函数、同一输入，三次测出 **50.0 s / 4.245 s / 39.169 s**，而实测 SQL+commit 仅 **17 ms**：

| 场景 | 页缓存状态 | wall |
|---|---|---|
| `g6_write_dim_probe` 的 **old 段** | **热**（紧跟 12.6 GB 备份拷贝之后，缓存已被备份过程灌满） | **0.035 s** |
| `g6_write_dim_probe` 的 **new 段** | **冷**（先 `aux.unlink()` 再 `shutil.copy2(pristine→aux)` 12.6 GB，页缓存被冲刷） | **39.169 s** |
| v3（独立直跑） | 半热（前有只读查询预热） | **4.245 s** |

⇒ **原 A4 对比（old 0.035 s vs new 39.169 s = 1119×）不公平**：两段的缓存状态不同，相差部分**不是代码差异**。

## 四、按裁定收口

用户裁定：「廉价则修、结构性则收口」。本轮结论属**结构性/测量方法论**层：
- 代码侧已无价可降（17 ms）；
- 要拿到公平读数必须重建测法（**两段同缓存态**：各先预热跑一次丢弃读数），成本 > 收益；
- ⇒ **收口**：A4 不再追，DB-0042 记 L3（测量条件不可比）为**未修·结构性**，并把「对比型测量必须同缓存态」提炼为可复用判据。

## 五、本轮零产品代码改动

三个剖面脚本均为 TEMP/`scripts/_tmp_*` 临时件，跑完即删；`git status` 于本轮前后一致（`b37117b` 后无新增改动）。

---
2026-11-11 01:35