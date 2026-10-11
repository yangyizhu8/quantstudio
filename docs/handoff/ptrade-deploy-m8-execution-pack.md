# qs-deploy M8 执行包（预制件——V1 过闸 + 用户确认后按序执行，预计 60 分钟内闭环）

> 状态：预制完成 2026-10-11 00:3x；触发条件 = V1 PASS + 用户明确确认（六步⑤闸门）。
> 纪律对照：精确 add（禁 add -A）·双远程核验·trading 同步门·sha 回填·文档同步。

## A. 提交面精确清单（主仓，12 文件面——git status 已核）

```
git add quantstudio/ptrade_deploy/ \                      # 内核包 12 文件（含 contract_table.yaml + templates/）
        quantstudio/strategy_compiler/cli.py \            # M4 +43 行
        quantstudio/gui/main_window.py \                  # M5 +4 行
        quantstudio/gui/tabs/deploy_tab.py \              # M5 tab 薄壳
        tests/deploy_fixtures/ \                          # 固件 + hv/ 横验证配置
        tests/test_deploy_pipeline.py \
        tests/test_deploy_equiv.py \
        tests/test_deploy_gates.py \
        tests/test_deploy_cli.py \
        tests/test_deploy_gui.py \
        docs/ptrade-deploy-pipeline-design.md \           # M1 设计文档
        docs/ptrade-deploy-convergence-ledger.md \        # M6 台账
        docs/evidence/ptrade-deploy-m7-evidence.md        # M7 证据（含 V1 补录）
# + M8 时新增：README.md / docs/strategy_toolbox.md / docs/prompt_engineering.md（§E 同步文案落盘后）
```

**禁止卷入**（他线在途）：agent_workspace/*、quantstudio/backtest/*（M）、config/profiles/*、docs/evidence 其他文件、docs/handoff 其他文件。

## B. commit message 草稿

```
feat(deploy): qs-deploy PTrade 回测→模拟盘/实盘部署转换模块（M1-M8 全流水线）

- 内核 quantstudio/ptrade_deploy/：P0 预检(vault 回哺)→P1 画像→P2 配置校验→
  P3 AST 变换→P4 等价验证(白名单 diff)→P5 平台门禁→P6 交付；结构化报告零抛异常
- CLI 壳 qs-compile deploy（+43 行零删改）；PyQt 壳「转模拟盘/实盘」tab
  （main_window.py +4 行 + deploy_tab.py 薄壳）——三路 parity SHA256 测试固化
- 25/25 新增测试全绿；V2 CONTRACT GATE 零干扰；V6 横验证 3/3 真实策略；
  V1 全量+黄金结果见 docs/evidence/ptrade-deploy-m7-evidence.md
- 闭环：vault(trading 侧) PD-0001~0006 + 主仓收敛台账（N: 4→2，R≡0）
- 设计文档 docs/ptrade-deploy-pipeline-design.md（§10 落点修正随本提交）
```

## C. 推送序列（用户确认后执行）

```
git commit  # 按提交面精确 add 后
git push origin
git ls-remote origin main          # 双远程 HEAD 逐位核验（多 push URL 两仓库）
# contract-gate CI 会自动跑（push main 触发）——核对 CI 绿
```

## D. trading 同步门（推送后同一工作周期）

```
cd D:\miniQMT策略实盘\QuantStudio-trading
git fetch origin && git merge origin/main      # 共享层同步（本模块全部在共享层，无豁免）
check-drift 九项 + ci-smoke 共享层回归段       # 按既有口径
# vault/ 提交：git add vault/knowledge/ptrade-deploy/ （本会话 trading 侧唯一新增面）
# 同步结果登记台账+日历（线调度回执）
```

预检备注：trading 仓现有他线 M 文件（docs/architecture/*.json、docs/sync-ledger.md）——merge 前核对无冲突路径重叠（本推送主仓面不含 docs/architecture，无重叠预期）。

## E. 文档同步文案（M8 时落盘三处）

### E1. README.md 转换通道表（行 216 后追加一行）

```markdown
| PTrade 回测产物 → 模拟盘/实盘部署 | `qs-compile deploy <deploy.yaml>` | 新增（qs-deploy：P0-P6 内核，CLI/PyQt 双端对等，docs/ptrade-deploy-pipeline-design.md） |
```

（README 行 401 qs-compile 行不动——子命令自动进 --help。）

### E2. docs/strategy_toolbox.md 文末追加节

```markdown
## 转模拟盘/实盘（qs-deploy，2026-10-11）

PTrade 回测策略产物 → 模拟盘/实盘部署代码的下游转换通道（独立于本工具箱的
策略生成域）：`qs-compile deploy <deploy.yaml>` 或 GUI「转模拟盘/实盘」tab。
P0-P6 流水线（预检 vault 回哺→画像→配置校验→AST 变换→等价验证→平台门禁→
交付）；CLI/PyQt 双壳同一内核（产物 SHA256 对等）。详见
docs/ptrade-deploy-pipeline-design.md 与 docs/ptrade-deploy-convergence-ledger.md。
```

### E3. docs/prompt_engineering.md 第 9 节后追加节

```markdown
## 10. 部署转换通道（qs-deploy，2026-10-11）

策略生成（本文件域）与部署转换分工：本地策略经 `qs-compile import` 转 PTrade
回测形态后，如需上模拟盘/实盘，再经 `qs-compile deploy <deploy.yaml>`（或 GUI
「转模拟盘/实盘」tab）完成部署化（状态持久化护栏/定时任务管控/平台门禁）。
智能体在策略生成阶段不承担部署化。
```

### E4. 设计文档 §10 落点修正（M5 呈备采信项）

§10 影响面中「main_gui.py 最小侵入 tab 注册」修正为「tab 注册落点
quantstudio/gui/main_window.py（+4 行 append-only）；main_gui.py 为薄启动器
零改动——侵入面小于原方案」。

## F. sha 回填清单（双推后）

- vault 条目 PD-0001~0005 fix_target 的 `pending-M8` → 主仓 commit sha；
- 主仓台账 §4 登记簿 5 行 pending-M8 → 同 sha；trading vault/ commit sha 另记；
- 台账 §1 轮次表无需动。

## G. ⑤用户确认呈报包（模板，V1 结果落定后填空即呈）

```
qs-deploy 六步⑤用户确认呈报
改动内容：内核 12 文件（P0-P6）+ CLI 壳(+43) + PyQt tab(+4+薄壳) + 25 测试 +
固件/横验证配置 + vault 6 条目 + 台账 + 设计文档 + 证据文档
验收：V1 [PASS/FAIL: ______] | V2 CONTRACT GATE PASS | V3 三路 parity |
V4 等价 diff | V5 门禁 | V6 横验证 3/3 | V7 闭环两轮（N 4→2，R≡0）
侵入面：cli.py +43 / main_window.py +4 零删改（diff 证明）；他线文件零卷入
请确认：是否批准 M8 双推+同步门执行？
```

## H. 今日时刻表（加速版）

| 时刻 | 动作 | 耗时 |
|---|---|---|
| 00:30-01:00 | 本执行包预制（本文件） | 已完成 |
| ~11:00 | V1：核对 daemon 收口→全量 pytest→黄金结果 | 60-120 min |
| V1 完成 | 补录证据文档 §8 + G 模板填空 → **即时呈报用户确认** | 10 min |
| 用户确认 | A-F 按序执行（add→commit→push→双远程核验→trading 同步门→sha 回填→E 文案落盘） | ≤60 min |
| 收口 | M8 呈验 + 收敛判据状态更新（轮 3 待真实使用周期） | 10 min |
```
