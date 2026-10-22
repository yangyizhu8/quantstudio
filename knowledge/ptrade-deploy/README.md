# ptrade-deploy 知识库（PTrade 回测 → 模拟盘/实盘部署）

> 定位：qs-deploy 模块闭环知识库——设计文档见主仓 `docs/ptrade-deploy-pipeline-design.md` §8（总调度批复修订②）。
>
> **边界（2026-10-22 拓扑纠偏）**：原 `QuantStudio-trading/vault/` **已于 2026-10-22 解散**，PD 条目迁入
> **主仓 `knowledge/ptrade-deploy/`**（与 `knowledge/qmt/` 同级对等）。主仓**双推**：`quantstudio-plus`（私有 VIP）
> ＋ `quantstudio`（**公开**）——即条目推送即构成公开再分发，**该边界由用户 2026-10-20 裁定一=B 明确接受**
> （「platform-docs 迁主库公开」含恒生/迅投官方文档转录）。收敛台账仍在 `docs/ptrade-deploy-convergence-ledger.md`。
>
> **口径提示**：本族 schema **独立定谳**——`fix_layer` 自有枚举（见下），**不并入 QD 五枚举、不入 QD 机检**；
> 三字段 `domain/trigger/recurrence` 与 QD 族同形（便于跨族检索），但域语义按本族落点自判。

## 条目 schema（frontmatter，可机读回哺——禁止散文式不可回哺笔记）

```yaml
---
id: PD-YYYY-NNNN
symptom: "现象一句话（可检索）"
root_cause: "根因一句话"
fix_target: "修复落点：文件路径 + commit sha"
fix_layer: contract_table | transformer | gate | template | kernel | profiler | config | equiv | engine
domain: D1 | D2 | D3 | D4 | D5          # 域编码（与 QD 族同形；D2 转换 / D3 运维与缺陷 为主）
trigger: "最小复现触发条件（非空；一句话可判定是否命中）"
recurrence: 0                            # 同族复发次数（整数；R 指标数据源，默认 0）
evidence: "验证证据指针"
fixture: "复现固件路径"
feedback:            # 回哺方式（至少一项非空）
  contract_row: "契约表新增行描述或 null"
  gate_rule: "门禁/校验规则新增描述或 null"
  template_patch: "模板改动描述或 null"
status: open | closed
closed_round: N
---
```

**`fix_layer` 枚举定稿（2026-10-22 ②审裁定 (a)）**：八枚举＝
`contract_table`（契约表）｜`transformer`（转换器）｜`gate`（门禁）｜`template`（模板）｜`kernel`（内核/preflight 回哺读取）｜
`profiler`（剖析）｜`config`（配置档）｜`equiv`（等价验证）。
**多落点以 `+` 连接**（各组件须在八枚举内）——如 PD-2026-0006/0007 的
`profiler+config+transformer+equiv`：横跨四层是事实，**禁止压成单词**（压词＝丢语义）。

## 回哺机制

P0 预检（主仓 `quantstudio/ptrade_deploy/preflight.read_vault`）读本目录
`*.md` 的 frontmatter（id/symptom/status）——条目存在即回哺链路生效；
trading 仓不可达时 P0 降级 degraded（不阻塞转换，台账记录）。

## 登记纪律（M1 §8.3/§8.5）

- 条目入库与框架层修复、验证证据、复现固件**四同步**，禁止只更其一；
- 每轮登记 N_i（新发现）/R_i（复发：同 root_cause 再次命中）；
- R_i>0 = 流程违规 → 先修管线再修漏洞；
- 收敛 = 连续 3 轮 R_i=0 且 N_i 单调递减趋零。
