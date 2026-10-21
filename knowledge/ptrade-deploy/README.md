# ptrade-deploy 知识库 vault（Obsidian）

> 定位：qs-deploy 模块（PTrade 回测 → 模拟盘/实盘部署转换）的闭环知识库——
> 设计文档见主仓 `docs/ptrade-deploy-pipeline-design.md` §8（总调度批复修订②）。
>
> **边界（修订①）**：本 vault 落 QuantStudio-trading 仓——实盘部署操作细节
> （券商/柜台/账户）只准入本 vault，不入公开仓；主仓仅存
> `docs/ptrade-deploy-convergence-ledger.md` 收敛台账（N_i/R_i 计数 + 指针）。

## 条目 schema（frontmatter，可机读回哺——禁止散文式不可回哺笔记）

```yaml
---
id: PD-YYYY-NNNN
symptom: "现象一句话（可检索）"
root_cause: "根因一句话"
fix_target: "修复落点：文件路径 + commit sha"
fix_layer: contract_table | transformer | gate | template | kernel
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

## 回哺机制

P0 预检（主仓 `quantstudio/ptrade_deploy/preflight.read_vault`）读本目录
`*.md` 的 frontmatter（id/symptom/status）——条目存在即回哺链路生效；
trading 仓不可达时 P0 降级 degraded（不阻塞转换，台账记录）。

## 登记纪律（M1 §8.3/§8.5）

- 条目入库与框架层修复、验证证据、复现固件**四同步**，禁止只更其一；
- 每轮登记 N_i（新发现）/R_i（复发：同 root_cause 再次命中）；
- R_i>0 = 流程违规 → 先修管线再修漏洞；
- 收敛 = 连续 3 轮 R_i=0 且 N_i 单调递减趋零。
