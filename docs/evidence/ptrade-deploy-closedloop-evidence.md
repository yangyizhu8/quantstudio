# PD-0011 验收证据：qs-deploy 闭环管线建成（③实施 + ④验收，2026-10-28）

> 方案：`docs/ptrade-deploy-closedloop-design.md`（②审通过；含两点附加：a 消费方记录、b 读到不相关⇒未救）
> 实施：纯增量（3 脚本 + 1 测试 + 台账 §4.9 + 条目 PD-2026-0011）；**`quantstudio/ptrade_deploy/` 零 diff**
> 本轮沉淀：PD-2026-0011

## 1. 七项验收逐条（方案 §5）

| # | 标准 | 实测 | 结论 |
|---|---|---|---|
| 1 | 巡检在真实仓运行、输出五项读数 | ① 收敛 PASS ② `entries=10 / source=trading-knowledge / degraded=False` ③ 族机检 PASS ④ R1/R4 PASS ⑤ L2 未提供（WARN）→ **退出码 2** | ✅ |
| 2 | 收敛机检对 §4.1–4.9 判 PASS | N 序列（轮3→9）`1→0→1→1→1→0→0`；R 全 `0`；末尾连续 R=0 = **7 轮**；末轮 N=**0** → **PASS** | ✅ |
| 3 | 负向对照：R>0 / N 非单调 → FAIL | `test_convergence_r_nonzero_fails`、`test_convergence_n_not_zero_fails`、`test_convergence_empty_ledger_fails_closed` | ✅ |
| 4 | G1 对真实 FAIL 报告生成起草命令 | 对 `全球资产轮动` 真实报告（P5 `contract_full: set_limit_mode 未门控`）生成含 `--symptom` 的可执行 `kb_draft` 命令 + 5 项待填清单；**族库文件数不变**（不落盘） | ✅ |
| 5 | 零行为变更 | `ptrade_deploy/` status `[]`（零 diff）；deploy 套件 **84/84**；契约矩阵 `OK` | ✅ |
| 6 | R1/R4 缺失 → WARN | 自测 `test_r1_r4_missing_warns`；补台账 §4.9 后真实巡检 ④ 转 **PASS**（r1=True, r4=True） | ✅ |
| 7 | 族机检仍合规 | `kb_hook --strict`：**11 件 / 不合规 0 件**（新增 PD-2026-0011） | ✅ |

## 2. ②审两点附加的落实证据

### a) L2 命中判定由消费方记录（禁起草方自证）

| 用例 | 输入 | 结果 |
|---|---|---|
| 缺 `confirmed_by` | 确认行仅「确认：已核」 | **未确认**（reason 指明缺 confirmed_by） |
| 起草方自证 | `confirmed_by=kb_draft 起草方` | **未确认**（reason 含「自证」） |
| 未引用报告 | `confirmed_by=总调度`（无 report=） | **未确认**（reason 含「报告」） |
| 合规确认 | `confirmed_by=总调度` + `report=<路径>` | 进入相关性判定 |

### b) 「读到条目但内容不相关 ⇒ 未救」负向

| 材料 | 结果 |
|---|---|
| **真实**报告（`全球资产轮动` P5 FAIL，签名 `contract_full: set_limit_mode 交易环境不可用且未门控`）+ 条目 **PD-2026-0004**（未知 API 误报） | **未救** —— 「条目 trigger 与失败签名无标识符重叠（CJK 重叠 0），通用词不计」 |
| 合成：报告仅含通用词（嵌套/门禁/注入/局部函数） | **未救** —— 标识符重叠 0（`test_l2_generic_cjk_words_only_not_saved`） |
| 合成：主题确实相关（未知 API 误报 ↔ `filter_stock_by_status` 未知 API） | **已救** —— 标识符重叠含 `api`/`p5`（`test_l2_relevant_entry_saved`） |

## 3. 验收中自我修正（如实登记）

**L2 初版判据过松，实测出现假『已救』**：
- 材料：真实报告（实体含 `notes`：`P3 警告: 嵌套复合语句…`、`P5 未知 API: get_order`）× 条目 PD-2026-0004；
- 初版结果：`已救`，重叠 8 token —— 但 token 全为 `{api, notes, stdlib, 函数, 嵌套, 未知, 注入, 门禁}`，
  **条目主题（未知 API 误报）与失败主题（set_limit_mode 未门控）实为无关**；
- 根因：①签名取整份报告（含 `notes` 通用提示）②判据仅用 CJK 二元组，通用词即构成重叠；
- 修正：**签名只取 FAIL 段**（`signature_from_report`）＋ **标识符≥1 且 CJK≥2**（`_ident_tokens`）；
- 修正后同一材料正确判 **未救**（标识符重叠 0）；并补两条回归用例固化
  （`test_signature_from_json_report_uses_fail_stage_only`、`test_l2_generic_cjk_words_only_not_saved`）。
- 定性：**③实施内的判据自我修正**，未外溢为转换行为变更（`ptrade_deploy/` 仍零 diff）。

## 4. 三态退出码实测

| 场景 | 退出码 | 依据 |
|---|---|---|
| 健康 | 0 | 五检查全 PASS 且提供合规 L2 材料（自测路径） |
| 仅 WARN | **2** | 真实仓、未提供 L2 材料（本轮实测值） |
| 硬缺口 | **1** | 收敛 FAIL / 族机检不合规 / 回哺 degraded / **L2 判未救**（`test_closedloop_l2_unsaved_forces_hard_fail` + 真实材料实测） |

## 5. 自测总览

`tests/test_deploy_closedloop.py`：**17 passed**（收敛 4、R1/R4 2、L2 6、G1 2、签名提取 1、退出码 2）。

## 6. 交付物

| 类型 | 路径 |
|---|---|
| G3 收敛机检 | `scripts/check_pd_convergence.py` |
| G4+G5+G2 巡检 | `scripts/check_deploy_closedloop.py` |
| G1 失败→起草命令 | `scripts/kb_draft_from_deploy.py` |
| 自测 | `tests/test_deploy_closedloop.py` |
| 台账 | `docs/ptrade-deploy-convergence-ledger.md` §4.9（轮 9，N₉=0/R₉=0） |
| 条目 | `knowledge/ptrade-deploy/PD-2026-0011.md`（closed / closed_round 9） |

## 7. 下一步（按用户裁定顺序）

闭环管线⑥出仓后，才启动 **PD-0012（`is_trade()` 语义契约冲突）** 与 **PD-0013（契约表补 `get_order`）**
的①方案——本件**未触碰**这两处框架缺陷代码。
