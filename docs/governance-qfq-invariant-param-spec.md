# 微流水线改动说明 — qfq_invariant 主库路径参数化

- 状态：**待审计**（改动说明已产出，未动代码）
- 归属：治理方案实施第 3 步前置（记录项 B）；随方案第 8 步统一用户确认 + 双仓库推送，不单独推送
- 审计要点对应：DSH 审核意见 §四（默认行为等价 / 范围最小化 / 测试 / 验收 / 回退）

---

## 1. 改动语义（行为等价）

`quantstudio/pipeline/qfq_invariant.py` `check_golden_rows()` 增加 `main_db_path` 参数：

```python
def check_golden_rows(golden_rows=None, main_conn=None, aux_conn=None,
                      aux_path=None, adj_latest_map=None, golden_path=None,
                      main_db_path=None):          # ← 新增，末位，默认 None
    ...
    if main_conn is None:
        import duckdb
        from quantstudio._paths import db_path    # 与 DATA_ROOT 同源（_paths.py:53-55）
        main_conn = duckdb.connect(
            str(Path(main_db_path) if main_db_path is not None else db_path()),
            read_only=True)
        own_main = True
```

**默认等价证明**：现状 `DATA_ROOT / "quantstudio.db"` 与 `db_path()` 完全同一表达式——`_paths.py:53-55` 定义 `db_path(name="quantstudio.db")` 返回 `DATA_ROOT / name`；`DATA_ROOT` 本身就 import 自 `quantstudio._paths`（qfq_invariant.py:44）。不传 `main_db_path` 时连接的库路径与改动前**逐字符一致**，read_only 语义不变，`own_main` 自关逻辑不变。

## 2. 范围最小化：全文件硬编码路径点完整清单

grep `duckdb.connect|DATA_ROOT|quantstudio.db` 全文件命中点逐点说明：

| 行 | 现状 | 处置 |
|---|---|---|
| :44 | `from quantstudio._paths import DATA_ROOT` | **不动**（L67 仍用） |
| :67 | `default_aux_path()` 返回 `DATA_ROOT / "qfq_aux.db"` | **不动**（aux 已有参数化，且属审计明确"已有参数不动"范围） |
| :480 | `check_golden_rows` 自开连接 `duckdb.connect(str(DATA_ROOT / "quantstudio.db"))` | **本改动唯一目标** |
| 其余全部函数（`audit_factor_integrity`:283、`refresh_golden_rows_for_code`:562、`verify_reanchor_selfcheck`:627 等） | `main_conn` 均为必传参数，无自开连接 | **不动** |

即：全文件主库自开连接仅 L480 一处，无其他硬编码点。改动 = 1 个函数签名 + 1 行连接表达式。

## 3. 测试

1. **既有测试零变化**：默认调用（不传 `main_db_path`）行为不变，`tests/` 中 qfq_invariant 相关用例保持通过；
2. **新增参数化用例**：传 `main_db_path=<快照副本路径>` 时，通过 monkeypatch/连接探测断言实际读取的是指定副本（可用仅存在于副本中的哨兵数据行验证），且默认路径分支不受影响。

## 4. 验收

1. **默认等价**（真实主库实跑）：改动前后各跑一次 `check_golden_rows()`（默认调用，同一 golden_rows 清单），返回的 `checked/mismatched/skipped/details` 逐字段一致；
2. **副本指向**：`main_db_path` 指向快照副本时读到指定库（测试用例通过）；
3. 全量相关测试通过（qfq_invariant + pipeline 相关套件）。

## 5. 回退

单点改动（1 函数 + 1 行），revert 即完全回退，无状态残留。

## 6. 用途（为什么现在做）

第 3 步起所有巡检/自检必须跑在**快照物理副本**上（治理方案 §2.3：禁止打开被写库）；黄金行自检是当前唯一硬编码主库路径的巡检入口，参数化后快照机制落地时零阻碍。
