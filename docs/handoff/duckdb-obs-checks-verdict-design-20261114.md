# 方案① 设计件：首周观测脚本 `checks` 判据重构（观测项与通过条件分离）

- **文档性质**：六步流水线第①步「书面方案」（`docs/*-design.md`）。**未过②审不得实施**。
- **起草会话**：duckdb 数据库升级执行会话（图节点⑲）
- **起草日期**：2026-11-14
- **被指问题来源**：总调度 2026-11-14 审核意见第 5 项——「`all(checks.values())` 中观测字段参与判定的缺陷，先形成问题、影响、纯增益分型与六步方案，**未经②审不改脚本**」
- **状态**：**待②审（独立审核）** —— 本件**不含任何代码改动**，脚本一字未动

> **知识反哺（R1 留痕）**
> `知识反哺：[命中] DB-2026-0002 质量审计关键词口径与计数单位（db / D3 / DB-2026-0002.md，2026-11-14）` —— 采纳：命中判据与**行数**口径必须与脚本源码逐字对应（`L135/L136/L144`），判据变更时口径说明须同批更新。
> `知识反哺：[命中] DB-2026-0003 观测脚本采样滑动窗口（db / D5 / DB-2026-0003.md，2026-11-14）` —— 采纳：本次重构**不得**触碰 top60 滑动窗口取样逻辑，仅动 `checks` 组装与 verdict 语义，避免把两类问题混在一笔改动里。
> `知识反哺：[无命中]` knowledge/kb-review 域（判据设计类）本轮检索面无相关条目，2026-11-14。

---

## 一、问题定义（先证实，再谈改）

### 1.1 事实（源码实证，`scripts/check_duckdb_firstweek_obs.py`）

`checks` 由 8 个键组装（L196-213），`verdict = "PASS" if all(checks.values()) else "FAIL"`（L215），`exit_code` 同源（L216）。

| 键 | 表达式（源码语义） | 性质 |
|---|---|---|
| `fatal_zero` | `fatal_hits == 0` | **通过条件**（缺陷为 0 才算过） |
| `wal_under_256mb` | `wal_mb < 256` | **通过条件** |
| `gate_pass_1_5_6` | `gate_rc==0 and "PASS 1.5.6" in gate_out` | **通过条件** |
| `daemon_alive` | forever 在位 **或** 有豁免 | **通过条件**（含豁免设计） |
| `daemon_absence_excused` | `occupancy_excused` | **观测项**：记录「豁免是否命中」；**无声明时恒 False** |
| `no_declaration_mismatch` | `not (inplace声明 ∧ 无forever)` | **通过条件**（不冲突才算过） |
| `once_running` | 存在 `mode=="once"` 进程 | **观测项**：记录「此刻是否有 once 轮在跑」；**无 once 轮时恒 False** |
| `no_qa_failure` | `qa_failed_count == 0` | **通过条件** |

### 1.2 缺陷陈述

> **`daemon_absence_excused` 与 `once_running` 是「记录事实」的观测项，却被并入 `all(checks.values())`，**
> **使「无任何实际缺陷」的时段同样判 FAIL。**

### 1.3 实测佐证（2026-11-14 16:18 现测，只读）

```
fatal_zero=True  wal_under_256mb=True  gate_pass_1_5_6=True  daemon_alive=True
daemon_absence_excused=False  no_declaration_mismatch=True  once_running=False  no_qa_failure=False
verdict=FAIL  exit_code=1  红项=['daemon_absence_excused','no_qa_failure','once_running']
```

此刻 **forever 实例在位（PID 39200）、FATAL 0、WAL 1.73MB、版本门 PASS 1.5.6、声明无冲突**——
即「供血、缺陷、版本、声明」四项全绿，仍判 FAIL。其中 `daemon_absence_excused=False` 与
`once_running=False` 是**纯粹的事实记录**（此刻无声明、无 once 轮），**不含缺陷含义**。

### 1.4 反例抑制（防止把问题说大）

- 11-10 00:22:30 那行的 FAIL **确有实质缺陷**（`daemon_alive=False` 真缺席、`no_declaration_mismatch=False` 真冲突），
  该类 FAIL 与本次重构**无关**，重构后仍应 FAIL。
- `no_qa_failure=False`（8 行命中来自 11-07 升级窗口前的日志）**也仍是实质红项**，重构后仍应 FAIL。
- ⇒ 本方案**不追求「让 FAIL 变 PASS」**，只追求「**FAIL 的构成说清楚**」。改后历史行是否翻绿，
  由验收阶段逐行双跑给出，**不得以「翻绿」为目标调参**。

---

## 二、改动范围（最小可逆）

### 2.1 拟改（一个函数内）

1. 把 `checks` 拆为两组：
   - `verdict_checks`（参与 `all()`）：`fatal_zero`、`wal_under_256mb`、`gate_pass_1_5_6`、`daemon_alive`、`no_declaration_mismatch`、`no_qa_failure`
   - `observations`（**不参与** `all()`，仅随 JSON/文本输出）：`daemon_absence_excused`、`once_running`
2. `verdict` / `exit_code` 改由 `verdict_checks` 计算。
3. **向后兼容**：`res["checks"]` 键集**保持不变**（8 键原样保留，含两类），另加 `res["verdict_checks"]` 与
   `res["observations"]` 两个新键；文本渲染同时打印「判定项」与「观测项」两段。

### 2.2 明确不改（防扩散）

- 不改日志取样（L124-126 的 top60 滑动窗口）——**另案**（见 §六 关联问题）。
- 不改命中判据字符串 `quality audit failed`、不改行数计数口径。
- 不改 `OBS_WINDOW_START`、不改 daemon 探测方式、不改占用声明语义分类。
- 不改历史证据文件任何已落盘读数。

### 2.3 影响面三审计（铁律 18）

| 审计项 | 判定 | 依据 |
|---|---|---|
| 是否影响项目其他功能 | **不影响** | 该脚本为独立只读观测器（`data/logs` + 进程表 + 门禁 CLI），无其他模块 import 它；`grep` 全仓引用仅文档 |
| 是否影响回测性能 | **不影响** | 不触主库、不参与回测链 |
| 是否影响回测精度 | **不影响** | 无引擎路径 |

### 2.4 纯增益分型判定

> **判定：`新增检测型` 的「判据重构子型」**（不是纯恢复型、不是纯性能型、不是数据修正型）。

理由与纪律：
- 该改动**会改变 verdict 可观察结果**（部分历史 FAIL 可能变 PASS），按铁律 18 属 **verdict 可被新证据改变** 的类别，
  **必须单独立项、单列声明，禁止与纯恢复型混办**。
- 因此**硬约束**：历史 14 行读数**原样保留**；改动后必须给出「旧 verdict / 新 verdict」**逐行并列对照表**，
  任一行翻绿须能逐项归因到「该行 ✗ 集中哪一项属观测项」。
- **禁止**为让某行翻绿而调整任何阈值或命中字符串。

---

## 三、验收标准（可机检）

1. **键集不破**：`--json` 输出仍含原 8 键；新增 `verdict_checks`、`observations` 两键。
2. **观测项不参与判定**：构造用例——把 `daemon_absence_excused`/`once_running` 置 False 而其余 6 项全真 ⇒ `verdict=PASS`；
   置 `no_qa_failure=False` 其余全真 ⇒ `verdict=FAIL`。两条用例均须落 `tests/`。
3. **实质缺陷仍 FAIL**：11-10 00:22 型（真缺席 + 真声明冲突）用例 ⇒ `verdict=FAIL`。
4. **逐行对照表**：14 行历史读数 × （旧 verdict, 新 verdict, 归因）三列，落 `docs/evidence/`。
5. **零回归**：既有 `tests/` 全绿；`--json` 与 `--evidence` 行为不变（后者仅追加行）。
6. **不改历史**：`git diff` 不得出现对既有证据文件已落盘读数的修改。

## 四、回退条件（预先写明）

出现以下任一 ⇒ **立即回退**（`git revert` 该提交），并按「判据缺陷」重新立方案：

1. 任一实质缺陷用例（§三.3）在新判据下变 PASS；
2. 原 8 键中任一键从 JSON 消失或被改名（破坏下游解析）；
3. 逐行对照表出现**无法归因**的翻绿（即某行 ✗ 集里没有任何观测项，却由 FAIL 变 PASS）；
4. 既有测试出现任何红项。

## 五、六步流水线（本件为第①步）

| 步 | 内容 | 状态 |
|---|---|---|
| ① | 书面方案（本件） | **已完成（2026-11-14）** |
| ② | 提交独立审核（ZCode / 审核席） | **待发起 —— 未过不得实施** |
| ③ | 实施（改脚本 + 补 2 条用例 + 生成对照表） | 阻塞于② |
| ④ | 验收（测试全绿 + 14 行双跑对照 + 证据落 `docs/evidence/`） | 阻塞于③ |
| ⑤ | 用户明确确认 | 阻塞于④ |
| ⑥ | 双仓推送并核对两远程一致 | 阻塞于⑤ |

**当前状态**：`BLOCKED(外部依赖/用户裁定)` —— 解除条件 = ②审通过 **且** 用户批准实施。
**在此之前脚本一字不改**；证据文件继续按现行判据产出 FAIL 读数（不美化）。

## 六、关联问题（同族但不并入本件，避免一笔改多事）

1. **取样分母是滑动窗口**：top60 会让老命中文件滑出，导致「命中数下降」被误读为修复（已实证）。
   属观测**语义说明**问题，倾向以文档＋输出内嵌警示解决，属另一案。
2. **占用声明陈旧**：`data/daemon_occupancy.json` mtime 停在 2026-11-11 05:56:42，
   `operation` 仍写 PID 36372 而实际 forever 为 39200。属**声明维护**问题（他线域），本会话只观测不介入。
3. **`daemon_absence_excused` 语义命名**：名字读起来像「缺席被豁免了（好事）」，实为「豁免是否命中」。
   若②审同意，可在同笔改动中**仅改注释与输出标签**（不动键名），避免下游解析破坏。

## 七、本件不做的事（防越权）

- 不改脚本（一字未改）。
- 不重跑历史窗口、不改历史读数、不删任何证据行。
- 不因本方案推进而放松 11-17 用户下发闸。
