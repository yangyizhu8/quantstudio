# 闭环件 G 验收证据（③④轮，2026-10-08）

> 方案：`docs/loop-g-plan.md` rev2（`c0bd03f`，②审「修订后通过」→可直接进③）。
> 实施：G-1/G-3 本会话直执（`31023bf`）；G-2 委派 zcode_code（两新文件）+本会话补跑运行级证据。

## 判据① 维度档案落位 —— **PASS**

`knowledge/contracts/dimension-alignment.md`（六要素模板 + DIM-01 实例）；形态同构于 `api-semantics.md`
建档模板；登记两处：`knowledge/README.md` 导览行 + `registry.md` 交叉引用段（纯新增）。

## 判据② 机检三态单测 + CLI + 退出码 —— **PASS**

| 检查 | 结果 |
|---|---|
| `--help` | ✓ 可用（`--archive` / `--alignment-dir` / `--local` 三参数面） |
| 三态单测 | ✓ **26 passed**（候选/维护态告警/无提示三态+口径边界+只读常驻断言） |
| 真实档案实跑 | ✓ `[DIM-01] N=1 候选`，`数据源=人工清单`（磁盘零 S1 JSON，降级语义生效），**EXIT=0** |
| 退出码语义 | ✓ 0 无提示／3 有候选或告警／1 异常或解析失败（单测覆盖） |

**③ 实施过程事件（如实记录）**：G-2 委派方（zcode）会话被权限层硬拦（`No permission client configured
for Bash`），其自查命令未能执行——**运行级证据由本会话补跑产生**（上表三项+判据⑤），委派方仅产出
静态代码与推演，未虚报。

## 判据③ 首例走查结论落档 —— **PASS**

DIM-01 ETF 日线策略域：**N=1（阈值 3），候选态**——四象限首跑（2026-10-07，残差达标 0.013%）计入；
**如实记录其 S1 报告产物未随案归档**（以台账行+验收证据件为准，数据源=人工清单）；其余 ETF 日线策略
（`etf_hot_theme_rotation` 等）暂无 S1 首跑记录 → 标「暂无」，**不虚增 N**。

## 判据④ 不改既有行为 —— **PASS**

`python scripts/run_contract_gate.py --strategies` → **CONTRACT GATE : PASS**；本批改动面=新增 3 文件
（`dimension-alignment.md` / `check_dimension_alignment.py` / `test_check_dimension_alignment.py`）+
2 处登记行（README/registry 纯新增）——受控面零触碰。

## 判据⑤ 脚本只读 —— **PASS**

grep 扫描 `write_text`/`mkdir`/`unlink`/`os.remove`/`rename`/`rmtree`/`open(...,'w')` → **零命中**；
并已固化为常驻单测 `test_read_only_no_write_paths`（防未来回归）。

## 回退

新增三文件整删即回退（零既有文件依赖）；README/registry 登记行摘除即复原。

## 结论

**五判据全 PASS**——件 G（闭环七件最后一环）落地：维度对齐档案（机制+首例实例）+ 机检脚本（只提示不
自动宣布，与 lifecycle「唯一硬门」语义一致）+ 首例走查。**⑤ 待用户确认三项**：①宣布动作确认权归属
（建议=用户）；②N 阈值沿用 3；③「首跑」定义口径澄清（同策略 id 只计首次合格记录）。
