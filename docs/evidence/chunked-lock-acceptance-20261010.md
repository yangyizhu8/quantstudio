# ③ 缺口修复 · 验收证据（2026-10-10）

- **件**：`write_passthrough_chunked` 缺表锁（独立缺陷）｜**方案**：`docs/write-integrity-chunked-lock-design-20261010.md`（②审通过·方案 A·判型纯恢复型）
- **实施**：`quantstudio/pipeline/writers.py`（numstat **65/60**：内联嵌套 + 5 行注释/锁行 + 59 行块缩进）
- **写前快照**：`c94ec159`（stash create + store）
- **性质**：实施+验收完成，**未提交**（六步：待用户确认后方可提交/推送）

## 1. 改动终态（锁序＝全仓统一层级）

```python
        # R2 缺口修复（2026-10-10 ②审通过·方案 A）：整操作级表锁包覆。
        # 本路径做整表重建（staging → DROP → RENAME）且 staging/ledger 协议本质单写者
        # ⇒ 锁粒度只能整操作级；锁序遵循全仓统一层级「表锁 → 连接锁」
        # （同款：_write_locked:890 / _write_passthrough:1057）。
        with self._table_write_lock(table):      # ← L1140（表锁在外）
            with self._conn_lock:                # ← L1141（连接锁在内）
                conn = self._conn()
```

## 2. 验收判据（方案 §7）

| # | 判据 | 结果 | 证据 |
|---|---|---|---|
| 1 | **并发实证**（同进程双线程·同表） | ✅ **成功 2 / 异常 0** | 临时库实测：两线程并发 `write_passthrough_chunked('stock_minutes', …)` 均正常返回；终表行数 200（后完成者整表重建，符合该路径语义）；**残留 staging 表 0**（协议未被破坏） |
| 2 | 协议完整性（resume 语义不变） | ✅ | 同上（`resumed_from=0 / rebuilt=False` 与修复前同形）；staging/ledger 由本方法自清理，无残留 |
| 3 | **不变面**（非串行表零变化） | ✅ | `stock_daily` 写入 200 行正常；**未新建表锁**（`_TABLE_WRITE_LOCKS` 无该键）⇒ `table_write_lock()` 返回 `nullcontext` ✓ |
| 4 | 契约测试 | ✅ **5 passed** | `tests/test_writer_channel_contract.py` 全绿（含 B1 通道非第三通道 / B2 ledger 守卫 AST 断言） |
| 5 | 时延对照（无竞争） | ◐ 结构性等价 | 非串行表走 nullcontext（零成本）；串行表在无竞争下 RLock 获取即放。**未做前后计时对照**（生产库被 daemon 独占，无法用生产表测；临时库计时不具代表性）——如实标注为未做 |
| 6 | 回填工具回归 | ⏸ **顺延** | `scripts/questdb_backfill.py` dry-run 需开生产库；daemon 17:17 恢复后独占 ⇒ 顺延至下次库窗（或由其在库窗内自测） |

## 3. 实施过程如实记录（两次自我纠错）

1. **薄壳实现被契约测试拦下**：首版采用「薄壳包裹 + `_write_passthrough_chunked_impl`」以规避重缩进，
   结果 `test_b1_chunked_variant_is_same_channel_not_third` / `test_b2_ledger_guards_swap` **FAIL**
   （AST 断言 ledger 守卫须在**该方法体内**）⇒ **不修改测试迁就实现**，改为方案 §4 字面形态（内联嵌套）。
   *这是验收环节真正发挥作用的一次：AST 级契约测试守住了"同一通道"的结构约定。*
2. **缩进脚本 off-by-one**：内联脚本只缩进了 `with self._conn_lock:` **之后**的行，漏了该行本身 ⇒
   `py_compile=1`（语法破损）⇒ 外科修复补 4 空格 ⇒ `py_compile=0`、契约测试 5 passed。
   *教训：机械改动用脚本时，块边界（首行是否含在内）必须显式断言，不能靠 range 直觉。*

## 4. 结论

**实施+验收完成**：主改（整操作级表锁）达方案 §7 判据 1-4；判据 5 结构性等价（未做计时）、判据 6 顺延库窗。
**未提交、未推送**——按六步流水线，待用户确认后执行同步提交与双仓推送。

---

## 5. 提交与生效安排（2026-10-10 总调度裁定登记）

- **提交**：`c080dac`（精确 2 件：本件 + `quantstudio/pipeline/writers.py`；零残留）——④ 验收通过后即刻提交照准；
- **六步⑤（用户确认）**：随**下批推送呈批轮**覆盖；**push 候批**（入下批统出，同策略研发口径）；
- **★ 生效安排（裁定）**：**下次 daemon 自然重启 / 库窗一并载入，不为此单独重启**。
  - 依据：daemon **PID 38096 于 2026-10-10 17:17:47 启动**，早于本改动实施时点（约 20:40）⇒ **当前运行体不含本修复**；
  - 该事实已写入 `c080dac` 的 commit message（回执会过期、commit message 不会）；
  - **下批呈批件须列入说明**：本件生效时点＝下次 daemon 重启；届时随窗核对 `writers.py` 载入（判据：重启时点 > c080dac 提交时点）。
- **未闭合明账（5 项）**：① §10.1 护栏小件（后批）；② §10.2 进程内连接/写路径归因小件（后批）；③ 判据 5 生产表计时（下次库窗）；④ 判据 6 回填 dry-run（下次库窗）；⑤ 10-09 16:40 冲突定性维持「不立案、先取证」。
