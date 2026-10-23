# PD-0010 方案：P0 回哺读取改多候选探测（抗 PD 族拓扑漂移）（六步①方案）

> 立项依据：2026-10-24 本域未闭环盘点实测——PD 族拓扑连续两次纠偏（2026-10-22 trading `vault/`
> 解散 → 条目迁入主仓 `knowledge/ptrade-deploy/`；随后件 D 批 2 再迁至 trading 仓 `knowledge/`），
> 而**代码指针 `preflight.VAULT_DIR` 仍硬编码旧址** `QuantStudio-trading\vault\knowledge\ptrade-deploy`
> （该目录已被判定解散，且其 10 个文件从未推送 → 远端环境无此目录）。
> 状态：①方案（待②审计）——**未实施、未提交**。
> 关联：`docs/ptrade-deploy-pipeline-design.md` §8.4、§14.6；`docs/ptrade-deploy-convergence-ledger.md`；
> `knowledge/ptrade-deploy/README.md`（PD 族 schema 与回哺机制）。

## 1. 问题定义

**现状（2026-10-24 实测）**：

| 候选落点 | 存在 | git 跟踪 | 定位 |
|---|---|---|---|
| `QuantStudio-trading/knowledge/ptrade-deploy/` | ✅ 10 md | ✅（件 D 批 2 已 mv；trading 仓 tracked knowledge/ 222 文件） | **最终归口** |
| `QuantStudio/knowledge/ptrade-deploy/` | ✅ 10 md | ✅（主仓公开副本） | 过渡中转 |
| `QuantStudio-trading/vault/knowledge/ptrade-deploy/` | ✅ 10 md | ✅ 但**远端无**（trading ahead 1013 未推） | **待解散旧址** ← **代码指针在此** |

`quantstudio/ptrade_deploy/preflight.py:30`
`VAULT_DIR = Path(r"D:\miniQMT策略实盘\QuantStudio-trading\vault\knowledge\ptrade-deploy")`

**失效机理**：指针指向**最不稳定的一个落点**——一旦该目录按 2026-10-22 决议被删除（trading 仓
`vault/` 已解散、其 10 个文件从未推送 ⇒ 任何按远端 clone 的环境本就没有它），`read_vault()` 即
`Path.exists()` 失败 → 返回 `degraded=True` → P0 回哺**静默失效**（降级不阻塞转换，转换照常成功，
但「已知坑未闭环」提示永久消失——正是 PD-2026-0009 定义的「面上存在、实质失效」的最隐蔽形态）。

**当前工作区仍能读到 9 条**（trading 工作区本地残留 vault/ 目录，read_vault 实测 entries=9、
degraded=False）——**这正是最危险的假象**：本地绿、远端哑。

## 2. 设计决策

### D1 多候选探测（按稳定性优先级），取代单点硬编码

```
VAULT_CANDIDATES = (
    <trading 仓>/knowledge/ptrade-deploy,     # 最终归口（件 D 批 2 后）
    <主仓>/knowledge/ptrade-deploy,           # 公开仓副本（过渡/单机运行）
    <trading 仓>/vault/knowledge/ptrade-deploy # 旧址（解散前兼容）
)
```
读取顺序取**第一个存在且可读的**；全部不可达 → `degraded=True` + 警告（口径不变）。

> **为何不用「钉死单点」**：拓扑正在迁移（两次纠偏），下一次迁址无从预知；候选探测让回哺链
> **对落点漂移免疫**，且每态都有 ≥1 个可用落点（当前三态全覆盖）。

### D2 命中源记录（provenance）

`PreflightResult` 增 `vault_source: str`（实际命中的候选名，如 `trading-knowledge`）；
P0 details 与报告可见——**消除「读到了但不知道读的哪」**（PD-2026-0009 同族教训：静默即风险）。

### D3 degraded 口径不变

路径全不可达 → `degraded=True` + 警告，**不阻塞转换**（§14.6 既有契约）；但**新增**
「命中的是旧址（兼容期）」的**提示性 warning**（非失败），提示该落点待迁移。

### D4 旧址清理由拓扑线负责，本方案不删任何目录

本方案只改**读取侧**（指针→候选）；`vault/` 残留（trading 仓 tracked 10 文件）的删除属拓扑线
（件 D 批 2 后续）职责，本方案不越界删除，避免误删他线在用文件。

### D5 候选路径的求值方式

候选以「仓库根相对 + 已知兄弟仓相对」表达，求值时按 `Path(__file__).resolve().parents[2]`（主仓根）
推出主仓路径，并按**主仓根同级目录名** `QuantStudio-trading` 推出兄弟仓路径——**不写绝对盘符**
（当前 `VAULT_DIR` 的绝对路径是另一处硬伤：换机/换盘即断）。

## 3. 改动范围

| 文件 | 改动 |
|---|---|
| `quantstudio/ptrade_deploy/preflight.py` | `VAULT_DIR`（绝对）→ `VAULT_CANDIDATES`（相对求值 3 项）；`read_vault()` 改候选遍历 + 记录 `vault_source` + 旧址提示 warning；`PreflightResult` 增 `vault_source` 字段；`run_preflight` 填入 details |
| `tests/test_deploy_vault.py` | 增候选优先级/回退/全不可达/命中源 4 类用例（既有 6 用例保持） |

runner / deliver / gates / transformer **零改动**；P0 读到的条目集合与今日**逐条相同**（同一族 9 条）。

## 4. 三型判定与纯增益审计

- **判定：纯恢复型**——把读取口径与已裁定的拓扑对齐，**行为零漂移**：
  - 今日本地实测读到 9 条（id/symptom/status 与结构化库逐条一致），改后仍读到 9 条；
  - 转换产物、门禁 verdict、等价 diff **零变化**（P0 只读知识条目，不参与代码变换）。
- 纯增益三问：①不影响其他功能（仅知识读取路径）②不影响回测性能（读 9 个 md，毫秒级）③不影响
  回测精度（不参与任何数值路径）——均「不影响」。
- **风险 R1（改口径引入误读）**：候选序错误可能读到**过期副本**（旧址缺 domain/trigger 字段）。
  缓解：旧址仅作**末位兜底**，且命中旧址时发提示 warning（可见，非静默）。
- **风险 R2（相对路径求值偏差）**：兄弟仓目录名随部署改名 → 候选不可达 → degraded（fail-closed，
  不误读）。

## 5. 验收标准

1. 候选优先级：主仓无 `knowledge/ptrade-deploy/`、trading 有 → 命中 trading 候选，`vault_source`
   记录该来源；
2. 候选回退：trading 候选不可达 → 命中主仓候选；
3. 全不可达 → `degraded=True` + 警告 + **不抛异常**（既有契约）；
4. 旧址兜底：仅旧址存在 → 命中旧址 + **提示性 warning**（「旧址兼容期，待迁移」）；
5. 端到端：真实产物跑 `run_deploy` → P0 details `vault_entries=9`、`vault_degraded=False`、
   `vault_source` 非空；
6. **既有 79 用例全绿**（含 `test_deploy_vault.py` 6 用例）+ 契约矩阵哈希一致；
7. **机检**：新增条目按 PD 族 schema 过 `kb_hook.validate_record`（`--strict`）——本轮若新增
   PD-2026-0010 条目，须含 `domain/trigger/recurrence`。

## 6. 回退条件

- 任一既有用例红 / 候选命中集合与今日不一致 / `vault_source` 未落 / degraded 口径变化
  → **立即 revert** 本批 commit；
- 若实施中发现「主仓副本」与「trading 归口」内容分叉（两落点条目内容不同）→ 停手并呈报
  （属拓扑线的归口裁剪，本方案不擅自选定权威副本）。

## 7. 知识反哺检索留痕

```
知识反哺：[命中] PD-2026-0009（PD/ptrade-deploy/PD-2026-0009.md，2026-10-24）
知识反哺：[命中] ptrade-deploy 知识库 README（PD/ptrade-deploy/README.md，2026-10-24）
知识反哺：[命中] qmt 知识库 README（QD/qmt/README.md，2026-10-24）
知识反哺：[命中] 平台转换铁律-文档签名不可信须建运行实测形态库（07-经验库/knowleage_base，2026-10-24）
```

采纳要点：
- **PD-2026-0009**（族 PD / 域 D3）：「面上存在、实质失效」的最隐蔽形态——本方案的核心动机即
  复用该判定：*回哺链是否真生效，必须在**目标部署形态**上验证*（远端 clone 环境 ≠ 本地工作区）。
- **ptrade-deploy README**（族 PD / 域 D3）：明确「P0 预检读本目录 frontmatter；trading 仓不可达时
  降级 degraded」——本方案把「不可达」从单点改为候选集，与该口径一致且更强。
- **qmt README**（族 QD / 域 D3）：记录件 D 批 2 的最终迁址方向（trading `knowledge/`）——候选序
  的第一项依据。
- **平台转换铁律-文档签名不可信须建运行实测形态库**（07-经验库）：「实测优先、探针先行」——
  本方案的验收第 5 项即以**真实产物端到端**（而非单测）确认命中来源与条目数。

## 8. 六步与排期

①方案（本文件，2026-10-24 出件）→ **②审计（待）** → ③实施（写前快照 + 精确清单）→ ④验收（本文件 §5
七项）→ ⑤用户确认 → ⑥随批统推（+ `knowledge/ptrade-deploy/` 族 schema 机检）。
