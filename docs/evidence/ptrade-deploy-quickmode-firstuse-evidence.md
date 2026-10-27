# PD-0008 快捷模式真实首用验收证据（T1，2026-10-27）

> 触发：2026-10-27 闭环盘点 → T1「快捷模式真实首用等价验证」经总调度授权即刻推进。
> 背景：PD-0008（GUI 快捷模式 + 统一输出根 `Demo_account_Real_account`）2026-10-21 出仓，
> 但**真实首用从未发生**（`Demo_account_Real_account` 目录不存在），功能闭环口径缺最后一环。
> 本件为**真实产物**上的首用验收，非单测级验证。

## 1. 执行链路（内核 quickmode 全链 + GUI 等价）

| 段 | 实测读数 | 来源 |
|---|---|---|
| ① discover | **36** 个可部署产物（中文目录名在列：`CANSLIM突破成长选股策略`/`ETF动量`/…） | `quickmode.discover_strategies()` |
| ② 素材选定 | `ashare_manual_pool_2d_momentum_top2`（真实 PTrade 产物） | `output/ptrade_export/…_ptrade.py` |
| ③ build+write | `output/.qs-deploy-quick/ashare_manual_pool_2d_momentum_top2.yaml` | `quickmode.write_quick_config()` |
| ④ run_deploy（CLI 路径） | **PASS**，P0–P6 七阶段全 PASS | `run_deploy(cfg).to_dict()` |
| ⑤ run_deploy（GUI 真点按钮） | status_label=「完成（PASS）」，耗时 0.2s | `DeployTab._on_quick_clicked()`（PyQt6 offscreen） |

**P0 回哺来源读数**：`P0 vault 回哺来源：trading-knowledge`（notes 内）——与 PD-0010 修复后口径一致。

## 2. 产物落盘（统一输出根）

`Demo_account_Real_account/ashare_manual_pool_2d_momentum_top2/`（**目录名保留 ASCII 原名**）：

| 文件 | SHA256（前 16） |
|---|---|
| `ashare_manual_pool_2d_momentum_top2_trade.py` | `1dcead9c6ec61aca` |
| `report.md` | `26ed7c87332daf1a` |
| `runbook.md` | `897bc3845a6d7569` |

## 3. 双端对等（CLI ≡ GUI，产物逐位一致）

同素材、同档位（`daily_eod_1450`）：

| 路径 | 产物 SHA（trade.py / report.md / runbook.md） | 与 CLI 侧一致 |
|---|---|---|
| GUI（真点「一键转换」） | `1dcead9c6ec61aca` / `26ed7c87332daf1a` / `897bc3845a6d7569` | **sha_equal = True** |
| CLI（quickmode 全链） | `1dcead9c6ec61aca` / `26ed7c87332daf1a` / `897bc3845a6d7569` | — |

⇒ 单一内核 + 双薄壳在**真实产物**上成立（非仅单测）。

## 4. 二次转换归档验证（`_archive/vNNN`）

- 第 1 次转换 → 落三件（无归档）；
- 第 2 次转换 → 生成 `_archive/v001/`（上一版三件，SHA 与首轮**逐位相同** ⇒ 转换确定性成立）；
- 第 3 次（GUI）→ 生成 `_archive/v002/`；
- 归档序号**递增不覆盖**（v001、v002 并存），符合 `keep_versions=null`（不限份数）语义。

## 5. 负向对照：高级模式（手写 deploy.yaml）行为不变

| 检查 | 读数 | 判定 |
|---|---|---|
| 夹具 `deploy_daily_eod.yaml` 的 `output.root` | `output/deploy`（**未落统一根**） | ✅ 高级模式输出根不受快捷模式影响 |
| 夹具 `keep_versions` | `null` | ✅ 默认语义未变 |
| GUI 高级页空输入提示 | 「请先选择 deploy.yaml」 | ✅ PD-0008 契约保持 |
| 快捷页位置 / 默认档位 | tab0=「快捷模式」；`daily_eod_1450` | ✅ 未漂移 |
| `strategy_id` 派生 | `ashare_manual_pool_2d_momentum_top2`（ASCII 直通） | ✅ 稳定 |

## 6. 缺陷发现

**本轮未暴露任何缺陷**——首用全链 PASS、双端一致、归档正确、负向对照无漂移。
（过程中 3 次脚本级报错均为**探针脚本自身问题**，非产品缺陷：`discover_strategies` 返回
dict 而非 Path；Qt 绑定为 PyQt6 而非 PyQt5；`load_config_from_yaml` 入参为**文本**而非路径。
三次均在探针侧修正后通过，产品代码零改动。）

## 7. 首用登记

- **N₈=0**（无新发现缺陷）、**R₈=0**（无复发）——本轮为**首用验证轮**，非缺陷修复轮；
- 收敛台账 §4.6 后新增 §4.7「轮 8（首用验证）」登记；
- 首用产物属**验证产物**，验收后清理（不入仓、不污染工作区；`.gitignore` 已含 `/Demo_account_Real_account/`）。

本轮沉淀：PD-2026-0010（回喂 T2 负向对照读数入该条证据节）、PD-2026-0008（首用结论回填 fix_target）
