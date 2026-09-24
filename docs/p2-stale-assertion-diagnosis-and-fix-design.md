# P2 性质判定与处置方案：`test_validator_is_single_chokepoint` 既有红

- **日期**：2026-09-23
- **性质**：六步流水线 **步骤1+2（溯源定谳 + 处置方案）**，呈审
- **对象**：`tests/test_pit_filter.py::test_validator_is_single_chokepoint`（本批 10 套件终验中唯一 failed）
- **登记**：`issue_registry` **S2-10**（用户 2026-09-23 裁定「另起追单归因」）

---

## 一、结论（一句话）

**不是「契约漂移应收口写入点」，而是「旧断言过期」** ——
**两面写入是设计内多入口**（有显式代码契约 + 权威契约测试 + 配置事实三重支撑）；
红的是 `test_pit_filter` 里一条**自 2026-08-03 起即过期的硬编码断言**。
⇒ **处置方向 = 修断言（对齐权威契约），不是收口写入点。**

---

## 二、现象与直接原因

| 层 | 事实 |
|---|---|
| 现象 | 该用例断言 `len(write_lines) == 1`，实测 **2** 处 → FAIL（本批 10 套件 128 例中唯一红） |
| 直接原因 | 断言硬编码「全管线 `writer.write` 仅 1 处」 |
| 代码缺陷 | **无**（生产代码两面写入合法，见 §四） |
| 业务边界 | 该断言写于「单一写入入口」的旧设计假设下；passthrough 通道引入后假设失效 |
| 隐性风险 | 该红长期存在（7 周）未被处理 → **可能掩盖真实回归**（真出现第三条裸写入时，因基线已红而无信号） |

---

## 三、溯源（漂移时间线，git 实证）

| 时点 | commit | 事实 |
|---|---|---|
| 2026-07-21 | `6b7f26b`（Initial backup） | daemon.py 内 `writer.write` **恰 1 处**（L1233）⇒ **当时该断言成立** |
| **2026-08-03** | **`d960d33`**（feat(mcp): 线2 全量表配置 + codex审计修复 + **passthrough 基础设施**） | 新增 passthrough 通道 ⇒ `writer.write` 变 **2 处**（L742 passthrough + L1860 stamp）⇒ **断言自此过期** |
| 2026-09-06 | `d2b0913`（任务一 #16 通道契约显式化） | 新增权威契约测试 `tests/test_writer_channel_contract.py`，**明确断言「应恰 2 处」** + 代码内写入通道契约声明（daemon.py:1331-1335） |

⇒ **漂移窗口 ≈ 7 周**；期间**权威契约测试一直绿**（现跑 5 passed），
即「正确的契约」有测试守护，「过期的断言」无人清理——**两条规格并存**。

---

## 四、判定依据：两面写入为什么是设计内多入口

### 4.1 代码内显式通道契约（daemon.py:1331-1335）

```
# 【PASSTHROUGH-CHANNEL 通道契约声明】（任务一 A2.2，2026-09-06）：
# 全量覆盖语义（CREATE OR REPLACE TABLE）/ 无增量水位（不推进 source_watermark）/
# 独立于 stamp 通道（不走 aligner/validator upsert 与 QFQ 自检——快照类表专用）。
# 写入通道契约：全管线 writer.write 仅此 passthrough 通道与 _stamp_and_write
# （stamp 主通道）两处合法——禁止第三条裸 write 路径
# （tests/test_writer_channel_contract.py 锁定）。
```

### 4.2 权威契约测试（`tests/test_writer_channel_contract.py`，5 passed）

| 契约 | 断言 |
|---|---|
| 契约 1 | `writer.write` 调用点**应恰 2 处**（stamp 方法体 + passthrough 区）；**出现第 3 处裸写入 = 通道违规 FAIL** |
| 契约 2 | `_stamp_and_write` 体内**必须**含 `_qfq_invariant_after_align`（防线①不被绕过） |
| 契约 3 | passthrough 通道**不得**推进 `source_watermark`（全量覆盖语义锚） |

### 4.3 配置事实（ternary confirmation）

```
config/profiles/mcp_only/collector_tasks.json：
  总任务 88；passthrough=true 任务 69
  69 张 passthrough 表 **不含任何 canonical 大表**（stock_daily / etf_daily / stock_minutes / etf_minutes 均无）
```

⇒ 两面写入服务**两类语义不同的表**：
- **stamp 主通道**：canonical 表（有 schema 契约、走 aligner→validator→upsert、有 QFQ 自检、推进水位）；
- **passthrough 通道**：69 张快照/原样表（DuckDB 表名列名=QuestDB 原样，**无 canonical 契约**，全量覆盖、不推进水位）。

### 4.4 若强行收口（并入 `_stamp_and_write`）的后果

| 方案 | 后果 | 判定 |
|---|---|---|
| passthrough 走 `_stamp_and_write`（含 validator） | 69 张非 canonical 表跑 canonical 校验规则 ⇒ 必大面积误拒/整批失败；且会推进水位（与「无增量水位」语义直接冲突） | **❌ 破坏性** |
| 给 `_stamp_and_write` 加 `passthrough` 分支绕开校验 | 把「单一 chokepoint」变成**内部分派的巨型方法**；且 `_qfq_invariant_after_align` 与水位推进需条件化 ⇒ 防线①与语义锚**更难守护** | **❌ 更差** |

⇒ **两面写入是架构上必要的分离**：一个走 canonical 契约、一个走原样覆盖契约。
**收口不是"更干净"，而是"错"**。

---

## 五、处置方案（呈审）

### 方案（推荐）：修过期断言，对齐权威契约 —— **不改生产代码**

| 项 | 内容 |
|---|---|
| **改动面** | 仅 `tests/test_pit_filter.py::test_validator_is_single_chokepoint` 一条用例 |
| **生产代码** | **零改动**（daemon.py / writers.py 均不动） |
| **新断言口径** | ① `writer.write` 调用点**恰 2 处**，且**分布于两合法通道**（passthrough 上下文 ∋ `passthrough=True`；stamp 方法体内 ∋ `_qfq_invariant_after_align`）；② 保留该用例的**原始意图**——`validator.validate` ≥4 处 + `_stamp_and_write` 调用 ≥4 处（canonical 路径一律汇聚）；③ **不复制** `test_writer_channel_contract` 的完整逻辑，而以其为**权威引用**（避免两处规格再次漂移） |
| **防回归** | 断言失败信息中显式指向权威契约测试与通道声明位置；并加一条「第三条裸写入即 FAIL」的语义（与权威契约同口径） |
| **纯增益** | 不改任何行为语义；把「长期红」转为「真实门禁」——**今后真出现第三条裸 write 会立刻红**（当前因基线已红而无信号，属实质防护缺口） |

### 备选（不推荐）：收口写入点
见 §4.4——破坏 passthrough 语义与防线①，**不建议**；若用户坚持收口，须另立框架层方案并重新论证 69 张表的校验与水位语义。

---

## 六、验收要点

| 层 | 判据 |
|---|---|
| 功能 | `test_pit_filter.py` 全绿；**权威契约测试 `test_writer_channel_contract.py` 仍全绿**（两处规格一致） |
| 语义 | 断言失败信息可自解释：指明两通道 + 指向权威契约测试 |
| 防回归 | 人为在 daemon.py 插一条假 `writer.write` ⇒ 两条测试**都**红（证明门禁有效） |
| 回归 | 本批相关套件（含 `test_pit_filter` / `test_writer_channel_contract` / 10 套件）全绿；**既有红清零** |
| 边界 | 不改生产代码（`git diff --stat` 仅测试文件一行用例） |

---

## 七、质量判据

| 判据 | 门槛 |
|---|---|
| 根因彻底 | 漂移时点定谳（`d960d33`，2026-08-03）+ 权威契约确认（`d2b0913`，2026-09-06）+ 配置事实（69/88 任务）三重支撑 |
| 不做破坏性修复 | **不**收口写入点（§4.4 已论证破坏性）；仅修过期断言 |
| 无回归 | 生产代码零改动；权威契约测试保持全绿 |
| 防复发 | 断言指向单一权威契约（避免两处规格并存再现）；插假写入双测试齐红验证 |

---

## 八、回退条件

- 若修断言后权威契约测试转红 ⇒ 立即回退（说明两处规格并不等价，需重新归因）；
- 若插假写入验证时两条测试未齐红 ⇒ 回退并重做门禁设计；
- 生产代码若在实施期被并行会话改动 ⇒ 暂停并按共享核心文件纪律重核（本方案本不改生产代码，故风险极低）。

---

## 九、请裁定

1. **处置方向**：采纳「修过期断言」（推荐）还是「收口写入点」（备选，需另立框架方案）；
2. 若采纳修断言：**是否走完整六步**（本项为测试契约对齐、生产代码零改动——我判断可按**轻量路径**：方案（本件）→ 实施 → 验收 → 你确认，免独立审计；如您要求完整六步，我照走）；
3. 实施时机：本线在途 8 笔随下批，本修复是否**并入下批**或**单独一笔**。