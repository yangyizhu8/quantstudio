# PD-0008 验收证据：GUI 快捷模式 + 统一输出目录 + P5 误报修复

> 对应方案：`docs/ptrade-deploy-quickmode-design.md`（②审通过 + 三条件已 folded；D3 裁定 A）。
> 实施快照：`c111c9d1f843d6f541a2c45b7cee9d2582799ad3`（stash stored，P5 轮写前快照）。
> 状态：④验收 PASS（本文件）→ 待⑤用户确认。

## 1. ②审三条件落地

| 条件 | 落地 |
|---|---|
| ① D4 `keep_versions` 口径定稿 | 方案 §3-D4 删去自相矛盾句，定稿为「`null`/缺省=不限份数、`0`=关闭归档、`N>0`=保留最近 N 份」；`config.OutputConfig` 校验拒绝负数/布尔；归档目录改 `_archive/vNNN/`（序号不用时间戳，保 parity 确定性） |
| ② 目录名长度上限收紧 | `deliver.safe_dir_name` 强制 `MAX_DIR_NAME_LEN = 40`（防 Windows MAX_PATH 260）；`test_safe_dir_name_keeps_chinese_and_caps_length` 断言 200 字符名截断为 40 |
| ③ 快捷 yaml 对拍用例 | `test_quick_equals_manual_yaml_sha`：快捷 yaml 与等价手写 yaml → 产物 SHA256 逐位一致 |

## 2. 实施明细

| 文件 | 改动 |
|---|---|
| `gates.py`（commit 1） | P5 误报修复：`_local_names` 补 7 类作用域绑定（函数参数/for 目标/with as/except 名/walrus/推导式/解包）+ `ENGINE_GLOBALS={g}` 豁免；`_add_target_names` 辅助 |
| `config.py` | 新增 `OutputConfig(root/dir_name/keep_versions)` + `DeployConfig.output`；`load_config` 解析与校验（keep_versions 非负整或 null） |
| `deliver.py` | `REPO_ROOT` / `DEFAULT_OUTPUT_ROOT_NAME=Demo_account_Real_account` / `safe_dir_name()`(≤40) / `resolve_out_root()`（显式参数 > cfg > 缺省仓库根，相对路径按仓库根解析）/ `_archive_previous()`（三档语义 + vNNN 序号）；`deliver()` 改用 cfg.output |
| `quickmode.py`（新） | `discover_strategies()`（递归 + `_verify*/_probe*` 过滤 + 同目录取 mtime 最新）/ `derive_strategy_id()`（ASCII 直通；含非 ASCII → 名称 sha1 前 6 位后缀，稳定）/ `build_quick_config()` / `write_quick_config()` |
| `gui/tabs/deploy_tab.py` | 双页（快捷/高级）：策略下拉 + 档位下拉（默认 daily_eod_1450）+ 一键转换；高级页控件与 `_on_start_clicked` 行为原样保留；`run_deploy_kernel` 仍为唯一内核调用点 |
| `tests/deploy_fixtures/*.yaml`（7 个） | 显式钉 `output.root: output/deploy`（保既有用例路径确定性、零回归） |
| `.gitignore` | 新增 `/Demo_account_Real_account/`（部署产物不入库） |
| `tests/test_deploy_gates_unknown.py`（新） | P5 正负双测 9 用例 |
| `tests/test_deploy_quickmode.py`（新） | 15 用例（发现/派生/目录/输出根/对拍/端到端/归档三档/GUI 冒烟） |
| `tests/test_deploy_gui.py` | QApplication 提为模块级常驻（qfluentwidgets QConfig 为 QObject 单例，app 回收后其 C++ 侧悬空 → 跨用例 RuntimeError） |

## 3. 验收标准逐项（方案 §6 七项）

| # | 标准 | 结果 |
|---|---|---|
| 1 | 快捷模式端到端：选 tech_etf_mvo → 一键 → 七阶段 PASS → 落 `Demo_account_Real_account/tech_etf_mvo_rotation/` | ✅ 实跑：`output/.qs-deploy-quick/tech_etf_mvo_rotation.yaml` → PASS → `Demo_account_Real_account/tech_etf_mvo_rotation/tech_etf_mvo_rotation_trade.py`（验收后已清理留痕） |
| 2 | 三路 parity 不回退 | ✅ `test_three_way_parity_sha` / `test_cli_kernel_parity_sha` 全绿（既有 73 用例内含） |
| 3 | 中文策略名：目录名保留中文、strategy_id ASCII 且稳定 | ✅ `derive_strategy_id("CANSLIM突破成长选股策略") == "CANSLIM_<6位>"`，两次同值；`safe_dir_name` 保留中文 |
| 4 | 重转归档：二次转换 → `_archive/v001/` 含上一版三件 | ✅ `test_archive_default_unlimited`（3 件）；`keep_versions=0` 关闭（`test_keep_versions_zero_disables_archive`）；`N=1` 只留 1 份（`test_keep_versions_n_prunes_old`） |
| 5 | P5 双测（正例 2 + 负例 4） | ✅ 实现为 9 用例：负例 6（参数/for/with+except/推导式+walrus/解包/g）+ 正例 3（直呼未知/未知模块属性/本地函数豁免但另一未知仍抓） |
| 6 | 零回归：既有 49 + 新用例全绿 + 矩阵哈希一致 | ✅ **deploy 套件 73/73**；`check_fund_matrix.py --check` 通过 |
| 7 | 快捷 yaml ↔ 手写 yaml 产物 SHA256 一致（条件③） | ✅ `test_quick_equals_manual_yaml_sha` |

## 4. 关键设计实证

- **P5 误报根因**：`call_name` 对 `X.method()` 返回**根名 X**（为兼容 `log.info` 归入契约表 `log`）；
  `_local_names` 旧实现漏 7 类作用域绑定 → `history.keys()`（参数）、`ch.isdigit()`（for 目标）被报
  「契约表外」。修复后正例仍捕获（`unknown_api_xyz()`、`unknownmod.do_stuff()`），本地函数不误报。
- **P5 双向边界**：`test_local_function_exempt_but_other_unknown_still_caught` 锁定「豁免不过宽」。
- **D5′ 扫描深度**：实测 45 个产物中 22 个位于两层 → 递归扫描（`rglob`）；`_verify*/_probe*` 默认过滤。
- **D2 稳定性**：`strategy_id` 只由名称派生（参与默认 `state_file` 命名），含非 ASCII 时追加名称
  sha1 前 6 位 —— 名称不变则 id 不变，不同中文名不碰撞。

## 5. 知识反哺回执（grep-able）

```
知识反哺：[命中] 系统性思维总纲（11-方法论与工程体系，2026-10-20）
知识反哺：[命中] 实战范例-双端对齐闭环全案（11-方法论与工程体系，2026-10-20）
知识反哺：[命中] 审计方法论-先证可达性再证敏感性与无先例断言定谳（11-方法论与工程体系，2026-10-20）
知识反哺：[命中] 工程化落地手册（11-方法论与工程体系，2026-10-20）
```

## 6. 六步进度

①方案 ✅（2026-10-19 出件）→ ②审计 ✅（通过 + 三条件 + D3=A）→ ③实施 ✅（commit 1 P5 `5649224`；
commit 2 快捷模式+输出根；快照 `c111c9d`）→ ④验收 ✅（本文件）→ **⑤用户确认（待）** →
⑥随批统推 + README/`docs/strategy_toolbox.md`/`docs/prompt_engineering.md` 同步。
