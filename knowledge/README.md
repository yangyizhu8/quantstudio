# 双端对齐知识库（Alignment Knowledge Base）

> 建立：2026-10-06（用户裁定）· 形态：仓库内 Markdown 知识分区（Obsidian 可直接作为 vault 浏览）
> 性质：双端对齐（PTrade 平台 ↔ QuantStudio 本地）领域知识的**唯一权威库**——注册表账本 /
> 契约档案 / 诊断剧本 / 历史索引四层。

## 1. 导览（按用途进入）

| 你要做什么 | 去哪里 |
|---|---|
| 接手双端对齐工作（新会话/智能体必读） | [alignment-loop-guide.md](alignment-loop-guide.md)（闭环管线总指导） |
| 查某个字段/API/规则的**正确口径** | [contracts/](contracts/)（契约档案） |
| 查**维度对齐状态与宣布进度**（件 G） | [contracts/dimension-alignment.md](contracts/dimension-alignment.md)（维度对齐档案）+ scripts/check_dimension_alignment.py（机检，只提示） |
| 诊断一个双端差异（SOP） | [playbooks/diff-triage.md](playbooks/diff-triage.md)（三态仲裁总纲） |
| 查洞的**总账本与收敛进度** | [registry.md](registry.md)（对齐面注册表） |
| 查历史修复案例的证据 | [archive-index.md](archive-index.md)（147 份证据件分类索引） |
| 了解探针策略矩阵 | [probes/README.md](probes/README.md) |

## 2. Obsidian 使用说明

- 本目录就是纯 Markdown——**Obsidian 只是阅读层，仓库文件是本体**：git 版本化、CI 校验、
  跨智能体（dsh/CodeBuddy/ZCode/Trae）直读全部无损；
- 打开方式：Obsidian → Open folder as vault → 选择**仓库根目录**
  `D:\miniQMT策略实盘\QuantStudio`（knowledge/ 为其中分区；文档内 `[[双链]]` 与相对链接混用，
  GitHub 渲染走相对链接，Obsidian 两者皆可）；
- **同步机制是 git 不是 Obsidian**：quantstudio-plus / quantstudio 双远程经
  `git push origin` 一次推送双仓同步（多 push URL）；Obsidian 不承担任何同步职责。

## 3. 更新纪律（常态化机制）

1. **落档触发**：每轮对齐修复走六步流水线，**第④步验收时同步落档**——更新 registry 条目状态 +
   contracts 契约档案 + （新类时）playbook + archive-index；
2. **消费触发**：新策略双端验证前读 contracts/；诊断差异按 playbooks/ 走；接手先读本 README；
3. **指针不复制**：契约知识存「结论 + 证据件指针」，原文留在 docs/evidence/，禁止双源；
4. **防腐校验**：registry 条目 ↔ 契约测试指针一致性随保真回归门校验，防账本腐烂；
5. 推送：knowledge/ 随六步第⑥步双仓推送同步（纯文档类推送可豁免同步门共享层检查，须登记豁免依据）。

## 4. 双仓与多仓关系

- 主仓 `QuantStudio` push → `github.com/yangyizhu8/quantstudio-plus` + `github.com/yangyizhu8/quantstudio`
  **一次推送双仓同步**（knowledge/ 建于主仓即两仓皆有，无需分别建）；
- `QuantStudio-trading`（实盘副本）与 `C:\QuantStudioHybrid`（Rust 副本）经既有同步门/保温基线
  跟随主仓，knowledge/ 随共享层自然流入。