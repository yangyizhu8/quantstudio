# D-29 提交预置件（2026-10-17）

> **性质**：提交**预置**件——**预置 ≠ 提交**。本件供 ⑤ 通过后**一步执行**；执行前须按 §四 重核基线（时效性）。
> **依据**：用户 2026-10-17 16:1x 裁定「④验收通过 ✓ → ⑤入今晚呈批专轮（与出库首推等同批）；待提交面随批」。

## 一、基线读数（2026-10-17 16:14，fetch 后）

| 项 | 读数 |
| --- | --- |
| HEAD | **`f4d9d80`**（他线：件3 项4/6 契约 API-01 check_limit） |
| `origin/main` vs HEAD | **0 / 4**（ahead=4，**4 笔全为他线**：`f4d9d80`／`47d6927`／`486882d`／`0ea425a`） |
| 我的 `4764a0e` | **已在 origin/main**（R1-C 统推已于 2026-10-16 完成并核验双远程逐位一致） |
| 本批范围 | **仅 D-29**（D-33 六件＋D-28 簿记批已随 `4764a0e` 推送） |
| 待提交面 | **23 项**（7 × `M` ＋ 16 × 新；含本预置件） |

## 二、精确清单（23 项，含时点 sha256 前 16 位）

### 修改件（7）

| # | 路径 | sha256(16) | 改动规模 |
| --- | --- | --- | --- |
| 1 | `quantstudio/strategy_compiler/source_import_qmt.py` | `9832b1be3791074a` | **+13 / −0**（纯增益：仅 `profit_ability` 一行） |
| 2 | `tests/test_qmt_fund_field_map.py`（新） | `efdb98cc11446ec8` | 7 用例 |
| 3 | `docs/evidence/fundamentals-contract-matrix.yaml` | `0fed75eb104df090` | 哈希追认 ＋ `meta.qmt_rows` 两行 |
| 4 | `docs/evidence/fundamentals-contract-matrix.md` | `fc5acc66ce114a69` | 哈希派生（`--sync` 生成） |
| 5 | `knowledge/registry.md` | `7574dee497f36b02` | QCAL-04 → **已锚定（D-29 ④验收通过）**（2026-10-17 16:2x 同步） |
| 6 | `docs/ISSUES.md` | `f09bdcb4a3f8f926` | D-29 ③④ 记录（共享台账） |
| 7 | `docs/PROJECT_STATE.md` | `de30721cce951ea6` | D-29 ③④＋⑤候批＋预置件 快照（共享台账） |

### 新增件（15）

| # | 路径 | sha256(16) | 说明 |
| --- | --- | --- | --- |
| 8 | `docs/qmt-profit-ability-mapping-design.md` | `dbe20c02ec5acc56` | 六步①方案 v1.0（②审通过） |
| 9 | `docs/d29-ruling-submission.md`（M） | `fa4dd51746b850e9` | 呈裁件（§八 S2 定谳／§八·补 v4 更正／§九 方案） |
| 10 | `knowledge/qmt/QD-2026-0007.md` | `32b7be72b0b1ffc2` | QD 条目（回哺闭环） |
| 11 | `docs/probe/qmt_profit_ability_probe-20261016.py` | `7ef4c2244700d181` | 探针 v1（仪器迭代·拒判） |
| 12 | `docs/probe/qmt_profit_ability_probe_v2-20261016.py` | `f02a7e89fa8cbad7` | v2（形态转储·拒判） |
| 13 | `docs/probe/qmt_profit_ability_probe_v3-20261016.py` | `ddb184665e75bb33` | v3（读值版·拒判） |
| 14 | `docs/probe/qmt_profit_ability_probe_v4-20261016.py` | `92f5a645b899f311` | v4（生产口径·NO_ROE_FIELD→结论作废） |
| 15 | `docs/probe/qmt_profit_ability_probe_v5-20261017.py` | `dee885f7ecc9a0ff` | v5（文档依据版·FIELD_CONFIRMED 4 命中） |
| 16 | `docs/probe/qmt_profit_ability_e2e_probe-20261017.py` | `4e5225ba50a8c4c3` | e2e v1（真实 wrapper 路径·判据 3） |
| 17 | `docs/probe/qmt_profit_ability_e2e_probe_v2-20261017.py` | `f15a5d88b70fd31f` | e2e v2（同期对齐·判据 4/5） |
| 18 | `docs/evidence/d29-acceptance.md` | `500340cc6091eb87` | ④验收证据（七判据＋21 期逐位比对） |
| 19 | `docs/evidence/d29-local-roe-baseline.txt` | `d2da7bd8f6c6b733` | 本地基线（fin_indicator 只读查询） |
| 20 | `docs/evidence/d29-crit4-ab-verify.txt` | `c405525451801e9b` | 判据 4 复核留档 |
| 21 | `docs/evidence/d29-map-tests.txt` | `18031883a839b38f` | 单测 7 passed |
| 22 | `docs/evidence/d29-contract-gate.txt` | `3f26823af3f277d2` | CONTRACT GATE PASS |
| 23 | `docs/handoff/d29-commit-preset-20261017.md` | `c33d6a9a3c5fcb82` | **本预置件**（随批入库，供溯源） |

## 三、执行序列（⑤ 通过后照此执行）

```powershell
# 1) 写前快照（四步配方，scoped）
$paths = @(<上述 22 项>)
git add -A -- $paths
$h = git stash create -m "baseline-<时间戳>-d29-commit"
git stash store -m "baseline-<时间戳>-d29-commit" $h
git reset
# 记录返回 hash 入会话记录/台账

# 2) 精确 add（禁 git add -A / .）
git add -- $paths

# 3) 教训 44：暂存面核对（必须恰为 22 项、且全为我方）
git diff --cached --name-status   # 须恰 23 项、全为我方
git diff --cached --numstat -- quantstudio/strategy_compiler/source_import_qmt.py   # 须 13/0

# 4) 提交（信息见 §五）
git commit -F <msgfile>
```

## 四、失效条件（执行前须重核，任一命中即暂停上报）

> **复核记录（2026-10-17 16:30）**：①他线 `f4d9d80..HEAD` 新提交仅改 1 文件，**与我方 23 件零交叉** ✅；②HEAD 已前进至 `1b09264` 但未触及我方文件 ⇒ 清单有效；③sha 漂移 3 处已修正（`registry.md` 主动同步 QCAL-04、`PROJECT_STATE.md` 追加记录、本预置件自身新增）⇒ **本件 sha 为 16:30 时点指纹**；④仓库无 `index.lock`/`MERGE_HEAD`/`rebase-merge` 残留 ✅。


1. **他线改动同文件**：`source_import_qmt.py` / `knowledge/registry.md` / `docs/ISSUES.md` / `docs/PROJECT_STATE.md`
   出现非我方 hunk（扫描法：`git diff -- <file>` 逐 hunk 目检 ＋ 他线关键词扫描须为 0）；
2. **HEAD 前进影响基线**：若 HEAD 已非 `f4d9d80` 且新提交触及上列文件 ⇒ 重核 diff 与 sha256；
3. **sha256 漂移**：任一文件 sha256(16) 与 §二 不符 ⇒ 以实际面重出清单（**禁止照抄本件 hash 提交**）。

## 五、提交信息要点

```
feat(QMT管线 D-29): profit_ability 原生比率字段映射（roe→du_return_on_equity / net_profit_margin→net_profit）
+ 矩阵哈希同批追认 + 7 用例 + QD-0007 + 验收证据

- 依据：Context7 /websites/dict_thinktrader_net（nativeApi/xtdata.html 全字段表）+ v5 探针端测 4 命中
- 改动：_QS_QMT_FUND_FIELD_MAP 增 profit_ability 一行（+13/-0 纯增益）；契约矩阵 meta.qmt_rows 两行
- 哈希：qmt_wrapper_hash 20f58868117f → 6927447ea23c（yaml+md 同批）
- 验收：判据 1/2/3/4/6/7 PASS（判据 4 口径逐位一致 21/21 期）；判据 5 不适用（ROE 仅排序无阈值）
- 披露：①覆盖差异（本地 2018Q1+ vs QMT 1990s+）非口径差；600000 本地缺 2024-03-30 期（观察项）
        ②qmt-pa-02（net_profit_margin）本地无列 ⇒ A/B 不可比（映射有效）
        ③叠加事实：本批 orchestrator.py 不在面内（D-33 已随 4764a0e 推送）
- 证据：docs/evidence/d29-acceptance.md 等 5 件；快照 <hash>
```

## 六、同批要件与后续

| 项 | 说明 |
| --- | --- |
| 矩阵哈希 | **已在面内**（`--check` 终验 OK；负向门 5/5） |
| 建议复跑 | 提交前复跑 `run_contract_gate.py --strategies`（他线 4 笔已前进，防交叉影响） |
| trading 同步门 | 本批触共享层（`quantstudio/`）⇒ **必跑不可豁免**；**当前受阻**（trading 工作树脏＝他线在途，候四线清理） |
| 推送 | 候用户批令；推送须走 prepush 门（逗号放行清单，教训 24；**禁用 `--no-verify`**） |
