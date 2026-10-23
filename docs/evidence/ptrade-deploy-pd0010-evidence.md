# PD-0010 验收证据：P0 回哺读取改多候选探测（抗 PD 族拓扑漂移）

> 对应方案：`docs/ptrade-deploy-vaultpath-design.md`（②审通过：多候选探测〔候选优先/回退/
> 全不可达/旧址提示/provenance〕按七项验收 ③启动）。
> 实施快照：`236c56f62ccfad3320a680324814ba0766b721e4`（stash stored，写前快照纪律）。
> 状态：④验收 PASS（本文件）→ 待⑤用户确认。
> 本轮沉淀：PD-2026-0010

## 1. 问题与根因（实测）

| 项 | 值 |
|---|---|
| 缺陷 | `preflight.VAULT_DIR` 硬编码绝对路径 `D:\...\QuantStudio-trading\vault\knowledge\ptrade-deploy`（旧址，2026-10-22 已判定解散，其 10 文件**从未推送** ⇒ 远端无此目录） |
| 拓扑实况 | 三落点并存：trading `knowledge/ptrade-deploy/`（**最终归口**，件 D 批 2 已 mv 且 tracked）→ 主仓 `knowledge/ptrade-deploy/`（公开副本）→ trading `vault/…`（旧址，残留） |
| 失效面 | 任何按远端 clone 的环境 P0 恒 `degraded=True`、`vault_entries=0` ⇒ **回哺静默失效**（降级不阻塞转换 ⇒ 转换仍「成功」但已知坑提示消失） |
| 假象 | 开发机工作区残留旧址目录 ⇒ 实测仍读到 9 条（本地绿、远端哑） |
| 内容分叉 | 旧址副本与新落点**逐条 DIFF**（旧址缺 `domain`/`trigger`/`recurrence`） |
| 附带硬伤 | 绝对盘符常量 → 换机/换盘即断 |

## 2. 实施明细

| 文件 | 改动 |
|---|---|
| `quantstudio/ptrade_deploy/preflight.py` | 新增 `vault_candidates()`（**运行时派生**：主仓根 + 同级兄弟仓目录名 `QuantStudio-trading`，无盘符字面量）；`VAULT_DIR` 降为**弃用兼容别名**；`read_vault()` 改候选遍历 + 返回 `VaultRead(entries, degraded, warnings, source)` + 旧址命中提示 warning + 全不可达才 degraded；`PreflightResult` 增 `vault_source`；模块 docstring 更新 |
| `quantstudio/ptrade_deploy/runner.py` | P0 details 增 `vault_source`；notes 增「P0 vault 回哺来源：<source>」；降级提示文案由「trading 仓不可达」改为「全部候选落点不可达」 |
| `tests/test_deploy_vault.py` | 既有 6 用例适配 4 元返回；**新增 5 用例**：候选优先/回退/全不可达/旧址提示/**源码无盘符字面量** |
| `knowledge/ptrade-deploy/PD-2026-0010.md` | R4 沉淀条目（`kb_draft.py` 起草 → `kb_hook.validate_record` 机检通过 → 补 trigger/讨论/闭环字段） |

## 3. 验收标准逐项（方案 §5 七项）

| # | 标准 | 结果 |
|---|---|---|
| 1 | 候选优先级：首个存在者胜出、`source` 记录 | ✅ `test_candidate_priority_picks_first_existing`（source=trading-knowledge） |
| 2 | 候选回退：首个缺失 → 命中次候选 | ✅ `test_candidate_fallback_when_first_missing`（source=main-knowledge） |
| 3 | 全不可达 → degraded + 警告列出候选 + 不抛异常 | ✅ `test_all_candidates_missing_degrades_with_listed_paths` |
| 4 | 旧址兜底 → 命中旧址 + **提示性 warning** | ✅ `test_legacy_candidate_hit_warns_compat`（warning 含「旧址」） |
| 5 | 端到端：真实产物 P0 details `vault_entries=9` / `vault_degraded=False` / `vault_source` 非空 | ✅ 实跑 `hv_tech_etf_mvo`：`vault_entries=9 degraded=False source=trading-knowledge`；notes 含「P0 vault 回哺来源：trading-knowledge」 |
| 6 | 既有 79 用例全绿 + 契约矩阵哈希一致 | ✅ **84/84**（79 + 5 新）；`check_fund_matrix --check` OK |
| 7 | 新增条目过 `kb_hook.validate_record`（PD 族 `--strict`） | ✅ **10 件全检、不合规 0 件**（exit 0） |

## 4. 零回归与判型核验

- **判型：纯恢复型**——改前读到 9 条、改后读到 9 条（id/symptom/status 逐条同集）；
  转换产物 / 门禁 verdict / 等价 diff **零变化**（P0 只读知识条目，不参与代码变换）；
- 纯增益三问：①其他功能不影响（仅知识读取路径）②回测性能不影响（读 10 个 md，毫秒级）
  ③回测精度不影响（不参与数值路径）——均「不影响」；
- 既有 84 用例全绿 + 矩阵哈希一致 + 族机检 10/10。

## 5. 知识反哺检索留痕（grep-able）

```
知识反哺：[命中] PD-2026-0009（PD/ptrade-deploy/PD-2026-0009.md，2026-10-24）
知识反哺：[命中] ptrade-deploy 知识库 README（PD/ptrade-deploy/README.md，2026-10-24）
知识反哺：[命中] qmt 知识库 README（QD/qmt/README.md，2026-10-24）
知识反哺：[命中] 平台转换铁律-文档签名不可信须建运行实测形态库（07-经验库/knowleage_base，2026-10-24）
```

## 6. 六步进度

①方案 ✅（2026-10-24）→ ②审计 ✅（通过）→ ③实施 ✅（快照 236c56f）→ ④验收 ✅（本文件）→
**⑤用户确认（待）** → ⑥随批统推。
