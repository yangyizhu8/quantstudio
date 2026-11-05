# QMT 产物订单审计行批 · ④验收件（V1-V7 全证据 · 2026-11-05）

> 批次：`4dcdb97`（feat(qmt): 产物订单审计行补齐…）＋ `0ae54a7`（chore(matrix): V4b 补笔）
> 方案：`docs/qmt-order-audit-line-design-20261104.md` v1.0+v1.1（②审有条件通过，三修订落盘）
> 授权：用户 2026-11-04 21:5x 裁①（R299）＋ 2026-11-05 01:0x 裁定传达（R301：出仓追认＋三件立即执行）
> 验收执行：**DSH 侧 O5 五步**（C4 分工；ZCode 侧 python 权限被拦，测试执行归 DSH）
> 隔离纪律：`QUANTSTUDIO_DATA_ROOT=backup_import_20261101_040935`（只读副本）＋独立锁目录＋offscreen；daemon 全程未运行
> 知识反哺：R1 [命中] QD-2026-0017｜R4 = **QD-2026-0020**（随批入库，V7 过检）

---

## 一、V1-V7 验收读数（全绿）

| 门 | 判据 | 实测 | rc |
|---|---|---|---|
| **V1** | `test_qmt_align_check` 全绿＋新用例三条 | **20 passed**（17 旧全保留＋新 3：L301 格式 A 解析／L332 格式 B 不进五维／L353 **A·B 同流混合负向防子串吞**——②审修订三） | 0 |
| **V2** | stub 冒烟全绿＋订单审计断言 | **6 passed**（S8 按策略下单面分层——②审修订二；fall_reversal 桩下 20 笔订单全出 A 行） | 0 |
| **V3** | 14 件 QMT 测试面全绿 | **171 passed in 7.57s**（对 168 基线 +3＝新用例数） | 0 |
| **V4a** | 六策略重转 ALL PASS＋SHA16 新六值 | **ALL 6 PASS (enc=gbk)**：CANSLIM `608bdd2fbb413cf5`｜fall_reversal `8318bdc1b59a44a8`｜tech_etf_mvo `e690122567065db3`｜vol_regime_mom_rev `321742f71999f8e7`｜weekly_smallcap `2eb2b1c838b6014c`｜周频三层 `c1865366ea6e5a48`（旧六值作废留痕：基线件 v1.2 变更日志） | 0 |
| **V4b** | wrapper_hash 同 commit 追认 | `check_fund_matrix --check --reverify`：**qmt_wrapper_hash `0c842fddbba9`→`4f2e73bd467a`**，`qmt_last_reverified→2026-11-04`，OK 契约矩阵门禁通过 ⚠️ 同 commit 要求实态偏差见 §三-c | 0 |
| **V5** | 六产物静态 A/B 在位 | **两产物域各 A=1 B=1 全在位**（qmt_import 重转件＋qmt_export 重生成件，bytes 100,080–115,660B） | 0 |
| **V6** | 全量回归对基线零新增 | **94 failed / 4439 passed / 5 skipped / 8 xfailed / 9 errors in 2107.21s**——failed+errors 对 `5cd6327` 基线（94/9）**逐位同零新增**；passed +59＝本批 +3＋他线 `220e97e..5eb912c` 新增测试 | 判定 PASS |
| **V7** | kb_hook --strict | `体检 1 件（族：qd），不合规 0 件`（QD-2026-0020） | 0 |

**O5 五步**：①diff 范围（6 路径 ⊆ 清单，他线零裹入）②py_compile 三件 ✓ ③模板串增量审查（格式 A/B 落位/缩进/GBK 可编码）④契约 V4b ✓ ⑤上表 ✓。

## 二、改动实态（两笔）

```
4dcdb97  6 files changed, 611 insertions(+)
  quantstudio/strategy_compiler/source_import_qmt.py      +26（_qs_order_dt＋格式 A＋格式 B，纯 print 零行为变）
  tests/test_qmt_align_check.py                          372（★整文件新建，见 §三-a）
  tests/test_qmt_stub_smoke.py                           +29（S8 断言）
  docs/qmt-order-audit-line-design-20261104.md           119（v1.0+v1.1）
  knowledge/qmt/QD-2026-0020.md                           64（R4 沉淀）
  docs/evidence/qmt-conv-correctness-baseline-…md          +1（v1.2 换锚留痕）
0ae54a7  2 files changed, 3 insertions(+), 3 deletions(-)   ← V4b 补笔（§三-c）
  docs/evidence/fundamentals-contract-matrix.{md,yaml}    qmt_wrapper_hash→4f2e73bd467a 等 3 行纯再生
```

## 三、实态与方案/②审要求的三处偏差（如实补正）

**a) `test_qmt_align_check.py`：方案 §二写「+30 行追加」，实态＝372 行整文件新建**
该件**历史即未跟踪**（`git ls-tree HEAD`/`ls-files`/`stash 1c2e4be5` 三查均无）——此前「168 passed」测试面含一件从未进 git 的测试件（文件系统存在 ≠ git 跟踪面）。本批在允许写入清单内将其**首次入库**：17 个旧用例**全部保留**（L51–L286 逐一核对）＋新增 3 用例（L301/L332/L353）。方案表述「追加」与实态「新建」不符，系方案起草时未核该件跟踪状态——**本验收件补正**。遗留风险已上报：`tests/` 其余未跟踪件未盘点（建议 dev 线全量核对）。

**b) qmt_export 六产物重生成（方案外动作，ZCode 候裁 2-A 裁定执行）**
S8 首跑红（fall_reversal：`orders=20 已触发但 A=0 B=0——产物疑为审计行补齐前旧版`）——qmt_export 域六产物为 10-09 旧版且 stub 测试不自行重生成（L60 注释）。处置 A：`orchestrate_source(target='qmt', run_smoke=False, db_path=隔离副本)` 六产物重生成（**我方首跑双层路径 bug 自纠**：函数自建 `<sid>` 层，`out_dir` 多套一层致核验读旧产物；清双层目录＋修正后 rc=0）⇒ S8 全绿。产物域刷新属预期（对件时点性纪律，qmt-e2e-quota-manual §二5 同款）。

**c) V4b「同 commit 追认」实态＝跨 commit 补笔（`4dcdb97`→`0ae54a7`）**
②审硬补要求矩阵追认与代码**同 commit**；实态＝`--reverify` 再生的 3 行矩阵 diff **遗留工作树**（4dcdb97 六文件清单漏含）⇒ 远程仓库内矩阵仍为旧哈希 `0c842fddbba9`、对 `4dcdb97` 后 wrapper 面 CI 将红。总调度 R301-①裁定「矩阵补笔立即执行、消除远程潜在红」⇒ `0ae54a7` 补笔完成（两文件残留 diff 清零）。**偏差如实登记**：同 commit 要求未达成，补救为紧邻补笔；犯次情形已由总调度另登（见 §四）。

## 四、流程注记（总调度 R301 裁定照录）

- **`4dcdb97` 出仓效力维持（追认）**：本笔经他线出仓（origin/main 已含，`e47291c` 时点）——虽有②审条件落实＋显著申报，**⑤闸被绕过，登记犯次（教训 80 情形）**；后续批次严格 **④→⑤→⑥**。
- **ZCode 委派两轮（E7）**：`sess_ba03945d`（首轮停候裁定点：`date=unknown` 坏行语义——ZCode 亲验判定源指正我任务包表述错误）＋`sess_e9e0fbf4`（实施四文件面，python 权限被拦验收归 DSH）。产物标注外部生成；续批裁定（选项 2 `return ''`）实证已入 QD-2026-0020 讨论节。
- ⑤呈批件并入 11:00 三合一窗（追认＋口径追补＋Step 1 证据件＋软锁追认）。

## 五、Step 2 冒烟轮接口（用户侧，明日执行）

一页纸（方案 §八）不变，产物换新锚 `608bdd2fbb413cf5`（113,570B）；四点验不变（驱动性／审计行格式／TARGET_NATIVE 频次／明细 CSV 可作判据输入）；用户直接贴 QMT 端回测日志＋交易明细。

## 六、变更日志

| 时间 | 内容 |
|---|---|
| 2026-11-05 01:1x | v1.0 ④验收件：V1-V7 全读数＋O5 五步＋两笔改动实态＋三处偏差补正（align_check 整文件新建/产物重生成处置 A/V4b 跨 commit 补笔）＋流程注记（追认/犯次/E7）＋冒烟接口 |
