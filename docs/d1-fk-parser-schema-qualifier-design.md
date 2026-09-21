# D1 方案：「REFERENCES main.表」限定名解析容错（schema 安全闸误报修复）

- 日期：2026-09-21｜归属：dev｜时限：今日 21:00 EtfAdj 夜窗前｜状态：**待审**
- 现象：daemon 每 5 分钟周期 writer init 被 `partial_or_mixed` fail-fast 拒绝（15:44 起 4 连败），复产停摆

## 一、根因（证据锚点）

`qfq_schema_contracts.py:354-358`：
```python
m = re.search(r"REFERENCES\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)", text, re.IGNORECASE)
if not m:
    continue          # ← 解析失败静默吞，该 FK 从 actual 列表消失
```

DuckDB 1.4.5 渲染的 `constraint_text` **带 schema 限定**：
`FOREIGN KEY (cutover_id) REFERENCES main.qfq_source_cutover(cutover_id)`
正则匹配到 `main` 后遇 `.` 无法继续 → **不匹配 → continue** → FK 漏报。

卷宗实证：`qfq_active_cutover` FK `exp=1 / parser-act=0`；**旧库 raw-text 同样带 `main.`**
——即本缺陷**非新库独有**，是两个库都中招的解析器缺陷（新库因 D2 丢约束而先触发闸门，暴露了 D1）。

## 二、语义层次确认（不改指纹语义的论证）

| 事实 | 含义 |
|---|---|
| 指纹存**结构化 FK 列表**（`referenced_table`/`referenced_columns`/`columns`），非原始文本 | 解析器是 actual 侧唯一入口 |
| 正则失败 → `continue` → FK 从 actual 消失 | 现状 = **错报缺失**（actual 少报，非指纹多要） |
| 修复后 actual 恢复真实 FK 集 | **如实报告**，exp 侧要求不变 |

⇒ **只改解析容错，不放宽任何校验维度**（安全闸属性保持：FK 仍须存在、列集仍须匹配）。
⇒ 指纹存值不变（结构化字段无变化），**不触发矩阵哈希追认**。

## 三、改动范围（最小）

**单函数**：`_table_foreign_keys`（:336-365）正则改为**容忍可选 schema 前缀**：
```python
r"REFERENCES\s+(?:[A-Za-z_][\w]*\s*\.\s*)?([A-Za-z_][\w]*)\s*\(([^)]*)\)"
```
即 `REFERENCES [<schema>.]<table>(cols)` 两形态通吃；`m.group(1)` 仍为**裸表名**（与指纹存值口径一致）。

**附加（同函数，防同类静默）**：
- 匹配失败时**不再静默 continue**，改为 `logger.warning` 记录 `table` + 原文片段——
  **不改变行为**（仍跳过该条），但把「静默吞」变为「可观测」。这是**可诊断性**修复，非校验放宽。

**不改**：指纹定义、比对逻辑、`partial_or_mixed` 判定阈值、任何 DDL/数据。

## 四、验收标准

| # | 项 | 判据 |
|---|---|---|
| V1 | 双形态契约测试 | 新增 `tests/test_qfq_fk_parser.py`：带 `main.` / 不带 `main.` / 多列 FK / 无 FK **四形态**均正确解析；红态先行（当前带 `main.` 形态必红） |
| V2 | 新旧库实跑 | 对两库调 `_table_foreign_keys('qfq_active_cutover')` 均返回 1 条（当前为 0） |
| V3 | 回归 | 29（锁/委托/writer）+ 94（GUI）+ **qfq 契约套件**全绿，零新增失败 |
| V4 | 闸门语义未放宽 | 构造「真缺 FK」场景，断言仍判 `partial_or_mixed`（**负向用例**，防把容错改成放行） |

## 五、回退条件

- 单函数改动（含正则一行 + warning 一处）；回退=还原该函数；
- V4 负向用例失败 ⇒ 立即回退（说明容错过度）；
- 落地需 daemon 进程重载方生效（D1 是代码，运行中进程读不到）——**重载时点由总调度令**。

## 六、D2 的边界（非代码，另件）

D1 修的是**解析器**；新库**真实缺失**的 NOT NULL/DEFAULT/FK 由 **D2（数据修复）** 承担——
二者互补：D1 让闸门能看见真 FK，D2 让 16 张受管表**真的具备**那些约束。
**只做 D1 不做 D2** ⇒ 闸门如实报告缺失 ⇒ 仍拒；**只做 D2 不做 D1** ⇒ 约束齐了但解析仍漏报 ⇒ 仍拒。
**两件必须都落地**（D2 立即生效，D1 待进程重载）。

## 七、推送回报与 CI（2026-09-21 补录）

- **推送**：`ff412fc..93d9382`，**三方一致**（本地 / quantstudio-plus / quantstudio 双远程 HEAD 逐位同）；
- **CI**：contract-gate **success**（矩阵哈希与契约门未因本件变动而红——本件不触 wrapper 模板，无同批哈希义务）；
- 提交构成：`quantstudio/pipeline/qfq_schema_contracts.py`（+15/-1）+ `tests/test_qfq_fk_parser.py`（新增）。

## 八、同族隐患排查结论（三条件之①，证据归档）

全仓 `constraint_text` / `canonicalize_default` 消费点**仅 `qfq_schema_contracts.py`**（grep 全 quantstudio/ 命中 10 处，全部本模块）：

| 消费点 | 失败行为 | 结论 |
|---|---|---|
| FK 解析 `:354-366` | `continue` → **静默吞**（FK 从 actual 消失） | **本件修复对象** |
| CHECK 解析 `:401-402` | `expr = m.group(1) if m else text` → **降级用原文**（不静默） | **无同类静默缺陷**；渲染敏感但失败可见。实态佐证：`detect_schema_status=COMPLETE_2_1` ⇒ CHECK 渲染与指纹存值一致 |
| `canonicalize_default` `:119`（消费 :266/:330） | 规范化函数，非版本敏感模式匹配 | **无隐患** |

**结论：除 FK 点外无其它版本敏感文本解析点**（全清已逐个给结论）。

## 九、引号形态残留登记（三条件之②）

**实测（1.4.5 scratch 库）**：含大小写标识符渲染**剥离引号**——
`CREATE TABLE p_mixed ("Id" ...)` → `constraint_text = FOREIGN KEY (pid) REFERENCES p_mixed(Id)`（无引号）。

| 判据 | 实测 |
|---|---|
| 1.4.5 是否渲染 `"main"."tab"` 形态 | **否**——引号在 constraint_text 中被剥离 |
| 受管表名形态 | 全为简单词（`re.fullmatch(r"[a-z_][a-z0-9_]*")` 无一例外） |
| FK 引用表名形态 | 全为简单词 |

⇒ **双重不可能触发**（渲染不产生 + 标识符形态不可能）。**在册残留、不扩正则**（禁顺手加固扩大改动面）。

## 十、docs 同步判定（六步第 6 步完整性要求，判定记录）

**判定：免同步。**

- 本修复属 **QFQ 安全闸内部**（`qfq_schema_contracts` 的 FK 文本解析容错），**不触及**：
  `README.md` 策略工具箱/提示词工程章节、`docs/strategy_toolbox.md`、`docs/prompt_engineering.md` 的任何表述面；
- 调用方与契约面**零变化**（指纹存值/比对逻辑/partial_or_mixed 判据均不变）；
- 故三处文档无需更新——**判定记录随卷宗入档**（不可缺）。

## 十一、尾巴登记（待 9/22 空档窗口）

**3 条 qfq 库依赖测试**（`test_production_db_invariant` / `test_report_equals_production_refused` / `test_golden_300750_range_none_raw`）
在 daemon 运行期因**独占持库**而失败（实证 `PermissionError: File is already open in ... PID 1564`）。

- 归因：**环境性**（daemon 正常运行时跑库依赖测试），与本件无因果；
- 复验条件：9/22 daemon 空档窗口复跑**全绿**后，D1+D2 案方判**全案关单**（补齐「验证器过=闸过」最后一格）；
- bak（49.82GB）处置令不变：待 9/22 晨检三判据（无 invalidated 首现 + float_share 正常轮 + 六项回报全达）经总调度确认后单独删。
