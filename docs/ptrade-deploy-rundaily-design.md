# PD-0006 方案：qs-deploy 支持run_daily 源形态（六步①方案）

> 立项依据：收敛台账轮 2 PD-2026-0006（open）——run_daily 源形态能力边界；总调度裁定排 M8 后六步新方案件；用户裁定 B 提前起草（2026-10-11）。
> 状态：①方案 ✅ → ②审计 ✅（总调度通过）→ ③实施 ✅ → ④验收 ✅（`docs/evidence/ptrade-deploy-pd0006-evidence.md`，含实施中增强：混合形态显式拒绝）→ ⑤用户确认 ✅（2026-10-15 批准，与 M8 同批）→ ⑥推送（dev 统推候令，勿单独 push）。

## 1. 问题定义

**复现**：V6 横验证中 `hv_tech_etf_mvo`（真实转换产物 `output/ptrade_export/tech_etf_mvo_rotation/tech_etf_mvo_rotation_ptrade.py`，37KB）P2 FAIL：

```
档位交叉校验失败：日线执行档要求源码存在 handle_data（挂载包装的输入），实际生命周期函数: ['initialize']
```

**源形态实态**（tech_etf_mvo_rotation_ptrade.py:799-805）：

```python
def initialize(context):
    ...
    run_daily(context, weekly_rebalance, time='15:00')   # 周频调仓挂载（ISO 周边界去重）
```

无模块级 `handle_data`——调仓逻辑挂在自定义函数 `weekly_rebalance`，由源自身 `run_daily` 驱动。上游 convert-pipeline 的周频/定时类策略产物均为此形态。

**机理**：现行 `config.py:226` P2 交叉校验要求日线执行档源码存在 `handle_data`（作为 `transformer.py _mount_daily` 的唯一挂载输入）；run_daily 源形态不含 handle_data → fail-closed FAIL（门禁行为正确，属能力边界——台账判定无误）。

## 2. 平台文档依据（先查文档铁律，2026-10-11）

依据 Context7 `/kay-ou/ptradeapi`（docs/original/Ptrade山西.md「run_daily-按日周期处理」）：

- 签名 `run_daily(context, func, time='9:31')`；func 必须以 context 为参数（Callable[[Context], None]）；
- **只能在 initialize 中调用**；可在 initialize 中多次调用实现多个定时任务，**交易中 run_daily+run_interval 累计上限 5 个**（超限堵塞致部分任务不触发）；
- 股票回测：回测周期为日时无论 time 设多少都只在 15:00 执行；**交易中不受此限制**（00:00~23:59 可设）。

推论：源产物 `run_daily(..., time='15:00')` 在日线回测=每日 15:00 执行语义；部署到交易环境后 time 可控生效——与 handle_data 源（回测收盘撮合语义 → 部署 14:50 尾盘执行）同属「收盘语义的尾盘近似」，重挂到档位时点语义等价。

## 3. 知识反哺检索留痕

```
知识反哺：[命中] 小市值ETF动量大白马策略（01-策略/小市值ETF动量大白马策略.md，2026-10-11）
```

采纳要点：该条目记录 run_daily + 时间判断实现多策略错峰调度——本方案 D3 的多挂载约束（单执行挂载初版限制+≤5 上限校验）与错峰实践一致；多挂载源扩展留待后续轮按此模式设计。

## 4. 改动范围（5 文件，纯增量分支）

| 文件 | 扩展 | 形态 |
|---|---|---|
| `profiler.py` | P1 识别 run_daily 源形态：扫描 initialize 内 `run_daily(ctx, <func>, time=...)` 调用 → profile 增 `run_daily_mounts: [{func, time}]`（结构化，供下游消费）；lifecycle 报告含挂载函数名 | 新增识别分支，既有路径零改动 |
| `config.py` | P2 校验放宽：日线档合法性=「有 handle_data」**或**「恰有 1 个 run_daily 执行挂载」（多挂载源 fail-closed：「暂不支持多 run_daily 挂载源，待后续扩展」）；挂载计数>5 预算校验（部署自身 1+源残留 0≤5） | 条件放宽+新 fail 消息 |
| `transformer.py` | `_mount_daily` 第二输入形态：无 handle_data 且有 run_daily 挂载 → 包装目标改为源挂载函数（如 weekly_rebalance），函数体原样内联进 `_deploy_daily_exec`；**源 run_daily 注册语句替换**为部署挂载（`run_daily(ctx, _deploy_daily_exec, time=档位时点)`，MOUNT 标记含源时点 provenance） | 新分支；handle_data 路径零改动 |
| `equiv.py` | P4 还原第 5 类：MOUNT 标记块还原为**源函数名**（weekly_rebalance，非 handle_data）+注册语句还原为源时点（15:00）；白名单 diff 扩「挂载签名行为第 5 类：run_daily 源形态重挂」 | 白名单追加 |
| `runner.py` | 透传 `run_daily_mounts` 供 P2/P3/P4 消费；报告 lifecycle 行显示挂载信息 | 透传 |

**不动**：gates.py（run_daily 已在部署产物 API 白名单——现行挂载产物即用）、templates、contract_table（无契约变化）、deliver（报告行模板微增）。

## 5. 设计决策

- **D1 识别保守性**：仅识别「initialize 直接语句级的 run_daily(context, <Name>, time=<str>)」——kwargs/变量引用/条件分支内挂载不识别（fail-closed 报不可识别，不猜测）。
- **D2 时点重挂**：部署产物统一重挂到档位时点（daily_eod_1450 → 14:50）；源时点记入 MOUNT provenance 并在 P4 还原——回测 15:00 语义与部署 14:50 尾盘的同近似关系与 handle_data 源完全一致（§2 推论），护栏统一时点管控。
- **D3 单挂载限制**：初版仅支持恰 1 个执行挂载的源；多挂载源（Obsidian 错峰模式）fail-closed 拒绝并指引——多挂载扩展留后续轮（台账新条目承接）。
- **D4 fail-closed 边界不回退**：现行「无 handle_data 即 FAIL」对非 run_daily 形态（如纯 initialize 策略）保持不变——放宽仅对「已识别 run_daily 挂载」的源生效。

## 6. 影响面（纯增益审计：三型判定=新增能力型）

- 既有 handle_data 源路径：五文件改动均为新分支/条件放宽，不触碰既有执行路径——25/25 既有测试全绿为证；
- 回测引擎/黄金结果：零交集（ptrade_deploy 独立包，不在回测路径）；
- V6 横验证池：tech_etf_mvo 从「P2 blocked（复现件保留）」转正——横验证补齐 4/4。

## 7. 验收标准

1. 新增固件：`tests/deploy_fixtures/fixture_rundaily_strategy.py`（initialize+run_daily 挂载形态）+ 对应 deploy.yaml；新测试（识别/校验/挂载/还原/端到端，预计 6-8 用例）；
2. `hv_tech_etf_mvo` 真实产物重跑 7 阶段全 PASS（P4 等价 diff 零回退）；
3. 既有 25 用例全绿（handle_data 路径零回归）；
4. V6 池补齐 4/4 + 台账轮 3 登记（N₃/R₃ 按实况）；
5. CONTRACT GATE（V2 口径）零干扰。

## 8. 回退条件

纯新增分支 → 回退=revert 本方案 commit（既有 handle_data 路径天然不受影响；hv_tech_etf 回到 P2 blocked 复现件状态）。触发：任一验收项失败且当轮无法闭环，或用户/总调度明示撤销。

## 9. 流程口径

本方案→②审计（ZCode）→修订至通过→③实施→④验收（§7）→⑤用户确认→⑥随下批统出推送。实施轮同步更新：台账（轮 3）、vault PD-0006 状态 open→closed、README/strategy_toolbox/prompt_engineering 若表述涉及（预计仅台账+证据文档）。
