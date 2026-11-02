# 统推⑤件 · #18 出仓呈批（2026-11-04 同窗 · 2026-11-02 起草）

> **件别**：**统推⑤件**（用户 2026-11-02 裁定：与「框架项呈批面件」**两份并列**，不合并、不合稿；
> 框架项呈批面件由 ⑫ 线主笔，本件不复制其内容）。
> **用途**：作为 **11-04 呈批正式件**，请求用户在 11-04 同窗批准 **#18 统推批次**出仓。
> **起草时点**：2026-11-02 23:59 后（池快照见 §二）；**呈批日须按实态重清点**（见 §六，教训 78）。

## 一、呈批请求

批准 **#18 统推批次**出仓，采用 **SHA 清单制**：

- **锚**：呈批日按实态清点后确定（= 池上界，禁用「推到 HEAD」范围式）。
- **池**：本件 §二 快照 4 笔 ＋ 呈批日实态补录笔（§六 清单逐项核）。
- **范围限制**：仅推送池内 SHA；`--pool` 断言 **exit 4 即停报**（池外笔 > 0 不得推）。
- **时点**：11-04 呈批同窗；避让 11-02 全链与 11-07 M-3。

## 二、池快照（起草时点 实测）

> `origin/main = 4976f1d`｜`HEAD = 6881a73`｜笔数 **4**｜文件 **17**｜**共享层文件 7**。

| # | SHA | 线归 | 提交时点 | 共享层文件数 | 全文件数 | 是否触共享层 |
|---|---|---|---|---|---|---|
| 1 | `6881a73` | 他线·qfq/delivery（跨平台守卫） | 11-03 00:04 | 7 | 12 | **Y** |
| 2 | `3a2ecc3` | 他线·PD-0017（同步门取证） | 11-03 00:03 | 0 | 1 | N |
| 3 | `e748bc3` | 本线·R66（裁定簿记·修正笔） | 11-02 23:21 | 0 | 3 | N |
| 4 | `25410f7` | 他线·PD-0017（R4 沉淀） | 11-02 23:20 | 0 | 1 | N |

**线归说明**：本批为**统推池跨线收池**（用户 2026-11-02 裁定机制）——本线 R66 笔与他线 PD-0017/qfq 笔同池出仓；
归属按各笔自身线别标注，**不因同池而改变归属**。

**共享层文件清单（7 个）**：
- `quantstudio/delivery/stats.py`
- `quantstudio/delivery/verify.py`
- `quantstudio/pipeline/daemon.py`
- `quantstudio/pipeline/qfq_aux_router.py`
- `quantstudio/pipeline/quality_audit.py`
- `tests/test_delivery_passthrough_no_watermark.py`
- `tests/test_qfq_aux_router_guard.py`

## 三、共享层判定（铁律 14 第④款）

**判定：豁免不适用 —— 必须过同步门。**

- 实测触面：**7 个共享层文件**（六类路径：`quantstudio/`、`config/`、`skills/`、`scripts/`、`tests/`、`main_gui.py`）。
- **口径更正留痕**：起草前（23:21 时点）池为 2 笔、共享层 0；`6881a73`（qfq/delivery 跨平台守卫）于其后入池，把共享层触面由 0 抬到 7 ⇒ **「共享层 0 可豁免」的前提已失效**，本件按实态改判为「不可豁免」。
- 后续动作：出仓后**同一工作周期内**在 `QuantStudio-trading` 过同步门（check-drift 九项 ＋ ci-smoke 共享层回归段），**合并后须补两点比对**（`git diff --name-only origin/main HEAD -- <共享层路径集>` 期望为空；防三点比对 merge 后退化漏检）。

## 四、R66 面摘要（并入本件，供⑤同窗同览）

| 维度 | 内容 |
|---|---|
| 问题 | 依赖闭包未入库 ⇒ 「本地绿≠仓库绿」：已提交文件 import 未入库模块，远端必挂（簇 A 内核五件套／簇 B delivery 夹具） |
| 方案 | `docs/r66-dependency-closure-gap-repair-design-20261102.md`（`a209481`；②审＝用户域职权审核通过） |
| 实施 | 六笔：`ddb1087`（五件套）／`dbaaccf`（簇 B 四件）／`d20364b`（门禁挂载＋扫描器）／`a1f9ac3`（大小写语义修缮）／`46e6e41`（白名单＋CI 接线）／`a209481`（方案）＋台账两笔 `199a551`/`c6d2191` |
| 门禁 | `.githooks/pre-push` 三闸（铁律预算门 → **依赖闭包门** → C7 声明闸）；CI `dependency-closure-scan.yml`（PR/push/每周 cron） |
| 验收 | 闭包门 `--self-test` **4/4**；扫描器 **3/3**；独立 oracle **11/11**；全树核缺口 **0**（豁免 5）；待推区间核缺口 **0**；delivery 族 14 文件 **133 passed**；`test_qmt_deploy.py` **5 passed** |
| 边界登记 | `ISS-R66-K`（属性级存在性＝超出模块级闭包门语义，不另立）／`ISS-R66-L`（白名单匹配面维持「目标路径 ∪ 模块名」） |
| 出仓实态 | 首批 15 笔已于 2026-11-02 21:22–21:23 出仓并经用户裁 A **追认**（`ISS-R66-M`） |

## 五、出仓断言口径（8 条，执行时逐条留证）

1. **SHA 清单制**：池＝显式 SHA 清单，锚＝池上界。
2. **第 0 步**：`python scripts/check_remote_advance.py` 核远端未推进（推进即停报）。
3. **四查**：`python scripts/check-iron-rules-budget.py` 内容级检查通过。
4. **池外笔断言**：`check_remote_advance.py --pool <SHA,...> --head <anchor>` ⇒ **exit 4 即停报**。
5. **7b refspec 断言**：推送前核锚为 40 位 SHA ＋ 提交数预期值；命令用 `git push origin "${anchor}:main"`——
   **禁用** PowerShell 双引号内 `$anchor:main`（变量插值歧义，ISS-PUSH-01 根因）。
6. **三方核**：推送后 `quantstudio-plus` / `quantstudio` 两远程与本地锚逐位一致。
7. **留痕**：`docs/handoff/push-bundle-rulings.log` 每 URL 一行，**须含 `RULING=` 声明文本**（缺声明即 `ruling=(none)`，事后须追认）。
8. **依赖闭包门**：pre-push 三闸须全过；`--no-verify` 一律禁用，若确需绕过须在回报中显式声明并登记原因。

## 六、呈批日实态清点清单（教训 78：按实态清点补，禁沿用旧数）

呈批当日依次执行并把实测值回填本件：

1. `git fetch origin && git rev-parse --short origin/main` —— 核远端未推进（相对本件 §二 的 `4976f1d`）。
2. `git log --oneline origin/main..HEAD` —— 逐笔清点，**新增笔按线与共享层触面补录**（本件 §二 表追加行）。
3. `git diff --name-only origin/main..HEAD` ＋ 六类路径过滤 —— **重算共享层文件数**，据此重判 §三 豁免/过门。
4. `python scripts/check_dependency_closure.py --base origin/main --head HEAD` —— 缺口须为 **0**（否则停报）。
5. 确认 11-05 09:00 副本门窗守护（`schedule-39b17726`）在册，且本批共享层文件已并入该窗批次。
6. 回填后重签本件 §二/§三，方可在同窗请求⑤。

## 七、附件

- **附件 A**：`docs/handoff/1104-push-pool-15-attribution-20261102.md` —— 15 笔「**出仓实态核验表**」
  （逐笔线归／共享层文件数／是否触共享层／出仓状态；已于 21:22–21:23 出仓并经裁 A 追认）。
- **附件 B**：`docs/handoff/push-bundle-rulings.log` 21:22/21:23 两行（`ruling=(none)` 原始留痕，未改写）。
- **附件 C**：`docs/ISSUES.md` 的 `ISS-R66-M`（出仓实态与追认）／`ISS-R66-N`（归属追查令·本会话自报）。

