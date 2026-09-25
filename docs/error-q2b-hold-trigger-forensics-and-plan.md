# Q2 第二笔：hold 确切触发点取证 + 方案骨架（2026-09-25）

- 状态：**取证完成，方案骨架呈审**｜归属：客户运维会话 1
- 前置：Q2 第一笔（`646cf5b`，artifact error 分类）已实施验收
- 案件：jabberwock 周期/水位 hold

## 1. 取证结论：hold 的确切触发链（逐段有代码依据）

| # | 环节 | 证据 |
|---|---|---|
| ① | 大表取数走 **Parquet 分片**：`mcp_adapter.fetch_table` → `export_dataset` / `create_export_job` → 逐 shard `get_artifact` → 落 Raw Landing → aligner → validator → writer | `mcp_adapter.py:16/168/236`；`client.py:958`（shard 级 `get_artifact`） |
| ② | **MCP 唯一源下因子 refresher 短路**（非 degraded）：因子随行情任务**常态注入** | `daemon.py:363-366`「MCP 唯一源：因子随行情任务常态注入，refresher 短路（非 degraded）→ return False」 |
| ③ | 故 MCP 路径下 `degraded` 只能来自**因子注入/取数链本身的失败**；且 `_refresh_factors` 有**过宽兜底**：任何异常 → `return True`（degraded） | `daemon.py:378-382`（`res.degraded` → WARNING「因子刷新 degraded…→ 水位 hold」）、`:387-390`（`except Exception … → degraded=True（水位 hold）`） |
| ④ | `detector_degraded=True` → **`passed=False`** + reason 固定文案 → `_commit_or_hold_watermarks(passed=False)` → **四价格表统一置 `status='held'` + `hold_reason`** | `qfq_resident_orchestrator.py:1426-1429`（reason=`detector_degraded: 因子刷新失败，价格水位强制 hold`）、`:1434`（调用）、`:828-835`（held 分支） |

### 1.1 关键放大点（客户「永久 hold」的机理）

1. **全表连坐**：`_commit_or_hold_watermarks` 内 `passed` 是**单值**，对**全部四价格表**统一决定（`:818-835`）
   ⇒ 一处因子/取数失败 → **四表水位全部 hold**；
2. **无死信出口**：`dead_letter_max` **默认 0**（`qfq_orchestrator_types.py:372`）⇒ 死信机制**默认关闭**；
   `dead_letter_max_attempts` 默认 8（`:419/479`）但**未被该路径消费**（本取证未见其参与 hold 决策）；
3. **失败可重复**：若根因是**持续存在的 artifact error**（如服务端对该 shard 持续返回 error 载荷），
   则每轮 refresh 都 degraded → **每轮都 hold** ⇒ 表现为「水位 hold、周期 hold」直至根因消失。

### 1.2 与第一笔的关系（同源邻接）

客户机 hold 的**最可能根因**即第一笔修复的 artifact error（`get_artifact` 载荷级 error）——
它使因子/取数链抛错 → 被 ②③ 兜成 `degraded=True` → 四表 hold。
**故**：第一笔（分类 + 重试）**直接削减**该 hold 的发生率；第二笔要补的是**当它仍然发生时，系统不该无限期 hold**。

## 2. 第二笔方案骨架（四候选，按类型标注）

| 案 | 内容 | 类型判定 | 是否需用户裁定 |
|---|---|---|---|
| **① degraded 分类留痕** | 把 `res.error` / 异常**分类**（可重试 / 确定性）+ 涉及表/码写入 `hold_reason` 与审计行（现状仅 `err=…` 一句） | **纯恢复型 + 新增检测型** | 否（可先行） |
| **② 收窄过宽兜底** | `except Exception → degraded=True`（`daemon.py:387-390`）改为：**仅已知因子链异常**置 degraded；未知异常按原语义上抛/分类，不静默 hold 四表 | **行为变更**（更正确，但改变异常语义） | **需裁定**（附影响面） |
| **③ 死信 / 降级通道** | 连续 N 轮**同因** 失败 → 记 dead letter（复用 `dead_letter_max_attempts`）+ 显式告警；提供**运维出口**（不再无限 hold 无信号） | **新增检测型 + 配置化**（默认值=现行为，逐级放开） | 默认值需裁定 |
| **④ 按表隔离 hold** | 只 hold **受影响的价格表**，其余表正常推进 | **非纯性能型**（牵动一致性门禁语义） | **需用户裁定**（须先论证四表一致性约束） |

**推荐推进序**：①（纯增量、零风险）→ ③（配置化，默认不变）→ ② / ④ 单独立项（附一致性论证）。

## 3. 验收与回退（骨架）

- 验收：① 断言 `hold_reason` 含分类与上下文（表/码/error 原文）；③ 断言「连续 N 轮同因 → 死信 + 告警」且**默认配置下行为与现状逐项一致**；② / ④（若批）须附**黄金/一致性对照**与差异逐项归因；
- 回退：精确 hunk 反向（禁整文件 `checkout`）；实施前建零副作用回退点；
- 铁律前置三问（其他功能 / 回测性能 / 回测精度）逐案填写。

## 4. 待审 / 待裁

1. 骨架与推进序是否认可（① → ③ → ②/④ 单列）；
2. ② 与 ④ 是否本次批内推进（或先只做 ①③，把 ②④ 交用户裁定）；
3. ③ 的「连续 N 轮」阈值与默认值（建议：默认关闭死信、仅告警；N=3 可配）。
