# 工作包 TD-D2 任务书：因子库 legacy → mcp-gen1 单一路由切换

> 版本：v1.1（2026-08-15；v1.0 审核 → 按 R-1~R-6 修订）
> 执行方：ZCode（本地实施，不提交 GitHub，等用户确认）
> 来源：工作包 D 复审识别的高危技术债（进度报告 v6.7.53 §3.2 TD-D2）
> 定位：**⑤ C-6 水位释放的前置待办**（切换必须在 ⑤ 之前完成，否则 front 写入锚与
> 权威源分叉）；可与 ⑤ 其它前置并行推进。
> 关联：`qfq_aux_paths.json`（generations 路由）、`qfq_aux_router.AuxDbRouter`、
> 工作包 D 已建的 `_qfq_aux_db_routed()`。

### v1.1 修订记录（相对 v1.0，按审核意见）

| 编号 | 级别 | 修订 |
|---|---|---|
| R-2 | 强制 | 路由开关唯一锚定 **active cutover 已激活 + ⑤ 释放门**；仅配置声明（generation_mode=dynamic / source_generation=mcp-gen1）**或仅 active cutover 存在**均不得切换。**关键事实（v1.1 核实）**：`b6_formal_20260807_v2` **已是 active**（v6.7.43，activated_at 2026-08-07），而 `resolve_runtime_identity` 见 active 即返回世代（`qfq_cutover.py:81-93`）——即"仅锚定 active cutover"在当前状态就会立即误切空 gen1 库，必须叠加 ⑤ 释放门。测试 3 补当前真实态用例 |
| R-1 | 重要 | 删除 `role` 参数（死参数，诱导分叉）；或仅日志标注。本版删除 |
| R-3 | 重要 | 收敛清单补 refresher 链路：`QFQFactorRefresher(aux_db=orch.aux_db)`（`daemon.py:319`）；grep 审计扩围 `aux_db=` 传参链来源唯一 |
| R-4 | 重要 | 方案 A 判定标准：gen1 灌数后历史因子深度覆盖与 legacy 等价（抽样 code 最早因子日不晚于 legacy 同 code），否则回退方案 B |
| R-5 | 低 | 分支 B 全量重锚必须在 ⑤ 释放**之前**完成（硬约束，非"协调"） |
| R-6 | 低 | 分支 B 窗口策略**首选顺序**（先重锚→再切路由→再开防线）；告警抑制仅兜底且须定义起止 |

---

## 0. 背景与目标

### 0.1 问题

当前 QFQ 因子（adj_factor / fund_adj）的**全部写入与读取**都指向 legacy 库
`data/qfq_aux.db`（由 `qfq_reanchor_schema.aux_db_path()` 固定推导：
`main_db.parent / "qfq_aux.db"`）。而 ⑤ C-6 水位释放后 **mcp-gen1
（`data/qfq_aux_mcp_gen1.db`）成为权威因子源**——若不同步切换，会出现：

- 写入锚（legacy）与防线 2.1/3 监测的权威源（gen1）**分叉**；
- front 增量写入用 legacy 锚，而权威因子在 gen1，防线 1 自洽（同源）但整体锚错误。

### 0.2 目标

把"三处同步切换"（`_load_qfq_global_snapshot` / `mcp_adapter._inject_adjfactor` /
`_qfq_align_aux_path`）**收敛为单一配置驱动路由入口**——⑤ 释放时**切配置、不切代码**，
分叉窗口从"改代码+测试+部署"压缩为"改一行配置"。

---

## 1. 已核实事实（2026-08-15，代码行号）

| # | 事实 | 证据 |
|---|---|---|
| 1 | `aux_db_path()` 固定推导 legacy：`main_db.parent/qfq_aux.db`（主库名非 qfq_aux.db 时） | `qfq_reanchor_schema.py:164-178` |
| 2 | 读取锚 `_load_qfq_global_snapshot` 用 `aux_db_path`（legacy） | `daemon.py:842-846` |
| 3 | 写入锚对齐 `_qfq_align_aux_path` 用 `aux_db_path`（legacy） | `daemon.py:2273-2286` |
| 4 | **MCP 因子注入 `mcp_adapter._inject_adjfactor` 也写 legacy** | `mcp_adapter.py:1156/1449/1905` |
| 5 | `qfq_maintenance` 因子拉取默认也写 legacy（db_path 推导同规则） | `qfq_maintenance.py:58-60` |
| 6 | **当前无任何代码路径写入 gen1**：`qfq_aux_mcp_gen1.db` 0 行（adj_factor/fund_adj 均空） | 2026-08-15 实测 |
| 7 | 工作包 D 已建 `_qfq_aux_db_routed()`（orch.aux_db 优先 → 回退 legacy），仅用于**防线 2/3 监测**，未用于写入锚 | `daemon.py:2288-2303` |
| 8 | 因子双源系列差 ~1.6%：MCP 系列 latest≈1.9495 vs tushare 系列≈1.9816 | README §MCP 数据源已知限制 |

**关键推论（事实 4+6）**：TD-D2 不只是"三处读取切换"——**gen1 因子灌数机制本身缺失**，
必须先设计"因子写向 gen1 的路径"，否则切换后 gen1 永远是空库。

---

## 2. 设计要求（核心：单一路由入口收敛）

### 2.1 统一路由函数（R-2 强制 + R-1）

新增（或重构）一个**唯一**的 aux 路径解析入口：

```python
def _qfq_aux_path(self) -> Path:
    """统一 QFQ 因子库路由：写入锚/读取锚/防线监测全部走这里（无参数）。

    切换唯一开关（R-2，全部条件满足才返回 gen1）：
      ① active cutover 记录存在且已激活（resolve_runtime_identity(require_active=True)，
         禁止仅凭配置声明 generation_mode=dynamic / source_generation=mcp-gen1 切换——
         当前 mcp_only 配置已是 dynamic+mcp-gen1，但 ⑤ 未释放，必须仍走 legacy）；
      ② ⑤ 释放门通过（watermark_release_authorized=true 的授权 manifest 已生效，
         或实现者核实后的等价可查询信号——见步骤 2 前置核实）。
    任一条件不满足 → 一律返回 legacy（data/qfq_aux.db）。

    ⚠️ 关键事实（v1.1）：当前 active cutover b6_formal_20260807_v2 已存在
    （v6.7.43，activated_at 2026-08-07），resolve_runtime_identity 见 active 即返回
    世代（qfq_cutover.py:81-93）。因此条件①在当前状态为真，**必须叠加条件②**，
    否则路由会立即切到空 gen1 库（0 行因子）→ 写入锚指向空库 → align fail-fast
    全线拒绝 / 防线 1 全量 skip。实现后必须用「当前态」验证仍返回 legacy。
    """
```

### 2.2 全部调用点收敛（消除"多路径各写一遍会漏"的教训模式）

以下点全部改为调用 `_qfq_aux_path()`，**禁止任何直接 `aux_db_path(main_db)` 推导**：

| 调用点 | 现状 | 改为 |
|---|---|---|
| `daemon._load_qfq_global_snapshot` | `aux_db_path` | `_qfq_aux_path()` |
| `daemon._qfq_align_aux_path` | `aux_db_path` | `_qfq_aux_path()`（或直接删除，并入统一函数） |
| `daemon._qfq_aux_db_routed` | 独立实现 | 并入 `_qfq_aux_path()`（写入锚与防线 2/3 监测同源，消除分叉） |
| `mcp_adapter._inject_adjfactor` | `aux_db_path` | `_qfq_aux_path()`（注入目标 = 权威库） |
| `mcp_adapter` 其它 `aux_db_path` 引用（1449 等） | legacy | 同上 |
| `qfq_maintenance` 写路径 | legacy 推导 | 经 daemon 注入的路由路径（不直接推导） |
| **`daemon._qfq_refresh_factors` → `QFQFactorRefresher(aux_db=orch.aux_db)`（R-3）** | `orch.aux_db`（已跟随路由） | **纳入审计**：确认 `orch.aux_db` 的唯一来源 = `_resolve_dynamic_identity`（= `_qfq_aux_path()` 同源），且 refresher 写目标与其一致 |

**grep 审计测试（R-3 扩围）**：
1. 禁止 `aux_db_path(` 直引（代码里不存在第二处路径推导）；
2. `aux_db=` 传参链来源唯一（所有 `aux_db=` 实参只能来自 `_qfq_aux_path()` / `orch.aux_db`，且二者同源）。

**验收语义**：切换后"写因子到哪、读锚从哪、防线 2/3 监测哪、refresher 刷到哪"
四者在同一路径上，代码里**不存在第二处** aux 路径推导。

### 2.3 ⑤ 释放时的切换动作（切配置不切代码）

1. 确认 gen1 因子已灌数（§3 步骤 1，R-4 判定标准）；
2. `qfq_aux_paths.json` 的 `generations.mcp-gen1` 已指向 gen1 库（现状已是）；
3. **⑤ 释放门通过**（R-2：watermark_release_authorized=true 授权生效）→ 路由函数
   自动返回 gen1（此时 active cutover 已存在 + 释放门通过，条件①+②齐备）；
4. 若需回退：撤销释放门（或 cutover 状态回退）→ 路由自动回 legacy。

---

## 3. 实施步骤

### 步骤 1（前置中的前置）：gen1 因子灌数机制

**现状缺口**：无代码路径写 gen1（事实 6）。

**设计选择（二选一，先核实再定）**：

- **方案 A：注入目标直切（实为冷启动式全量注入）**——`mcp_adapter._inject_adjfactor`
  在路由收敛后自然写向 gen1（当释放门通过时）。**判定标准（R-4）**：gen1 灌数后
  **历史因子深度覆盖与 legacy 等价**——抽样 20+ code，其 gen1 最早因子日**不晚于**
  legacy 同 code 的最早因子日（防线 1 自洽需查任意历史 bar_day）；深度覆盖不达标
  → 回退方案 B。复用 README 既有"因子冷启动（未覆盖 code → 全历史导出注入）"
  机制对 gen1 跑一次全量。
- **方案 B：legacy → gen1 迁移脚本**——一次性把 legacy 因子表复制/迁移到 gen1
  （复用 `frontfix` 类脚本经验，只迁移 adj_factor/fund_adj 两表）。

**执行前必须回答的问题**（见 §5 同值性分叉）：
- gen1 因子来源是 MCP 注入（系列 latest≈1.9495）还是 legacy 复制（可能混 tushare 系列）？
- 两者同 code 因子值是否一致？

### 步骤 2：单一路由入口实现（§2.1/2.2）

**前置核实（步骤 2 第一件事）**：确认"⑤ 释放门"的可查询信号——`watermark_release_authorized`
当前 pin 在授权 manifest（`qfq_formal_authorization.py:306`，release 是单独未来授权）。
实现者须核实：释放后该信号在主库/配置的**可判定形式**（如 handoff 落库字段、或
`qfq_watermark_intent` 状态），并写入实施记录；若不可查询，则退化为"显式配置开关
（qfq_aux_paths.json 增加 released: true）"，同样满足"⑤ 释放时切配置不切代码"。

### 步骤 3：测试

- 路由一致性：
  - **当前真实态（R-2 核心用例）**：dynamic 配置 + **active cutover 已存在**（b6_formal_20260807_v2）+ ⑤ 未释放 → 断言**全部点仍指 legacy**（防止"仅 active cutover 即切"误判——v1.1 核实 active cutover 已为真）；
  - dynamic 配置 + 无 active cutover → 仍 legacy（R-2 原用例）；
  - dynamic 配置 + active cutover + ⑤ 释放门通过 → 全部点指向 gen1；
  - 断言**无第二处路径推导**（grep 审计：禁 `aux_db_path(` 直引 + `aux_db=` 传参链来源唯一，R-3）；
- Phase 3 fail-fast 行为不变（无快照仍 raise）；
- 防线 1/2.1/3 + S1 计数在两种路由态下正常；
- `qfq_selfcheck_log` 写入位置随路由（batch_audit.db 不变）；
- refresher 链路（R-3）：mock 释放门两态，断言 `QFQFactorRefresher.aux_db` 与
  `_qfq_aux_path()` 指向一致。

### 步骤 4：切换执行（⑤ 前置，用户授权后）

按 §2.3 + §4 复验清单执行。

---

## 4. 复验清单（切换前后）

**切换前**：
- [ ] gen1 库因子灌数完成：`adj_factor`/`fund_adj` 非空；
- [ ] gen1 与 legacy 同 code 因子**抽样对比一致**（或记录差异并走 §5 分支 B）；
- [ ] 路由函数在两种态下指向正确（测试 3 覆盖）。

**切换后**：
- [ ] **防线 1 对 gen1 锚抽样 bad==0**（工作包 D 复审遗留复验项，审核指令明确要求）；
- [ ] 防线 2.1 在 gen1 实跑（缺日/跳变/突增正常，交叉源 mcp_only 下禁用态记录）；
- [ ] 防线 3 黄金行在 gen1 实跑：**expected 按新锚重算**——S2 只在 reanchor
      committed 时刷新，**无除权事件的 code 不触发**，需评估黄金行是否手动重锚定
      （首版黄金行 159995 若因子无变化则 expected 不变，可跳过；有变化须手动更新）；
- [ ] 口径 B 与 `_qfq_aux_path()` 指向**一致**（重锚自洽与监测同库断言）；
- [ ] 回归：Phase 3 fail-fast 行为不变；`qfq_selfcheck_log`/S1 计数正常；
- [ ] 存量 front 与 gen1 锚自洽性确认（§5 分叉处理结果）。

---

## 5. 同值性分叉点（必须回答，任务书核心决策）

**问题**：legacy→gen1 切换瞬间，存量 front 的锚语义——
存量 front 按 legacy 锚计算；切 gen1 后若 gen1 因子 ≠ legacy（世代重算），防线 1
只管新写入行（增量自洽 ✅），但**存量 front 与新锚不自洽**，会触发防线 2.1/黄金行
误报吗？

**分支 A：gen1 因子与 legacy 同值**（方案 B 迁移，或 MCP 注入值与 legacy 一致）
- 存量 front 与新锚自洽 ✅，无需全量重锚；
- 切换 = 纯路由变更，风险最低。

**分支 B：gen1 因子为 MCP 权威系列，与 legacy（混合系列）不同值**（差 ~1.6%，事实 8）
- 存量 front（legacy 锚）与 gen1 新锚**不自洽**；
- **必须伴随一次全量重锚/rebase**（类似 Phase 2 的 14,416,065 行重算，正确全局因子
  基准）——把存量 front 全部按 gen1 锚重算；
- **时序硬约束（R-5）**：全量重锚**必须在 ⑤ 释放之前完成**——释放后增量用 gen1
  锚写、存量仍是 legacy 锚，不一致面每日扩大；
- **切换窗口策略（R-6）**：**首选顺序方案**——先重锚存量（gen1 锚）→ 再切路由
  （释放门）→ 再确认防线全绿（重锚彻底则防线自然无报，无需新增告警抑制机制）；
  告警抑制**仅作为顺序无法执行时的兜底**，且必须定义明确起止（如"重锚执行窗口内
  防线 2.1 跳过存量比对"），禁止无界抑制。

**核实方法**（步骤 1 产出）：完成 gen1 灌数后，抽样 20+ code 对比 legacy/gen1 同
code 因子值 → 一致走分支 A，不一致走分支 B。

**决策记录要求**：分支选择 + 依据写入实施记录，禁止跳过。

---

## 6. 风险与回退

| 风险 | 影响 | 缓解/回退 |
|---|---|---|
| **⑤ 前误切换（R-2）**：仅凭 active cutover 已存在（b6_formal_20260807_v2）即切 gen1 空库 | 写入锚指向空库 → align fail-fast 全线拒绝 / 防线 1 全量 skip | 路由唯一开关 = active cutover + **⑤ 释放门**；测试 3 当前真实态用例兜底 |
| gen1 灌数机制缺失导致切换无法执行 | ⑤ 阻塞 | 步骤 1 独立立项先行；方案 A/B 提前定（R-4 判定标准） |
| 分支 B 需全量重锚 | 大动作 | 复用 Phase 2 frontfix 经验（分批/备份/验收）；**硬约束：⑤ 释放前完成（R-5）** |
| 切换瞬间防线误报 | 告警疲劳/假阻断 | **首选顺序方案（R-6）**：先重锚→再切→再确认防线；抑制仅兜底且定义起止 |
| 路由收敛遗漏第二处推导 | 部分路径仍 legacy | 测试 3 的 grep 审计（禁 `aux_db_path(` 直引 + `aux_db=` 传参链唯一，R-3） |
| 黄金行 expected 未随锚更新 | 启动误报 | §4 复验项 + S2 机制说明（无除权 code 手动重锚定评估） |
| 释放门信号不可查询 | 路由无法判定 | 步骤 2 前置核实；退化为显式配置开关（`qfq_aux_paths.json` 加 `released: true`） |

---

## 7. 交付物

1. 统一路由函数实现 + 全部调用点收敛 diff（§2）；
2. gen1 灌数机制（方案 A 或 B）实现 + gen1 因子非空证据；
3. 测试（路由一致性 + fail-fast 不变 + 防线/S1 正常）；
4. 同值性抽样对比结论 + 分支决策记录（§5）；
5. 复验清单执行证据（§4 逐项）；
6. 若分支 B：存量 front 全量重锚执行记录（分批/备份/验收，复用 Phase 2 经验）；
7. 完成报告 + 进度报告更新。

---

## 8. 铁律与合规

1. **性质**：改 `daemon.py`/`mcp_adapter.py`/`qfq_maintenance.py` = 数据适配层/
   管线内核改动，适用**框架层修复铁律**——实施前用户确认，Git 同步 post-repair
   确认 + README/docs 同步；
2. **启用时序**：本工作包是 **⑤ C-6 释放前置**——切换完成 + 复验通过才允许释放；
3. **行为等价**：除"因子库路径随路由"外，不改变任何 API/撮合/复权语义；切换本身
   若触发分支 B 全量重锚，属**数据修复**而非性能优化，按正确性变更审核；
4. **进度报告铁律**：落地 + 审核通过后更新实时进度报告（含证据、分支决策、变更记录）。

---

## 9. 与 ⑤ 前置的时序关系

```
TD-D2 步骤 1（gen1 灌数）  ──┐
TD-D2 步骤 2-3（路由+测试）──┼── 与 ⑤ 其它前置并行
⑤ C-6 释放前置检查（含 TD-D2 复验）──┘
     ↓
⑤ 实际释放（用户最终确认） → 4 QFQ 任务 → 观察期
```
