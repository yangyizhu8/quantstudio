# DB 族 KB 条目候选（三条）· 待⑥会话登记 `db` 族后经 `kb_draft.py` 入库

- **日期**：2026-10-29｜**归属**：duckdb 数据库升级执行会话（图节点⑲）｜spec 002-duckdb-1.5-upgrade · T052
- **族归属裁定（总调度 2026-10-29）**：批准登记 **`db` 族**（数据库引擎与数据完整性域）；**登记动作由⑥会话在 `knowledge/kb-info.yaml` families 段执行**（schema 枚举与本件对齐）⇒ **本会话不自开新族**。
- **本件作用**：把三条条目的起草参数备齐（可直接喂 `scripts/kb_draft.py`），避免 M-4 重新回忆读数。
- **入库前置**：`db` 族登记完成（`kb-info.yaml` families 段有 `id: db` + `dir` + `entry_glob` + `id_pattern` + `fix_layer_enum` + `required_fields` + `domain_vocab`）⇒ 然后按 §四命令逐条起草，机检（`kb_hook.validate_record`）不过则不落盘。

---

## 一、条目①：duckdb 1.5.x `executemany` 批量写劣化（本项目唯一引擎落点）

| 参数 | 内容 |
|---|---|
| `--symptom` | duckdb 1.5.6 上 `executemany` 批量写耗时较 1.4.5 劣化 4–6 倍（300 行 0.64s→3.94s；1000 行 3.31s→13.40s；5000 行 15.44s→>90s 超时），而 `INSERT INTO … SELECT`（DataFrame 零拷贝注册路径）不受影响 |
| `--root-cause` | duckdb 1.5.x 的 `executemany` 逐行绑定路径劣化（ART index-delete 缺陷同族，`duckdb#23645` 由静默错删变 FATAL，1.5.6 未修复）；本项目写者中唯一走 `executemany` 的落点是 `index_constituents_meta.py:125` → `index_constituents_snapshot_meta`（PK `(index_code,time)`） |
| `--evidence` | `docs/evidence/a14-executemany-scale-20261029.md`（本机 1.4.5/1.5.6 双版本中位数读数，3 个批次量级） |
| `--fix-target` | `quantstudio/pipeline/index_constituents_meta.py:125`（唯一落点；改写入形态**单独立项**，不在 002 首跳范围）＋ `docs/evidence/a14-executemany-scale-20261029.md`（结论登记） |
| `--domain` | 待⑥会话登记 `db` 族时确定（建议：数据库引擎 / 性能域） |
| `--fixture` | `docs/evidence/a14-executemany-scale-20261029.md`（可复跑读数脚本与三档批次参数） |
| 备注 | 判型：**新增检测型**（巡检/结论开始报告真相）；**处置＝risk_only**：不影响正确性，影响时延；客户通知已量化（`docs/handoff/customer-notice-5-duckdb-1.5.6-upgrade-20261029.md` §六） |

## 二、条目②：`DROP+RENAME` 静默清空主键 vs `TRUNCATE` 保留（两版本读数）

| 参数 | 内容 |
|---|---|
| `--symptom` | passthrough 全量覆盖用「CTAS 临时表→灌数→DROP 原表→RENAME」时**静默清空目标表主键**（1→0，不报错不告警）；改用 `TRUNCATE`+`INSERT`（同一事务）后主键保持 1→1、`ON CONFLICT` 冲突探测继续有效 |
| `--root-cause` | `CREATE TABLE … AS SELECT`（CTAS）**不继承任何约束**（主键/唯一/非空全丢），而 `DROP`+`RENAME` 只换表名不换约束 ⇒ 每次覆盖都把主键清成 0；`TRUNCATE`+`INSERT` 保留原表对象与索引，故主键不变 |
| `--evidence` | `docs/evidence/pt01-f2-truncate-20261029.md`（四组对照：TRUNCATE 1→1 / DELETE 1→1 / DROP+RENAME 1→0 且后续 `ON CONFLICT` 抛 `BinderException`）+ `docs/evidence/passthrough-drop-repro-20261028.md`（1.4.5/1.5.5/1.5.6 三版本复现）+ `docs/evidence/pt01a-passthrough-pk-fix-20261029.md`（改道后读数） |
| `--fix-target` | `quantstudio/pipeline/writers.py:1575-1628`（`_write_passthrough` → F2 `TRUNCATE`+`INSERT`）与 `writers.py:1672-1762`（`write_passthrough_chunked` → 同步 F2，保留 staging+ledger）+ `quantstudio/pipeline/config_lint.py`（`_lint_one_task` 新增 ERROR 级前瞻守卫：passthrough 指向带主键表 ⇒ 启动即报错） |
| `--domain` | 待⑥会话登记 `db` 族时确定（建议：数据完整性 / 写入语义域） |
| `--fixture` | `tests/test_passthrough_pk_preservation.py`（P1–P6：保主键 / 旧语义对照 / 双写 sha256 逐位一致 / F1 重建 / 指纹表拒绝 / lint 负向与现状零告警） |
| 备注 | 判型：**纯恢复型**（改道前后写入内容 sha256 相同 ⇒ 无行为漂移）；配套：`scripts/check_passthrough_pk.py`（只读巡检）+ `docs/handoff/duckdb-pt01-passthrough-runbook-20261029.md` |

## 三、条目③：实施期制品判据随改道失准 ⇒ 必须回扫（本轮新得）

| 参数 | 内容 |
|---|---|
| `--symptom` | 改代码后，制品里写死的**判据**（grep 模式、测试计数、退出码语义）会悄悄失准：判据本身不再成立但不报错，读者会据此误判「已通过」 |
| `--root-cause` | 制品的判据是**对当时实现形状的断言**；实现改道（集合化、单文件化、测试扩容、占用语义）后，断言的**所指对象**已变，而制品无自检机制 ⇒ 失准是默认结果而非例外 |
| `--evidence` | `docs/handoff/duckdb-1.5-upgrade-governance-approval-20261029.md`（Phase 7 呈批件，含 4 处判据回扫的逐条对照）＋ `specs/002-duckdb-1.5-upgrade/quickstart.md` §0 检查项 2/4/7（已校正后的判据写法） |
| `--fix-target` | 实施纪律：**每次改道后回扫制品判据**（grep 模式重测、计数更新、退出码语义复核），回扫结果落证据并逐条列「失准点 → 校正后」；本会话本轮实例见 `docs/evidence/pt01a-passthrough-pk-fix-20261029.md` 与本轮上报 |
| `--domain` | 待⑥会话登记 `db` 族时确定（建议：工程纪律 / 制品一致性域；若无匹配，取族内最接近者并在 notes 注明） |
| `--fixture` | `docs/handoff/duckdb-1.5-upgrade-governance-approval-20261029.md` §四 + `specs/002-duckdb-1.5-upgrade/quickstart.md` §0（可机检的判据写法范例） |
| 备注 | 判型：**新增检测型**；来源：2026-10-29 Phase 5/7 实施轮的自查纠偏（4 处失准：版本正则 grep 命中集合文案、契约点数 6→27、PK 巡检退出码 3=SKIP 非失败、灰度窗口判据与实现漂移） |

---

## 四、M-4 入库执行序列（族登记完成后）

```bash
# ① 逐条起草（--kb knowledge/<db族目录>；--domain 取⑥登记时确定的域码）
python scripts/kb_draft.py --kb knowledge/<db族目录> \
  --symptom "<条目① symptom>" --root-cause "<root-cause>" \
  --evidence "docs/evidence/a14-executemany-scale-20261029.md" \
  --fix-target "quantstudio/pipeline/index_constituents_meta.py:125" \
  --domain <D?> --fixture "docs/evidence/a14-executemany-scale-20261029.md"

# ② ②③ 同理（参数见上）
# ③ 机检：kb_hook.validate_record 按族校验（kb_draft 内置，不过不落盘）
# ④ 落盘后核对：knowledge/<db族目录>/ 下新增 DB-*.md 三件，id 连续、entry_glob 命中
```

> 纪律提醒（铁律 22）：条目必须过 `kb_hook.validate_record` **按族**校验才落盘；`kb-info.yaml` 的 `entry_count_at` 是登记时读数，**不作强断言**，但落盘后应更新 `updated` 字段与计数。
