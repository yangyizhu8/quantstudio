# A 轨 provenance 草案 · 独立字符串键（2026-11-17）

> **状态**：**草案，未实施**。JSON **未改**（`git status` 对 `ptrade-api-signatures.json` 空）。
> **依 DRIFT-N7-r3/r4**：A 轨仍为候选；独立字符串 provenance 键为最小设计方向；**不批准 JSON 改动**。

## 一、结构前提（实测）

| 项 | 实测值 |
|---|---|
| `runtime_corrections` 值类型 | **8 键全部 `str`** |
| 消费者 | **仅 1 件**：`ptrade-api-signatures.json` 自身（`quantstudio/`／`scripts/`／`tests/` **无 Python 读该键**） |
| ⇒ 结论 | `set_backtest_is_trade` **是字符串**，**不能**在其中「加对象字段」；结构改动须通过**新增键**实现 |

## 二、拟新增键（**草案，二选一**）

### 方案 甲（推荐）：`runtime_corrections` 内新增**字符串**键

```json
"runtime_corrections": {
  "...既有 8 键，一字不改...": "...",
  "is_trade_doc_basis": "Registered from PTrade public documentation (001-page 523/3299, 002-index 523/783/3299, portability_rules.py:26-29). Documentation-basis registration only: measured_on_target=false; no cross-version signature guarantee. Supersedes T3 merged restatement for is_trade ONLY; set_backtest restatement retained verbatim and NOT withdrawn. Skill-side validators and local_only_symbols still block is_trade (A-track pending, NOT lifted)."
}
```

| 优点 | 风险 |
|---|---|
| 值与既有键**同类型（str）**⇒ **不改变既有键的类型与内容** | 信息挤在一行字符串，可机读性弱 |
| 原 `set_backtest_is_trade` **一字不改** ⇒ 天然满足「保护旧转述」 | 需人工读长句 |

### 方案 乙：顶层新增**并行对象**键

```json
"symbol_basis": {
  "is_trade": {
    "doc_basis": [".../001-page.md:523", ".../001-page.md:3299", ".../002-index.md:783", "portability_rules.py:26-29"],
    "measured_on_target": false,
    "cross_version_guarantee": false,
    "supersedes": "runtime_corrections.set_backtest_is_trade (for is_trade only)",
    "retained_verbatim": "runtime_corrections.set_backtest_is_trade",
    "blocks_pending": ["local_only_symbols", "validate_agent_strategy.py:762/995/1005"]
  }
}
```

| 优点 | 风险 |
|---|---|
| 结构化、可机读 | **新增顶层键** ⇒ 若有严格 schema 校验器会报未知键（须先核 `validation_policy`／消费者） |
| 不触碰 `runtime_corrections` 子结构 | 仍属 JSON 结构变更（需 A 轨正式批准） |

## 三、**必须如实保留**的三项（任一选项均适用）

| # | 项 | 措辞要求 |
|---|---|---|
| 1 | **未目标版本实测** | 必须显式 `measured_on_target=false`；**不得**宣称「已验证/兼容通过」 |
| 2 | **旧转述** | `runtime_corrections.set_backtest_is_trade` **原字符串一字不改**；**不连带撤回** `set_backtest` |
| 3 | **剩余 BLOCK** | 必须写明 `local_only_symbols` 仍列 `is_trade`、skill 三处校验器（`validate_agent_strategy.py:762/995/1005`）**仍 BLOCK、未解除** |

## 四、消费者兼容核（落地前必做）

| 步骤 | 内容 |
|---|---|
| C-1 | 确认无 Python 代码读 `runtime_corrections`（**已实测：0 处**） |
| C-2 | 若采方案乙，须核是否存在 **JSON schema 严格校验**（禁未声明键） |
| C-3 | 若未来新增消费者，须**按 `str` 读取**既有键；对象键须单独声明 |
| C-4 | 落地须与 A 轨方案文档 **同批**，且**不得**在 B 轨提交中夹带 |

## 五、本轮状态

| 项 | 值 |
|---|---|
| JSON | **未改** |
| A 轨 | **仍为候选** |
| 本件 | **草案**，供 A 轨方案轮引用 |

---

## 六、2026-11-17 文档纠正（DRIFT-N7-r5）

| # | 原表述 | 纠正为 | 性质 |
|---|---|---|---|
| A-1 | `2011-page` 523/3299 | **`001-page` 523/3299** | **笔误**（文件实为 `001-page.md`），全量替换 1 处 |
| A-2 | 「⇒ 零结构变更、**零消费者破坏**」 | 「⇒ **不改变既有键的类型与内容**」 | **过度表述**：类型不变 ≠ 无任何破坏 |
| ~~A-3~~ | ~~「新增字符串型键零破坏」~~ | **该行作废（幻影纠正）** | ⚠️ **我方错误**：正文**从无**「零破坏」表述——C-1 原文即为「确认无 Python 代码读 `runtime_corrections`（**已实测：0 处**）」，本已限定范围；我方误把不存在的问题写进纠正表，**已撤回该行** |

⚠️ **实际纠正＝2 处**（A-1 笔误、A-2 过度表述）；**A-3 已撤回**（幻影纠正，正文原本已限定范围）。
**JSON 未改**（A 轨仍为候选）。

**自查说明**：A-3 行是我方在写纠正表时**凭对草稿的印象**填入，未先回读正文核实 ⇒ 记入自检教训「写『已纠正 X』前必须回读 X 是否真的存在」。
