# 微流水线改动说明 — verify 分片 hash v4（B1 NULL 排序方向修正）

- 状态：**待审计**（v3 方向反了 → v4 修正，未实施）
- v3→v4 变更：NULL 归**最后一片**（与 DuckDB 默认 NULLS LAST 对齐），非第一片
- 随 3B 统一确认推送

---

## v4 修正：B1 NULL 排序方向（v3 勘误）

### v3 错误

v3 把 NULL 放在第一片（`WHERE key IS NULL OR key < first_boundary`），但 DuckDB 默认 NULLS LAST 把 NULL 排在全表最末——两个流不一致，hash 必不相等。

### v4 方案（选定：NULL 归最后一片）

与 DuckDB 默认 NULLS LAST 对齐，全表基准查询零改动：

```python
# 前 N-1 片：不含 NULL（正常 key 范围）
shard_sql = f"""
    SELECT ... FROM "{table}"
    WHERE "{key_col}" >= '{prev_boundary}' AND "{key_col}" < '{next_boundary}'
    ORDER BY {sort_key}
"""

# 最后一片：包含 NULL + 最大 key 范围
last_shard_sql = f"""
    SELECT ... FROM "{table}"
    WHERE "{key_col}" IS NULL OR "{key_col}" >= '{last_boundary}'
    ORDER BY {sort_key}
"""
```

等价性论证：
- 全表 `ORDER BY code, time`（NULLS LAST）→ 流 = [非NULL 升序] + [NULL 行]
- 分片流 = [片1 非NULL] + [片2 非NULL] + ... + [最后片(含NULL)] 
- 最后片内部 `ORDER BY code, time` 也把 NULL 排在片内最末（DuckDB 默认）
- 拼接结果 = [全部非NULL 升序] + [NULL 行] = 全表流 ✅

### NULL 计数守恒

```python
null_count = conn.execute(f'select count(*) from "{table}" where "{key_col}" is null').fetchone()[0]
# 最后片完成后：shard_null_count == null_count
# 总行数：sum(all_shard_counts) == total_count
```

### 适用范围

当前 18 表首列实测均无 NULL（主键列 NOT NULL），此分支不触发。但作为规格根基的等价性证明必须正确。

### 测试 T8/T9（按 v4 方案改写）

| # | 场景 | 判据 |
|---|---|---|
| T8 | 构造含 NULL code 的测试表 → 分片 hash 与全表 hash 一致 | hash_old == hash_new（NULL 在最后片） |
| T9 | NULL 行计数守恒 | 最后片 NULL 行数 == 全表 NULL 行数 |

---

## 其余内容（同 v2，未变）

- §1 问题定义 / §2.1-2.3 分片核心+等价性+自适应键 / §2.5 R5 等价性验收 / §2.6 内存上界 / §3-7 实现/测试/回退/验收
