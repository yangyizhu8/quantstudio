# 新项目接入知识库闭环 · 清单（KMAP-M1 · 30 分钟）

> **顺序提示（2026-10-23 M4 实测修正）**：先跑一次 `kb-kit/install.ps1 -Target <项目根> -NoHook`（只为拿到 `scripts/kb_*.py`），
> 再建库（`kb_draft.py` 需要 scripts/ 就位），最后装钩子/CI —— 勿按「先建库后装机检」的直觉顺序走。
>
> 前置：全局制度层已生效（`~/.dsh/AGENTS.md` 的 R1 开工检索留痕 / R4 验收沉淀，等窗后落位）——
> 未落位时本清单仍可执行，只是「人查」环节靠自觉。
> 产出模板：`kb-info-template.yaml` / `kb-family-readme-template.md` / 本清单（同在 `docs/templates/`）。

## 阶段 0 · 零动作（制度层自动覆盖）

新项目会话从第一天起就被 R1/R4 约束：Plan 必含「知识反哺：[命中]…／[无命中]…／[豁免]…」；
验收必含「本轮沉淀：<条目 id> 或 N/A(理由)」。**空库期常态是 `[无命中]`——声明本身即留痕。**

## 阶段 1 · 建库（约 15 分钟）

| # | 动作 | 产物 | 判据 |
|---|---|---|---|
| 1 | 建目录 `knowledge/<族>/` | 目录 | 族名短、小写（如 `dev`、`ops`） |
| 2 | 复制 `kb-family-readme-template.md` → `knowledge/<族>/README.md`，定谳枚举与 id 前缀 | 族 README | 枚举 ≥1 项；写清「多落点是否允许」 |
| 3 | **沉种子条目 3–5 条**（从项目已有 ISSUES/踩坑提炼） | `<ID>-YYYY-0001.md…` | 用 `kb_draft.py --kb knowledge/<族>` 生成并过检 |
| 4 | 复制 `kb-info-template.yaml` → `knowledge/kb-info.yaml`，填 families/registries | 注册卡 | `families[].dir` 实存；`id_pattern` 与种子条目匹配 |

## 阶段 2 · 装机检（约 10 分钟）

| # | 动作 | 判据 |
|---|---|---|
| 5 | 跑 `scripts/install_kb_hook.ps1 -Repo <项目根>`（装 `.githooks/pre-push` + `core.hooksPath`） | `git config core.hooksPath` 返回 `.githooks` |
| 6 | （可选）把 `scripts/kb_hook.py --files <file> --strict` 接入 CI（M3 后提供 workflow 模板） | CI 对坏条目转红 |

## 阶段 3 · 跑基线（约 5 分钟）

| # | 动作 | 判据 |
|---|---|---|
| 7 | 从种子条目改写 10–20 问（含 1/3 无 API 名）→ 跑 grep 记基线命中率 | 读数落 `docs/evidence/r0-*-baseline.md` |

## 接入后的日常（谁干什么）

| 时点 | 机制 | 项目主人要做什么 |
|---|---|---|
| 每轮开工 | R1 留痕（会话强制） | 无 |
| 踩坑后 | `kb_draft` 起草 + 族 schema 机检 | 审批条目内容（点头/改一句） |
| 每次 push | `kb_hook` 分族体检（WARN 起步） | 无；观察一轮后可裁升 `--strict` |
| 每轮验收 | R4：本轮沉淀 id 或 N/A | 填一行 |
| 每月 | R0 题集复测 + R/B/H | 看 3 个数 |

## 三条边界

1. **深度反哺（写盘 BLOCK）不在接入包**：规则表框架在，规则内容按项目自填（如 QuantStudio 的 B1/B2/B3）。
2. **「自动沉淀」＝不留漏网**：R4 保证「要么条目、要么理由」，内容判断永远在人/agent。
3. **Obsidian 可选**：绑定的是 markdown+frontmatter+git，Obsidian 只是阅读层。
