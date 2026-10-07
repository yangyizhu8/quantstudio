# 件 A · 对齐生命周期门 · 设计方案（六步①，2026-10-07 总调度批准启动 P2-α）

> 母授权：闭环计划 A-G 全批（2026-10-07）；本件②单独审。
> 类型：**新增检测型**（新门禁只对「未对齐」报 WARN/FAIL，不改变既有对齐行为）。

## 1. 问题定义

对齐目前是**事后审计**（S1 手动跑）——新策略转换后是否双端对齐，无管线级强制。闭环运转
要求：对齐升格为**管线门禁**——未过对齐门的策略不得标记毕业/分发，闭环自此自动转起来。

## 2. 改动范围（框架层，策略/引擎零改动）

### 2.1 策略生命周期状态机（契约文档）

`docs/strategy-compiler/alignment-lifecycle.md` 新建：

```
generated → converted → local-passed → platform-run → aligned → graduated
                │（qs-compile）   │（本地回测+校验）  │（平台侧回测·用户域）│（S1 残差门）   │（毕业/可分发）
```

- 每态的**进入判据与产出物**逐态定义（generated=R6 验收过；converted=api_portability PASS；
  local-passed=本地回测黄金指标落盘；platform-run=平台日志（Log.txt 契约）交付；
  **aligned=S1 报告残差达标**；graduated=registry 登记+状态归档）；
- **唯一硬门**：`aligned` 态——S1 残差判据（首策略族阈值 <0.5%，达标后每维度收紧）；
- 降级路径：平台版本演化/新案立案 → graduated → re-aligned（回环）。

### 2.2 校验器门禁项（`skills/quantstudio-strategy-compiler/scripts/validate_agent_strategy.py`）

新增校验规则 `ALIGNMENT-GATE`（对齐 NO-LOOKAHEAD-INCLUDE 同模式）：
- **输入**：策略目录的 `alignment/report.md`（S1 产物落盘约定）；
- **判定**：产物存在 + verdict 含「已对齐」或残差达标 ⇒ PASS；产物缺失 ⇒ **WARN**
  （首版 WARN 不 BLOCK——存量策略豁免+新策略柔性过渡，避免一刀切阻断在役流程）；
  产物存在但未达标 ⇒ **BLOCK**（明确失败信号，禁止毕业）；
- **存量豁免**：转换产物无 `alignment/` 目录 ⇒ 视为 legacy，WARN 提示补跑（登记制）。

### 2.3 S1 产物落盘约定（B 件联动）

`align_diff_report.py` 已支持 `--output`——约定转换产物目录新增 `alignment/report.md`
标准路径（A 件文档化，B 件在 S3 台账消费同路径）。

## 3. 影响面

- 新策略全管线多一道 aligned 硬门（BLOCK 仅在「跑了但没过」时触发——不允许假装毕业）；
- 存量六策略/四象限：豁免+登记（四象限已达标 0.013%，可作为首个 `aligned` 归档样例）；
- 引擎/策略源码/转换模板零改动。

## 4. 验收标准

1. 校验器单测：三态判定（达标 PASS/缺失 WARN/未达标 BLOCK）+legacy 豁免；
2. 生命周期文档纸面演练：四象限全案走查（generated→graduated 六态证据齐）；
3. 既有校验器回归全绿（新增规则不扰动既有规则）。

## 5. 回退条件

写前快照；校验规则单点摘除即回退（清单项独立）；状态机纯文档无回退面。