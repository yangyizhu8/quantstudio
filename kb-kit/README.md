# kb-kit · 知识库闭环便携工具包（v1.0.0）

> **一句话**：把「知识沉淀 → 分族机检 → 反哺检索」这套闭环，用**一条命令**装进任何 git 项目。
> 制度文本（开工留痕 R1／验收沉淀 R4）见 `templates/onboarding-checklist.md` 与各项目 AGENTS；
> 本包只提供**机器**（脚本＋钩子＋模板＋CI）。

## 1. 装什么（自包含）

```
kb-kit/
  KIT_VERSION              版本号（kb_hook 运行时会打印，用于发现分叉）
  install.ps1              幂等安装器（见 §3）
  scripts/kb_preflight.py  frontmatter 解析/校验核心（QD 式 CLI 仍可用）
  scripts/kb_hook.py       通用分族体检（读 knowledge/kb-info.yaml 的 families）
  scripts/kb_draft.py      条目草稿器（--kb 任意族，族 schema 驱动）
  scripts/install_kb_hook.ps1  pre-push 钩子安装器
  templates/kb-info.yaml   注册卡模板（families= schema 轴 / registries= 归属轴）
  templates/family-README.md 族 README 模板（枚举/域/登记纪律）
  templates/kb-schema.yml  GitHub Actions CI 模板（WARN 起步）
  templates/onboarding-checklist.md  30 分钟接入清单
```

## 2. 30 分钟接入（四阶段）

| 阶段 | 动作 | 预计 |
|---|---|---|
| 1 建库 | 建 `knowledge/<族>/`；抄 `templates/family-README.md` 定谳枚举；沉 3–5 条种子条目（`kb_draft.py --kb`）；抄 `templates/kb-info.yaml` 填 families | ~15 min |
| 2 装机检 | `powershell -File kb-kit/install.ps1 -Target <项目根>` | ~10 min |
| 3 跑基线 | 从种子条目改写 10–20 问（含 1/3 无 API 名）→ grep 记基线命中率 | ~5 min |
| 4 文档核对 | 按 `templates/onboarding-checklist.md` 勾选 | 计入上列 |

## 3. 安装器用法

```powershell
# 首次接入（拷脚本 + 建 knowledge 骨架 + 装 pre-push）
powershell -File kb-kit/install.ps1 -Target C:\path\to\project

# 加装 CI（可选；项目需用 GitHub Actions）
powershell -File kb-kit/install.ps1 -Target C:\path\to\project -InstallCI

# 升级已有副本（只覆盖 scripts/，不动知识库）
powershell -File kb-kit/install.ps1 -Target C:\path\to\project -Upgrade

# 维护者：从 QuantStudio 主仓同步本包（覆盖 kb-kit/scripts 与 templates/）
powershell -File kb-kit/install.ps1 -Target <任意> -Sync -CanonicalRepo D:\miniQMT策略实盘\QuantStudio

# 只拷脚本不装钩子（自管钩子的项目）
powershell -File kb-kit/install.ps1 -Target C:\path\to\project -NoHook
```

**幂等**：二次执行默认全部跳过；`-Upgrade` 才覆盖 `scripts/`；`knowledge/` 与项目已有钩子**永不覆盖**。

## 4. 与 QuantStudio 的关系（单一事实源）

主仓 `scripts/kb_*.py` 与 `docs/templates/*` 是**权威源**，`kb-kit/` 是**分发副本**。
副本分叉用 `KIT_VERSION` 与 `-Sync` 管理；分叉本身不致命（门禁脚本各自演进），
但**通用 schema 三字段（domain/trigger/recurrence）与问法口径必须以 `docs/templates/kb-entry-schema-v1.md` 为准**。

## 5. 边界（如实）

1. 安装器为 **PowerShell**（Windows 优先）；Linux/macOS 项目需手抄四步（`scripts/` 是纯 Python，可直用）；
2. 深度反哺（写盘 BLOCK 规则）**不在本包**——规则内容按项目自填（如 QuantStudio 的 B1/B2/B3）；
3. `kb_hook` 默认 **WARN 不阻断**；升阻断＝`--strict`（钩子侧改 `install_kb_hook.ps1 -Strict` 重装）；
4. 项目无 GitHub Actions ⇒ `-InstallCI` 跳过即可，不影响接入完成。
