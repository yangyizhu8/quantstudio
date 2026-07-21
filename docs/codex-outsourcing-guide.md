# Codex 外包手下 · 使用说明

> 把本机 Codex CLI（`codex exec` 非交互模式）封装成随叫随到的外包工具。
> 实测环境：codex-cli 0.137.0 / Windows 11 / Git Bash / provider=custom(kimi-for-coding, 本地中转 127.0.0.1:15721)
> 实测日期：2026-07-19

## 一句话唤起（对 Claude 说）

| 你说 | Claude 做 |
|------|-----------|
| "用 codex 审查 `<文件夹>`" | 跑 `scripts/codex_review.sh`，拿到报告后逐条核实真伪，只交付确认的真问题 |
| "让 codex 画一张 `<描述>` 的图，`<宽x高>`" | 跑 `scripts/codex_image.sh`（⚠️ 当前配置不可用，见下文限制） |

---

## 封装 1：代码审查 ✅ 已实测通过

```bash
bash scripts/codex_review.sh "<目标文件夹>" [输出文件] [--watch]
# 示例（--watch 弹出实时窗口，亲眼看 Codex 干活）
bash scripts/codex_review.sh "D:/miniQMT策略实盘/QuantStudio/quantstudio/pipeline" "" --watch
```

**实时观看 Codex 交互**（v3：三种模式）：
- **`--tui`（推荐）**：弹出 **Codex 原生对话框**跑任务——原生渲染、全自动（read-only + 无审批）、跑完可直接在框内追问。脚本立即返回并打印会话文件路径，报告用配套工具从会话存档提取：
  `python scripts/codex_last_report.py "<会话文件(cygpath -w 转换)>" --wait 1800 -o 报告.md`
  注意：TUI 不认 `--skip-git-repo-check`/`-o`（exec 专属）；非 git 目录首次会在框内弹信任确认，选继续即可。
- **`--watch`**：弹终端窗口 tail 日志（exec 模式，纯旁观原始输出流）。
- **不带旗标**：exec 静默跑，输出落 `<报告名>.log`，随时 `tail -f`；结束时日志尾有"任务已结束"标记。
- ⚠️ 报告/日志不要放进被审查的文件夹内，codex 会把它们当审查对象多读一遍。

等价裸命令（任何 shell 可用）：

```bash
codex exec --skip-git-repo-check -s read-only -C "<目标文件夹>" -o "<报告文件.md>" "<审查提示词>"
```

关键参数：

| 参数 | 作用 | 为什么必须 |
|------|------|-----------|
| `--skip-git-repo-check` | 允许在非 git 目录运行 | 本项目不是 git 仓库，不加直接拒跑 |
| `-s read-only` | 只读沙箱 | 保证审查绝不改代码 |
| `-C <dir>` | 指定工作根目录 | 让 Codex 只看目标文件夹 |
| `-o <file>` | 最终回复落盘 | 报告直接进文件，不用从终端噪声里抠 |

提示词要点（已固化在脚本里）：按【高/中/低】三档、每条带 `文件:行号` + 理由、没把握不要报、无问题写"无"。

**完整流程 = Codex 审查 → Claude 逐条核实 → 只保留真问题**。
实测一轮：Codex 报 15 条 → 核实后确认 11 条、剔除误报 3 条、修正理由 2 条（见 `output/codex/review_20260719_024325_verified.md`）。**Codex 的结论不能直接采信，核实环节不可省。**

实测耗时：2 个 Python 文件（约 320 行）≈ 6 分钟。

## 封装 2：图片生成 ⚠️ 当前配置不可用（封装已就绪）

```bash
bash scripts/codex_image.sh "<画面描述>" [宽x高] [输出文件夹]
# 示例（要高清直接写像素）
bash scripts/codex_image.sh "赛博朋克风格的上海外滩夜景" 3840x2160
```

**实测结果：失败。** 根因（已定位，非脚本问题）：

1. 本机 codex 配的是自定义 provider（`~/.codex/config.toml`：`model_provider="custom"` → kimi-for-coding，走本地中转 127.0.0.1:15721）。**内置 `image_gen` 工具只在 OpenAI 官方后端下加载**，当前会话工具列表里根本没有它。
2. Codex 自行兜底直连 `api.openai.com` 也失败：本机网络连接超时（`UND_ERR_CONNECT_TIMEOUT`）。

**启用条件（满足其一即可，脚本无需改动）：**
- `codex login` 改用 ChatGPT 官方账号登录，且网络可达 OpenAI（需代理）；或
- 配置可用的 `OPENAI_API_KEY` + 可达网络（Codex 会走图片 API 兜底）。

替代方案：需要出图时改用其他本机可用的文生图渠道（如智谱 CogView、即梦等国内 API）。

---

## 踩坑记录（重要）

1. **非 git 目录必须加 `--skip-git-repo-check`**，否则 codex exec 直接拒绝运行。
2. **Windows 沙箱限制**：Windows 沙箱实现已被官方移除（`features list` 里 `experimental_windows_sandbox removed`）。`-s read-only` 下复杂 PowerShell 命令会被 policy 拦截（`Get-ChildItem | Where-Object`、`cmd /c` 都被拒），Codex 只能用简单别名命令（`dir -Recurse -Name`、`cat`）绕行——能跑通，但会浪费几轮试错时间。审查任务可接受；如需 Codex 执行复杂命令，考虑 `-s danger-full-access`（信任场景下）。
3. **中文乱码**：Codex 用 `cat` 读 UTF-8 文件时中文注释显示为乱码（GBK 控制台问题），不影响代码逻辑审查，但它读不懂中文注释的意图，审查上下文会打折。
4. **审查结论必须核实**：实测误报率约 3/15。典型误判——把 pandas float64 除零说成"ZeroDivisionError 崩溃"（实际得 inf）；不了解项目数据约定就报"时间戳精确匹配可能查空"（实际库里就是午夜对齐）。核实手段：比对源码 + 小段代码实测 + 查库验证。
5. **`-o` 输出文件是刚需**：codex exec 的终端输出混着 thinking、工具调用、ERROR 噪声，直接解析很痛苦；`-o` 只落最终回复，干净。
6. **无害噪声**：启动时会报几条 `failed to load skill ... SKILL.md: invalid YAML`（~/.codex/skills 下老文件）和 MCP notion `invalid_token`，均不影响执行，忽略即可。
7. **首次在新目录运行**会往 `~/.codex/config.toml` 写 `trust_level = "trusted"` 条目，属正常行为。
8. **模型即 provider 所配模型**：当前审查由 kimi-for-coding 完成（不是 GPT）。质量尚可，但档位判断偏保守（把模式性安全风险放中档），核实时需自行复核档位。

## 产物清单

| 文件 | 说明 |
|------|------|
| `scripts/codex_review.sh` | 代码审查封装（已实测 ✅） |
| `scripts/codex_image.sh` | 图片生成封装（就绪，待官方后端 ⚠️） |
| `output/codex/review_*.md` | Codex 原始审查报告 |
| `output/codex/review_*_verified.md` | Claude 核实后的真问题报告 |
| `output/codex/images/` | 图片输出目录（预留） |
