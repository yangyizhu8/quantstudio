# PD-0015 方案：状态文件路径硬编码 `/home/fly` 导致策略无法保存（券商禁用该路径）

> 立项依据：2026-10-30 20:09 实盘反馈——PTrade 模拟盘上传部署产物时报
> `更新策略失败，错误原因：/home/fly被禁止使用`（无法存为策略文件）。
> 状态：①方案（待②审计）——**未实施、未提交**。

## 1. 问题定义（用户侧现象 → 代码根因）

**现象**：把 `Demo_account_Real_account/全球资产轮动/strat_0c202b_trade.py` 上传到 PTrade 模拟盘，
平台在**保存阶段**即拒绝，错误原因 `/home/fly被禁止使用`（策略根本存不进去，谈不上运行）。

**根因链**：

| 环节 | 事实 | 位置 |
|---|---|---|
| ① 默认路径硬编码 | `DEFAULT_STATE_FILE = "/home/fly/notebook/{strategy_id}_state.pkl"` | `quantstudio/ptrade_deploy/config.py:25` |
| ② PERSIST 注入把它写进产物 | 生成 `with open('/home/fly/notebook/strat_0c202b_state.pkl', 'wb')` | 产物 L49 / L913（PERSIST 块） |
| ③ 券商静态扫描代码文本 | 代码里出现 `/home/fly/notebook/` 字符 → 拒绝保存 | 用户实测报错 |

**官方文档依据（本地平台知识库，逐字）**：

> 来源：`knowledge/platform/ptrade/01-API文档主页/001-page.md:2573`（同文 `002-index.md:2573`）
>
> `get_research_path` – 获取研究路径 `get_research_path()`
> 使用场景：该函数可在回测、交易模块使用
> 接口说明：该接口用于获取研究根目录路径，该路径为 `'/home/fly/notebook/'`。
> **注意事项：在部分券商里，会限制使用路径 `/home/fly/`，如果代码里面存在 `/home/fly/notebook/`
> 这个字符，会提示无法保存，需要替换成：`get_research_path()`，效果一样。**

官方持久化范式（同文件 368–401 行）：

```python
import pickle
NOTEBOOK_PATH = get_research_path()
...
with open(NOTEBOOK_PATH + 'hold_days.pkl', 'rb') as f:   # 读
    g.hold_days = pickle.load(f)
...
with open(NOTEBOOK_PATH + 'hold_days.pkl', 'wb') as f:   # 写
    pickle.dump(g.hold_days, f, -1)
```

⇒ **我们的产物用「字面量绝对路径」，官方要求「运行时 `get_research_path()` 拼接」**。
用户的券商属于「限制类」（文档所述「部分券商」），故命中该限制。

**旁证（同类平台约束，指向同一修法）**：`001-page.md:3341` 记载「ptrade 引擎**禁用了 os 模块**，
用户无法在策略中通过代码实现子目录创建」——所以修复只能用**字符串拼接**（不能 `os.path.join`），
且**不应引入子目录**（子目录需 `create_dir`）。

## 2. 目标

1. 产物代码中**不再出现** `/home/fly` 任何字面量；
2. 状态文件仍能正确落盘/恢复（用 `get_research_path()` 运行时取根目录 + 文件名拼接）；
3. **防再犯**：新增门禁，产物命中平台禁用路径前缀即 FAIL（fail-closed）；
4. 用户可配置的绝对路径不再被直写进产物。

## 3. 设计

### D1 配置层：默认值改为「纯文件名」

```python
# 改前
DEFAULT_STATE_FILE = "/home/fly/notebook/{strategy_id}_state.pkl"
# 改后（只给文件名；根目录运行时取）
DEFAULT_STATE_FILENAME = "{strategy_id}_state.pkl"
```

### D2 PERSIST 注入：运行时解析根目录

产物 PERSIST 块（写入/读取两侧）改为：

```python
# PTRADE-DEPLOY:PERSIST BEGIN
try:
    _PD_NOTEBOOK = get_research_path()      # 官方口径：运行时取研究根目录
except Exception:
    _PD_NOTEBOOK = ''
def after_trading_end(context):
    try:
        with open(_PD_NOTEBOOK + 'strat_0c202b_state.pkl', 'wb') as _pf:
            pickle.dump(g.state, _pf, -1)
    except Exception as _pe:
        log.warning('PTRADE-DEPLOY 状态落盘失败: %s' % _pe)
# PTRADE-DEPLOY:PERSIST END
```

读取侧（`initialize` 头部）同样改为 `_PD_NOTEBOOK + '<name>'`。

> 设计取舍：`_PD_NOTEBOOK` 在**模块级**求值一次（`get_research_path()` 可在回测/交易模块用，
> 官方示例即模块级 `NOTEBOOK_PATH = get_research_path()`）；`try/except` 兜底为空串（等价相对路径）
> 而非崩溃——与既有 PERSIST「失败仅告警不中断」纪律一致。

### D3 用户配置的路径不再直写

- 若 `persistence.state_file` 配置为**纯文件名**（无 `/`、无盘符）→ 照常使用（拼 `_PD_NOTEBOOK`）；
- 若配置为**绝对路径** → **P2 配置段 FAIL**，报错文案指引：「state_file 请使用纯文件名；
  平台禁用绝对路径（官方：部分券商限制 /home/fly/），根目录由 get_research_path() 运行时获取」。

### D4 P5 新增门禁：平台禁用路径检测

`gates.py` 新增一项检查 `platform_path_policy`：

```
产物代码文本中出现  /home/fly  → FAIL：「产物含平台禁用路径 /home/fly（部分券商禁止保存），
                                      请用 get_research_path() 运行时获取」
```

判型说明：**新增检测型**——新增能力开始报告真相，可能使「原本 PASS 的产物」变 FAIL（正是目的）。

### D5 文档/条目/契约表同步

- `docs/ptrade-deploy-pipeline-design.md` §P3/§6 补「状态路径运行时解析」口径；
- 条目 `PD-2026-0015`（kb_draft 起草 + kb_hook 校验）；
- 无需改契约表（`get_research_path` 属平台 API，需**补进契约表**：`backtest:true / trading:true /
  action:keep`，依据官方「可在回测、交易模块使用」）。

## 4. 改动范围

| 文件 | 改动 |
|---|---|
| `quantstudio/ptrade_deploy/config.py` | 默认值改为纯文件名；校验绝对路径 |
| `quantstudio/ptrade_deploy/templates/persist_template.py`（或 transformer 内联模板） | 注入 `_PD_NOTEBOOK = get_research_path()` |
| `quantstudio/ptrade_deploy/transformer.py` | PERSIST 注入改用运行时拼接 |
| `quantstudio/ptrade_deploy/gates.py` | 新增 `platform_path_policy` 检查 |
| `quantstudio/ptrade_deploy/contract_table.yaml` | 补 `get_research_path` 行 |
| `tests/test_deploy_persist_path.py`（新建） | 见 §5 |
| `docs/ptrade-deploy-pipeline-design.md` | 口径补记 |

**不动**：策略源码、既有产物（重转会覆盖）、P4 等价逻辑（PERSIST 属剥离白名单，不影响等价判定）。

## 5. 验收标准（5 项）

1. **目标用例重转**：`全球资产轮动` 重转后产物**不含** `/home/fly` 字面量（grep 断言 0 命中），
   且 PERSIST 块含 `get_research_path()`、P0–P6 **全 PASS**；
2. **P4 不破**：重转后 `P4_equiv = PASS`（PERSIST 块在剥离白名单内）；
3. **门禁负向**：构造含 `/home/fly/notebook/x.pkl` 的产物 → P5 `platform_path_policy` **FAIL**；
   构造纯文件名 → PASS；
4. **配置拒绝**：`persistence.state_file: /abs/path.pkl` → P2 FAIL 且文案指明改用纯文件名；
5. deploy 全套件绿（现 121 + 新增）；契约矩阵 `OK`；族机检全绿（含 PD-2026-0015）。

## 6. 影响面 / 风险 / 回退

| 项 | 评估 |
|---|---|
| 产物代码 | **变化**：PERSIST 块路径写法（预期且必要）；策略逻辑零变化 |
| 已部署产物 | 旧产物（含 `/home/fly`）需**重转**才可用；登记为「已知须重转」 |
| P4 等价 | 不受影响（PERSIST 块剥离后与源逐节点相等） |
| 风险 R1 | `get_research_path()` 在个别券商版本不可用 → `try/except` 兜底空串（退化为当前目录相对路径），不崩溃 |
| 风险 R2 | 用户在 `state_file` 写了绝对路径 → P2 直接拒绝（不静默改写） |
| 回退 | 单批 revert（4 文件 + 测试） |

## 7. 三型判定

- **数据修正型**（产物路径写法按官方正确口径变化：显式声明影响面 = 仅 PERSIST 块路径行；
  准确度只升不降 = 从「部分券商无法保存」改善为「可保存」）；
- 其中 D4 门禁为**新增检测型**（见 §3-D4 说明）——二型在方案阶段**分列，不混办**。

## 8. 知识反哺检索留痕（R1）

```
知识反哺：[命中] PD-2026-0011（PD/D3/PD-2026-0011.md，2026-10-30）——闭环管线五检查口径（本轮须同步登记新门禁）
知识反哺：[命中] PD-2026-0013（PD/D2/PD-2026-0013.md，2026-10-30）——契约表补行先例（get_research_path 照此补）
知识反哺：[无命中] PD 族检索（/home/fly、get_research_path、平台禁用路径、state_file），2026-10-30
```

**主检索面（平台文档库，命中决定性依据）**：

```
知识反哺：[命中] PTrade 官方文档 get_research_path（platform/ptrade/01-API文档主页/001-page.md:2573，2026-10-30）
         ——逐字：「在部分券商里，会限制使用路径 /home/fly/，如果代码里面存在 /home/fly/notebook/ 这个字符，
         会提示无法保存，需要替换成 get_research_path()，效果一样」
知识反哺：[命中] PTrade 官方持久化范式（同上 368-401）——NOTEBOOK_PATH = get_research_path() + 文件名拼接
知识反哺：[命中] PTrade 引擎禁用 os 模块（同上 3341）——修复只能用字符串拼接，且不引入子目录
```

## 9. 排期

①方案（本件）→②审计→③实施（4 文件 + 测试，约 1.5 小时）→④验收（5 项，含重转 + 负向）→⑤确认→⑥随批统推。

## 10. 用户侧临时绕过已实机验证成功（2026-10-30，修法方向获外部证明）

在正式修复出仓前，用户授权对**单份已转换产物**做临时手工改动并实机上传验证：

| 项 | 内容 |
|---|---|
| 改动文件 | `Demo_account_Real_account/全球资产轮动/strat_0c202b_trade.py`（备份留存 `_archive/*.bak-pd0015-原版-含homefly`，80,088 B） |
| 改动内容 | ① 模块级新增 `try: _PD_NOTEBOOK = get_research_path() / except: _PD_NOTEBOOK = ''`；② 落盘侧与恢复侧两处 `open()` 路径改为 `_PD_NOTEBOOK + 'strat_0c202b_state.pkl'` |
| 改动面 | 语义 3 处 + 说明注释；策略逻辑零改动 |
| **验证结果** | **用户实测：上传成功、策略可保存可运行（2026-10-30）** |

⇒ **修法方向（运行时 `get_research_path()` + 文件名拼接）已获实机验证**，本方案 §3-D1/D2 不是纸面推断。

**该临时改动不计入修复**：不入 git（目录在 `.gitignore` 内）、不申报为已修复；PD-0015 出仓后**必须重转覆盖**。
临时件内已埋 `PD-0015 临时手工改动` 注释块，便于识别与清理。

## 11. ★实证发现（用户验证过程暴露，反向修订方案）

手工改动第一版曾把说明注释写成「部分券商禁用 `/home/fly` 字面量」——**注释里本身含该字符**，
首次校验命中数 = 1（未归零）。券商扫描的是**代码全文（含注释与字符串）**，不是 AST 调用点。

对方案的两处反向修订：

1. **D4 门禁口径**：`platform_path_policy` 必须做**全文文本扫描**（含注释、字符串、docstring），
   **不得**只扫 AST 常量；六种口径都要覆盖（逐字 / 反斜杠 / 带空格 / 任意 `/home/<name>` / 全角斜杠 / `notebook/`）。
2. **D2 生成代码的注释文案**：注入 PERSIST 块的说明性注释**不得出现该路径字面量**
   （指路时用「研究根目录」等规避写法），否则门禁会把自己生成的注释判红。

