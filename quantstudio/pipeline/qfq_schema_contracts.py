"""QFQ schema 版本化物理指纹 —— 唯一真相源（v2.4 B-3a）。

本模块是 QFQ schema 状态识别与最终 2.1 DDL 的**唯一权威契约源**。它定义：

1. **两个独立冻结的版本化物理指纹**（不派生自当前 DDL / SCHEMA_CONTRACT_DUCKDB /
   SCHEMA_VERSION 字符串）：
   - ``LEGACY_QFQ_2_0_FINGERPRINT``：B-3R 实测的真实 2.0 物理结构（11 表）。
   - ``TARGET_QFQ_2_1_FINGERPRINT``：mcp-cutover-design-v2.md 审核通过的最终 2.1
     物理结构（15 表 = 11 升级 + 4 新表）。
2. **source_watermark 共享 DDL + 指纹**（writers.py 与 QFQ schema 模块共同引用，
   单一真相源，禁止两份手写结构）：
   - ``SOURCE_WATERMARK_2_1_DDL`` / ``SOURCE_WATERMARK_2_0_DDL``。
   - ``TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT`` / ``LEGACY_SOURCE_WATERMARK_FINGERPRINT``。
3. **聚合主库指纹**（QFQ 管理表 + source_watermark）：
   - ``LEGACY_MAIN_DB_2_0_FINGERPRINT`` / ``TARGET_MAIN_DB_2_1_FINGERPRINT``。
4. **物理指纹 parser + 校验器**（``parse_physical_contract`` / ``verify_fingerprint``），
   校验维度：精确表集合（管理范围限定）、列集合（拒绝多余）、列顺序、类型、**物理
   NOT NULL**（显式 + inline PK + 复合 PK 列均计 true）、**DEFAULT 规范化**、PK 列与
   顺序、UNIQUE、**外键**。
5. **pre-cutover 静态写入映射**（``pre_cutover_generation``）：B-3a 扩列后既有生产
   INSERT 的静态兼容桥。QFQ 价格表→legacy 哨兵；非 QFQ 表→not-qfq-managed 哨兵；
   ``source`` 保留真实值不改写。不查 active cutover（B-5/B-6 范围）。

设计依据：mcp-cutover-design-v2.md §3.2.1/3.2.2/3.2.3/3.2.5/3.4/4.3、B-3R 勘察报告。

本模块**无数据库副作用**：纯数据定义 + 纯函数，不在 import 时连任何库、不发任何
DDL/DML。所有校验由调用方传入连接后只读执行。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 标识常量
# ---------------------------------------------------------------------------

# B-3 新增表（2.0 不存在，2.1 存在）
B3_NEW_TABLES: Tuple[str, ...] = (
    "qfq_discovery_baseline",
    "qfq_source_cutover",
    "qfq_active_cutover",
    "qfq_cycle_lease",
)

# B-3b migration runner 将产生的 shadow / rename 残留表（探测到任一即 PARTIAL_OR_MIXED）
# 4 张需 v2 swap 的表 × {_v2, _legacy}
_V2_SWAP_TABLES = (
    "qfq_pending_backfill", "qfq_observation_cursor",
    "qfq_anchor_state", "qfq_watermark_intent",
)
KNOWN_SHADOW_TABLES: Tuple[str, ...] = tuple(
    name for t in _V2_SWAP_TABLES for name in (f"{t}_v2", f"{t}_legacy"))

# migration ledger / state 表占位（B-3b 范围，本轮空集，留扩展点）
KNOWN_MIGRATION_TABLES: Tuple[str, ...] = ()

# 4 张 QFQ 价格表（pre-cutover 世代映射区分 QFQ vs 非 QFQ）
QFQ_PRICE_TABLES: Tuple[str, ...] = (
    "stock_daily", "stock_minutes", "etf_daily", "etf_minutes",
)

# B-3a 后 QFQ 重锚子系统管理的全部 DuckDB 表（15 张，target 2.1）
# trade_calendar 是与 writers.py 框架 schema 共享的表（两边 DDL 逐字一致），不纳入
# 版本化判定基准的"空库"信号（见 qfq_schema_status 的空库判定），但仍属 target 契约。
TARGET_QFQ_MANAGED_TABLES: Tuple[str, ...] = (
    "qfq_anchor_state", "qfq_reanchor_event", "qfq_pending_backfill",
    "qfq_bootstrap_run", "qfq_bootstrap_item", "trade_calendar",
    "qfq_cycle_run", "qfq_trigger_queue", "qfq_watermark_intent",
    "qfq_fresh_capture", "qfq_observation_cursor",
    # B-3 新表
    "qfq_discovery_baseline", "qfq_source_cutover",
    "qfq_active_cutover", "qfq_cycle_lease",
)

# legacy 2.0 管理表（11 张，无 B-3 新表）
LEGACY_QFQ_MANAGED_TABLES: Tuple[str, ...] = (
    "qfq_anchor_state", "qfq_reanchor_event", "qfq_pending_backfill",
    "qfq_bootstrap_run", "qfq_bootstrap_item", "trade_calendar",
    "qfq_cycle_run", "qfq_trigger_queue", "qfq_watermark_intent",
    "qfq_fresh_capture", "qfq_observation_cursor",
)

# 共享表（writers.py 与 QFQ schema 都管；source_watermark 单独由 writers 建）
SHARED_MANAGED_TABLES: Tuple[str, ...] = ("source_watermark",)

# 所有"已知版本化对象"：用于空库判定（任一存在 → 非空库）
KNOWN_VERSIONED_OBJECTS: Tuple[str, ...] = (
    *TARGET_QFQ_MANAGED_TABLES,      # 含 legacy 11 + B-3 新 4
    *KNOWN_SHADOW_TABLES,
    *KNOWN_MIGRATION_TABLES,
    *SHARED_MANAGED_TABLES,
)

# ---------------------------------------------------------------------------
# 物理指纹数据结构
# ---------------------------------------------------------------------------
# 每张表指纹：
#   {
#     "columns": [(name, TYPE_UPPER, not_null_bool, default_canonical_or_None), ...]  # 有序
#     "primary_key": [col, ...]            # 有序列；空列表=无 PK
#     "unique": [[col, ...], ...]          # UNIQUE 约束；本批表均无，留 []
#     "check": [...]                       # CHECK 约束；本批表均无，留 []
#     "foreign_keys": [{"columns":[...], "referenced_table":..., "referenced_columns":[...]}, ...]
#   }
# not_null 取**物理语义**：DuckDB 中显式 NOT NULL、inline PRIMARY KEY、表级复合 PRIMARY KEY
# 的列，introspection（PRAGMA table_info.notnull）均报 True。故指纹对这三类列均标 True。
# default 取**规范化**值（见 canonicalize_default），与 DuckDB introspection 的 dflt_value
# 比对前双方都过 canonicalize_default。

Fingerprint = Dict[str, object]
FingerprintDB = Dict[str, Fingerprint]


# ---------------------------------------------------------------------------
# DEFAULT 规范化
# ---------------------------------------------------------------------------

def canonicalize_default(raw: Optional[str]) -> Optional[str]:
    """把 DuckDB introspection 的 dflt_value 规范化为稳定可比形式。

    DuckDB PRAGMA table_info 的 dflt_value 形态（实测 1.5.5）：
    - 整数：``'0'`` / ``'5'``（字符串带数字）
    - 字符串：``"'x'"``（外层单引号包裹）
    - BOOLEAN：``'0'`` / ``'1'``（与整数同形）
    - 无 DEFAULT：``None``

    规范化规则：
    - None → None
    - 去首尾空白
    - 整数/布尔字面量 ``0``/``1`` → ``'0'``/``'1'``（保留为字符串）
    - 单引号字符串 ``'...'`` → 去外层引号后的内容
    - 其它（表达式如 CURRENT_TIMESTAMP）→ 去空白后原样保留（本批表不用此类默认）
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    # 单引号字符串字面量：去外层引号
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1]
    # 整数 / 布尔字面量
    if re.fullmatch(r"-?\d+", s):
        return s
    # 其它表达式：去多余空白保留
    return re.sub(r"\s+", " ", s)


# ---------------------------------------------------------------------------
# 指纹 parser（从 DDL 文本解析物理指纹）
# ---------------------------------------------------------------------------

def _split_top_level(s: str) -> List[str]:
    """按顶层逗号切分（括号内部逗号不切）。"""
    parts: List[str] = []
    depth = 0
    cur: List[str] = []
    for ch in s:
        if ch == '(':
            depth += 1
            cur.append(ch)
        elif ch == ')':
            depth -= 1
            cur.append(ch)
        elif ch == ',' and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


# 列定义正则：name TYPE[(...)] ，TYPE 取第一个 token（含可选 (n) 精度）
_COL_RE = re.compile(
    r'^\s*("?[A-Za-z_][\w]*"?)\s+([A-Za-z]+(?:\s*\([^)]*\))?)(.*)$',
    re.DOTALL)


def parse_physical_contract(ddl: str) -> Fingerprint:
    """从单表 CREATE TABLE DDL 文本解析完整物理指纹。

    维度：columns(name,type,not_null,default) 有序；primary_key 有序；unique；check；
    foreign_keys。not_null 取物理语义（显式 NOT NULL + inline/复合 PK 列）。

    Args:
        ddl: 形如 ``CREATE TABLE IF NOT EXISTS t ( ... )`` 的 DDL 文本。

    Returns:
        指纹字典。结构见模块 docstring。
    """
    m = re.search(r"\((.+)\)", ddl, re.DOTALL)
    if not m:
        return {"columns": [], "primary_key": [], "unique": [], "check": [], "foreign_keys": []}
    inner = m.group(1)

    columns: List[Tuple[str, str, bool, Optional[str]]] = []
    pk_cols: List[str] = []
    unique_cols: List[List[str]] = []
    check_exprs: List[str] = []
    foreign_keys: List[dict] = []

    for p in _split_top_level(inner):
        p = p.strip()
        if not p:
            continue
        up = " " + p.upper().replace("\n", " ").strip() + " "

        # 表级 PRIMARY KEY (a, b, ...)
        if up.strip().startswith("PRIMARY KEY"):
            pkm = re.search(r"\(([^)]*)\)", p)
            if pkm:
                pk_cols = [c.strip().strip('"') for c in pkm.group(1).split(",") if c.strip()]
            continue

        # 表级 UNIQUE (a, b, ...)
        if up.strip().startswith("UNIQUE") and not up.strip().startswith("UNIQUE(") is False:
            pass  # 占位，下方统一处理
        if re.match(r"^\s*UNIQUE\s*\(", up.strip()):
            um = re.search(r"\(([^)]*)\)", p)
            if um:
                unique_cols.append([c.strip().strip('"') for c in um.group(1).split(",") if c.strip()])
            continue

        # 表级 CHECK (...)
        if up.strip().startswith("CHECK"):
            cm = re.search(r"\((.*)\)", p, re.DOTALL)
            if cm:
                check_exprs.append(re.sub(r"\s+", " ", cm.group(1).strip()))
            continue

        # 表级 FOREIGN KEY (cols) REFERENCES tab(cols)
        if up.strip().startswith("FOREIGN KEY"):
            fkm = re.search(r"FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)", p, re.IGNORECASE)
            if fkm:
                foreign_keys.append({
                    "columns": [c.strip().strip('"') for c in fkm.group(1).split(",") if c.strip()],
                    "referenced_table": fkm.group(2).strip(),
                    "referenced_columns": [c.strip().strip('"') for c in fkm.group(3).split(",") if c.strip()],
                })
            continue

        # 列定义
        cm = _COL_RE.match(p)
        if not cm:
            continue
        name = cm.group(1).strip().strip('"')
        typ = cm.group(2).replace(" ", "").upper()
        rest = cm.group(3) or ""
        rest_up = " " + rest.upper().replace("\n", " ").strip() + " "

        not_null = bool(re.search(r"\bNOT\s+NULL\b", rest_up))
        inline_pk = bool(re.search(r"\bPRIMARY\s+KEY\b", rest_up))
        if inline_pk and name not in pk_cols:
            pk_cols.append(name)
        # inline PK 列物理 NOT NULL
        if inline_pk:
            not_null = True

        # DEFAULT <value>
        default_canonical: Optional[str] = None
        dm = re.search(r"\bDEFAULT\s+('(?:[^']|'')*'|-?\d+(?:\.\d+)?|TRUE|FALSE|NULL|[A-Za-z_][\w()]*)", rest_up)
        if dm:
            default_canonical = canonicalize_default(dm.group(1))

        columns.append((name, typ, not_null, default_canonical))

    # 物理语义：表级复合 PRIMARY KEY 的列在 DuckDB introspection 中报 notnull=True。
    # 故把这些列也标 not_null=True（与 verify_fingerprint 的 _table_columns 物理读取一致）。
    pk_set = {c.lower() for c in pk_cols}
    columns = [
        (n, t, (nn or (n.lower() in pk_set)), d) for (n, t, nn, d) in columns
    ]

    return {
        "columns": columns,
        "primary_key": pk_cols,
        "unique": unique_cols,
        "check": check_exprs,
        "foreign_keys": foreign_keys,
    }


# ---------------------------------------------------------------------------
# 指纹校验器（只读，不抛）
# ---------------------------------------------------------------------------

def _table_exists(conn, table: str) -> bool:
    """只读判断表是否存在。"""
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='main' AND lower(table_name)=?",
        [table.lower()],
    ).fetchone()
    return bool(row and int(row[0]) > 0)


def _table_pk(conn, table: str) -> List[str]:
    """只读返回某表 PK 有序列名（duckdb_constraints()）；无 PK 返回 []。

    **不吞异常**：表不存在或 introspection 失败时向上传播（由 detect_schema_status
    统一转 UNKNOWN，不静默返回空集）。调用前应先用 _table_exists 确认表存在。
    """
    rows = conn.execute(
        "SELECT constraint_column_names FROM duckdb_constraints() "
        "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'",
        [table],
    ).fetchall()
    if not rows:
        return []
    pk = rows[0][0]
    return [str(c).lower() for c in pk] if pk else []


def _table_columns(conn, table: str) -> Dict[str, dict]:
    """只读返回 {列名小写: {type, not_null, default, pk_flag}}。

    **不吞异常**：introspection 失败时向上传播（detect_schema_status 转 UNKNOWN）。
    表不存在时 PRAGMA table_info 返回空行集 → 返回 {}（由调用方结合 _table_exists 判定）。
    """
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    out: Dict[str, dict] = {}
    for r in rows:
        # (cid, name, type, notnull, dflt_value, pk)
        out[str(r[1]).lower()] = {
            "type": str(r[2]).upper(),
            "not_null": bool(r[3]),
            "default": canonicalize_default(r[4]),
            "pk_flag": bool(r[5]),
        }
    return out


def _table_foreign_keys(conn, table: str) -> List[dict]:
    """只读返回某表的外键约束列表（duckdb_constraints()）。

    **不吞异常**：introspection 失败时向上传播。DuckDB FK 的 ``constraint_text`` 形如
    ``FOREIGN KEY (cutover_id) REFERENCES qfq_source_cutover(cutover_id)``。
    FK 列用 ``constraint_column_names``（权威有序）；referenced 表/列从
    ``constraint_text`` 的 ``REFERENCES tab(cols)`` 部分解析。
    """
    rows = conn.execute(
        "SELECT constraint_column_names, constraint_text "
        "FROM duckdb_constraints() "
        "WHERE table_name = ? AND constraint_type = 'FOREIGN KEY'",
        [table],
    ).fetchall()
    fks: List[dict] = []
    for col_names, text in rows:
        if not text:
            continue
        m = re.search(
            r"REFERENCES\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)",
            text, re.IGNORECASE)
        if not m:
            continue
        cols = [str(c).strip().strip('"').lower() for c in (col_names or []) if str(c).strip()]
        fks.append({
            "columns": cols,
            "referenced_table": m.group(1).strip().lower(),
            "referenced_columns": [c.strip().strip('"').lower()
                                   for c in m.group(2).split(",") if c.strip()],
        })
    return fks


def _table_unique_constraints(conn, table: str) -> List[List[str]]:
    """只读返回某表的 UNIQUE 约束列表（每个约束 = 有序列名列表）。不吞异常。

    从 duckdb_constraints() 取 constraint_type='UNIQUE' 的 constraint_column_names。
    """
    rows = conn.execute(
        "SELECT constraint_column_names FROM duckdb_constraints() "
        "WHERE table_name = ? AND constraint_type = 'UNIQUE'",
        [table],
    ).fetchall()
    out: List[List[str]] = []
    for (cols,) in rows:
        if cols:
            out.append([str(c).lower() for c in cols])
    return out


def _table_check_constraints(conn, table: str) -> List[str]:
    """只读返回某表的 CHECK 约束表达式列表（规范化）。不吞异常。

    从 duckdb_constraints() 取 constraint_type='CHECK' 的 constraint_text，提取
    CHECK(...) 内的表达式并规范化（去多余空白）。
    """
    rows = conn.execute(
        "SELECT constraint_text FROM duckdb_constraints() "
        "WHERE table_name = ? AND constraint_type = 'CHECK'",
        [table],
    ).fetchall()
    out: List[str] = []
    for (text,) in rows:
        if not text:
            continue
        m = re.search(r"CHECK\s*\((.*)\)", text, re.IGNORECASE | re.DOTALL)
        expr = m.group(1) if m else text
        out.append(re.sub(r"\s+", " ", expr.strip()))
    return out


def verify_fingerprint(conn, fingerprint: FingerprintDB, *,
                       reject_extra: bool = True, strict_order: bool = True) -> bool:
    """只读判定物理结构是否与指纹逐字一致。

    校验维度（任一不符返回 False；introspection 异常向上传播，由 detect_schema_status
    统一转 UNKNOWN，不静默吞掉）：
    - 每张受管表存在（_table_exists；缺表返回 False）；
    - 列集合精确（reject_extra=True 时拒绝多余列）；
    - 列顺序：strict_order=True（默认）要求顺序与指纹一致；False 仅校验集合；
    - 列类型一致；
    - 物理 NOT NULL 一致（PRAGMA table_info.notnull）；
    - DEFAULT 规范化一致；
    - PK 列与顺序一致（duckdb_constraints，PK 顺序始终强校验）；
    - 外键一致（duckdb_constraints）；
    - UNIQUE 约束一致（duckdb_constraints，P0-4）；
    - CHECK 约束一致（duckdb_constraints，P0-4）。

    **不**拒绝无关 canonical 表（如 stock_daily/etf_basic 等框架表）——reject_extra
    仅作用于"受管表内部的多余列"与"受管表缺失"。状态机用 KNOWN_SHADOW_TABLES 等常量
    单独判定 shadow/migration 残留（见 qfq_schema_status）。
    """
    for table, spec in fingerprint.items():
        # 先明确判存在（缺表返回 False，不依赖 _table_columns 的空集歧义）
        if not _table_exists(conn, table):
            return False
        actual = _table_columns(conn, table)
        exp_cols = spec.get("columns", [])
        actual_order = list(actual.keys())
        exp_order = [c[0].lower() for c in exp_cols]
        if reject_extra:
            # 集合精确（不多不少）
            if set(actual_order) != set(exp_order):
                return False
            if strict_order and actual_order != exp_order:
                return False
        else:
            if not set(exp_order) <= set(actual_order):
                return False
        # 类型 / NOT NULL / DEFAULT
        for (cname, ctype, cnotnull, cdefault) in exp_cols:
            cn = cname.lower()
            a = actual.get(cn)
            if a is None:
                return False
            if a["type"] != ctype:
                return False
            if a["not_null"] != bool(cnotnull):
                return False
            if a["default"] != cdefault:
                return False
        # PK 列与顺序（始终强校验顺序）
        if _table_pk(conn, table) != [c.lower() for c in spec.get("primary_key", [])]:
            return False
        # 外键
        actual_fks = _table_foreign_keys(conn, table)
        exp_fks = spec.get("foreign_keys", [])
        if len(actual_fks) != len(exp_fks):
            return False
        for efk in exp_fks:
            if not any(
                afk["columns"] == [c.lower() for c in efk["columns"]]
                and afk["referenced_table"] == efk["referenced_table"].lower()
                and afk["referenced_columns"] == [c.lower() for c in efk["referenced_columns"]]
                for afk in actual_fks
            ):
                return False
        # UNIQUE 约束（P0-4）：精确集合一致（顺序无关，按列名集合排序比较）
        actual_unique = sorted(sorted(u) for u in _table_unique_constraints(conn, table))
        exp_unique = sorted(sorted(u) for u in spec.get("unique", []))
        if actual_unique != exp_unique:
            return False
        # CHECK 约束（P0-4）：精确集合一致
        actual_check = sorted(_table_check_constraints(conn, table))
        exp_check = sorted(spec.get("check", []))
        if actual_check != exp_check:
            return False
    return True


# ---------------------------------------------------------------------------
# 共享 DDL（source_watermark）—— writers.py 与 QFQ schema 共同引用，单一真相源
# ---------------------------------------------------------------------------

SOURCE_WATERMARK_2_0_DDL = """
    CREATE TABLE IF NOT EXISTS source_watermark (
        source             VARCHAR,
        table_name         VARCHAR,
        freq               VARCHAR,
        last_date          BIGINT,
        last_batch_id      VARCHAR,
        updated_at         TIMESTAMP,
        PRIMARY KEY (source, table_name, freq)
    )"""

# 最终 2.1：审计列 source_generation/cutover_id 均 NOT NULL 无 DEFAULT；PK 不变。
# 历史行回填属 B-3b；新空库不得产生 NULL 审计字段（写入方经 pre_cutover_generation 提供确定值）。
SOURCE_WATERMARK_2_1_DDL = """
    CREATE TABLE IF NOT EXISTS source_watermark (
        source             VARCHAR,
        table_name         VARCHAR,
        freq               VARCHAR,
        last_date          BIGINT,
        last_batch_id      VARCHAR,
        updated_at         TIMESTAMP,
        source_generation  VARCHAR NOT NULL,
        cutover_id         VARCHAR NOT NULL,
        PRIMARY KEY (source, table_name, freq)
    )"""


# ---------------------------------------------------------------------------
# source_watermark 指纹
# ---------------------------------------------------------------------------

LEGACY_SOURCE_WATERMARK_FINGERPRINT: Fingerprint = {
    "columns": [
        # PK 列物理 NOT NULL（DuckDB PRAGMA table_info 对 PK 列报 notnull=True）
        ("source", "VARCHAR", True, None),
        ("table_name", "VARCHAR", True, None),
        ("freq", "VARCHAR", True, None),
        ("last_date", "BIGINT", False, None),
        ("last_batch_id", "VARCHAR", False, None),
        ("updated_at", "TIMESTAMP", False, None),
    ],
    "primary_key": ["source", "table_name", "freq"],
    "unique": [],
    "check": [],
    "foreign_keys": [],
}

TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT: Fingerprint = {
    "columns": [
        # PK 列物理 NOT NULL（DuckDB PRAGMA table_info 对 PK 列报 notnull=True）
        ("source", "VARCHAR", True, None),
        ("table_name", "VARCHAR", True, None),
        ("freq", "VARCHAR", True, None),
        ("last_date", "BIGINT", False, None),
        ("last_batch_id", "VARCHAR", False, None),
        ("updated_at", "TIMESTAMP", False, None),
        # 审计列 NOT NULL 无 DEFAULT
        ("source_generation", "VARCHAR", True, None),
        ("cutover_id", "VARCHAR", True, None),
    ],
    "primary_key": ["source", "table_name", "freq"],
    "unique": [],
    "check": [],
    "foreign_keys": [],
}


# ---------------------------------------------------------------------------
# LEGACY 2.0 QFQ 指纹（11 表，来自 B-3R 实测真实 2.0 物理结构）
# ---------------------------------------------------------------------------
# not_null 取物理语义：DDL 显式 NOT NULL 的列 + 表级复合 PK 列均标 True。
# default 取 DDL 字面量规范化（DEFAULT 0 → "0"）。

LEGACY_QFQ_2_0_FINGERPRINT: FingerprintDB = {
    "qfq_anchor_state": {
        "columns": [
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", True, None),
            ("price_source", "VARCHAR", True, None),
            ("detection_source", "VARCHAR", False, None),
            ("detection_anchor_factor", "DOUBLE", False, None),
            ("factor_date", "BIGINT", False, None),
            ("anchor_version", "BIGINT", False, None),
            ("status", "VARCHAR", True, None),
            ("locked_detection_factor", "DOUBLE", False, None),
            ("locked_price_anchor_version", "BIGINT", False, None),
            ("last_ex_date", "BIGINT", False, None),
            ("last_event_id", "VARCHAR", False, None),
            ("retry_count", "INTEGER", False, "0"),
            ("last_stale_probe_at", "TIMESTAMP", False, None),
            ("last_stale_probe_error", "VARCHAR", False, None),
            ("probe_fail_count", "INTEGER", False, "0"),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["asset_type", "code", "price_source"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_reanchor_event": {
        "columns": [
            ("event_id", "VARCHAR", True, None),
            ("event_type", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", False, None),
            ("price_source", "VARCHAR", False, None),
            ("detection_source", "VARCHAR", False, None),
            ("old_factor", "DOUBLE", False, None),
            ("new_factor", "DOUBLE", False, None),
            ("old_factor_date", "BIGINT", False, None),
            ("new_factor_date", "BIGINT", False, None),
            ("daily_method", "VARCHAR", False, None),
            ("minute_ratio_plan", "VARCHAR", False, None),
            ("xtquant_price_ratio", "DOUBLE", False, None),
            ("ratio_dispersion", "DOUBLE", False, None),
            ("ratio_cluster_count", "INTEGER", False, None),
            ("golden_check", "VARCHAR", False, None),
            ("status", "VARCHAR", True, None),
            ("block_reason", "VARCHAR", False, None),
            ("error", "VARCHAR", False, None),
            ("precheck_summary", "VARCHAR", False, None),
            ("postcheck_summary", "VARCHAR", False, None),
            ("rows_detail", "VARCHAR", False, None),
            ("rows_stock_daily", "BIGINT", False, None),
            ("rows_stock_minutes", "BIGINT", False, None),
            ("rows_etf_daily", "BIGINT", False, None),
            ("rows_etf_minutes", "BIGINT", False, None),
            ("collection_mode", "VARCHAR", False, None),
            ("trigger_surface", "VARCHAR", False, None),
            ("bootstrap_run_id", "VARCHAR", False, None),
            ("cycle_business_date", "BIGINT", False, None),
            ("occurrence_count", "BIGINT", False, "1"),
            ("started_at", "TIMESTAMP", False, None),
            ("finished_at", "TIMESTAMP", False, None),
            ("created_at", "TIMESTAMP", True, None),
            ("first_seen_at", "TIMESTAMP", True, None),
            ("last_seen_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["event_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_pending_backfill": {
        "columns": [
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", True, None),
            ("table_name", "VARCHAR", True, None),
            ("freq", "VARCHAR", True, None),
            ("range_start", "BIGINT", True, None),
            ("range_end", "BIGINT", True, None),
            ("reason", "VARCHAR", True, None),
            ("anchor_version", "BIGINT", False, None),
            ("status", "VARCHAR", True, None),
            ("attempt_count", "INTEGER", False, "0"),
            ("last_error", "VARCHAR", False, None),
            ("trigger_id", "VARCHAR", False, None),
            ("last_event_id", "VARCHAR", False, None),
            ("next_retry_at", "TIMESTAMP", False, None),
            ("claimed_by", "VARCHAR", False, None),
            ("claimed_at", "TIMESTAMP", False, None),
            ("dead_letter_at", "TIMESTAMP", False, None),
            ("created_at", "TIMESTAMP", True, None),
            ("updated_at", "TIMESTAMP", True, None),
            ("resolved_at", "TIMESTAMP", False, None),
        ],
        "primary_key": ["asset_type", "code", "table_name", "freq", "range_start", "range_end"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_bootstrap_run": {
        "columns": [
            ("bootstrap_run_id", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", False, None),
            ("params", "VARCHAR", False, None),
            ("resume_cursor", "VARCHAR", False, None),
            ("total_count", "BIGINT", False, None),
            ("completed_count", "BIGINT", False, None),
            ("blocked_count", "BIGINT", False, None),
            ("failed_count", "BIGINT", False, None),
            ("status", "VARCHAR", False, None),
            ("schema_version", "VARCHAR", False, None),
            ("config_hash", "VARCHAR", False, None),
            ("baseline_version", "VARCHAR", False, None),
            ("started_at", "TIMESTAMP", False, None),
            ("updated_at", "TIMESTAMP", False, None),
        ],
        "primary_key": ["bootstrap_run_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_bootstrap_item": {
        "columns": [
            ("bootstrap_run_id", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", True, None),
            ("status", "VARCHAR", True, None),
            ("attempt_count", "INTEGER", False, "0"),
            ("block_reason", "VARCHAR", False, None),
            ("last_error", "VARCHAR", False, None),
            ("started_at", "TIMESTAMP", False, None),
            ("finished_at", "TIMESTAMP", False, None),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["bootstrap_run_id", "asset_type", "code"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "trade_calendar": {
        "columns": [
            # 列顺序 = 正式库实际 introspection 顺序（P1-3 修正：不再用 DDL 声明顺序，
            # 改用正式库真实顺序，使严格列顺序校验对生产 legacy 库成立）。
            ("cal_date", "BIGINT", True, None),
            ("is_open", "BOOLEAN", True, None),
            ("source", "VARCHAR", False, None),
            ("updated_at", "TIMESTAMP", False, None),
            ("exchange", "VARCHAR", False, None),
            ("pretrade_date", "BIGINT", False, None),
        ],
        "primary_key": ["cal_date"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_cycle_run": {
        "columns": [
            ("cycle_id", "VARCHAR", True, None),
            ("business_date", "BIGINT", False, None),
            ("trigger_surface", "VARCHAR", False, None),
            ("config_hash", "VARCHAR", False, None),
            ("schema_hash", "VARCHAR", False, None),
            ("phase", "VARCHAR", True, None),
            ("discovered_count", "BIGINT", False, "0"),
            ("executed_count", "BIGINT", False, "0"),
            ("success_count", "BIGINT", False, "0"),
            ("failed_count", "BIGINT", False, "0"),
            ("pending_count", "BIGINT", False, "0"),
            ("status", "VARCHAR", True, None),
            ("started_at", "TIMESTAMP", True, None),
            ("finished_at", "TIMESTAMP", False, None),
            ("error", "VARCHAR", False, None),
            ("detector_degraded", "BOOLEAN", False, "0"),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["cycle_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_trigger_queue": {
        "columns": [
            ("trigger_id", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", True, None),
            ("trigger_type", "VARCHAR", True, None),
            ("detection_source", "VARCHAR", True, None),
            ("source_key", "VARCHAR", False, None),
            ("effective_date", "BIGINT", False, None),
            ("payload_hash", "VARCHAR", False, None),
            ("factor_old", "DOUBLE", False, None),
            ("factor_new", "DOUBLE", False, None),
            ("factor_revision", "BIGINT", False, None),
            ("status", "VARCHAR", True, None),
            ("attempt_count", "INTEGER", False, "0"),
            ("next_retry_at", "TIMESTAMP", False, None),
            ("claimed_by", "VARCHAR", False, None),
            ("claimed_at", "TIMESTAMP", False, None),
            ("last_event_id", "VARCHAR", False, None),
            ("last_error", "VARCHAR", False, None),
            ("dead_letter_at", "TIMESTAMP", False, None),
            ("created_at", "TIMESTAMP", True, None),
            ("updated_at", "TIMESTAMP", True, None),
            ("completed_at", "TIMESTAMP", False, None),
        ],
        "primary_key": ["trigger_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_watermark_intent": {
        "columns": [
            ("cycle_id", "VARCHAR", True, None),
            ("source", "VARCHAR", True, None),
            ("table_name", "VARCHAR", True, None),
            ("freq", "VARCHAR", True, None),
            ("old_watermark", "VARCHAR", False, None),
            ("candidate_watermark", "VARCHAR", False, None),
            ("status", "VARCHAR", True, None),
            ("hold_reason", "VARCHAR", False, None),
            ("committed_at", "TIMESTAMP", False, None),
        ],
        "primary_key": ["cycle_id", "source", "table_name", "freq"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_fresh_capture": {
        "columns": [
            ("capture_id", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", True, None),
            ("source", "VARCHAR", False, None),
            ("daily_range_start", "BIGINT", False, None),
            ("daily_range_end", "BIGINT", False, None),
            ("minute_range_start", "BIGINT", False, None),
            ("minute_range_end", "BIGINT", False, None),
            ("daily_row_count", "BIGINT", False, None),
            ("minute_row_count", "BIGINT", False, None),
            ("daily_min_time", "BIGINT", False, None),
            ("daily_max_time", "BIGINT", False, None),
            ("minute_min_time", "BIGINT", False, None),
            ("minute_max_time", "BIGINT", False, None),
            ("daily_sha256", "VARCHAR", False, None),
            ("minute_sha256", "VARCHAR", False, None),
            ("metadata_sha256", "VARCHAR", False, None),
            ("download_trace", "VARCHAR", False, None),
            ("status", "VARCHAR", False, None),
            ("created_at", "TIMESTAMP", True, None),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["capture_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_observation_cursor": {
        "columns": [
            ("detector_name", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", True, None),
            ("cursor_as_of", "BIGINT", False, None),
            ("last_run_id", "VARCHAR", False, None),
            ("scan_range_start", "BIGINT", False, None),
            ("scan_range_end", "BIGINT", False, None),
            ("status", "VARCHAR", False, None),
            ("last_error", "VARCHAR", False, None),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["detector_name", "asset_type"],
        "unique": [], "check": [], "foreign_keys": [],
    },
}


# ---------------------------------------------------------------------------
# TARGET 2.1 QFQ 指纹（15 表：11 升级 + 4 新表）
# ---------------------------------------------------------------------------
# 与 legacy 的差异：
# - qfq_trigger_queue +6 列（trigger_id_version/price_source/source_generation/cutover_id
#   NOT NULL；retired_at/retire_reason nullable）
# - qfq_cycle_run / qfq_bootstrap_run +3 列（price_source/source_generation/cutover_id NOT NULL）
# - qfq_fresh_capture / qfq_reanchor_event +2 列（source_generation/cutover_id NOT NULL）
# - qfq_pending_backfill +2 列（price_source/source_generation）且 PK 扩 8 列
# - qfq_observation_cursor +2 列（price_source/source_generation）且 PK 扩 4 列
# - qfq_anchor_state +1 列（source_generation）且 PK 扩 4 列
# - qfq_watermark_intent +2 列（source_generation/cutover_id）且 PK 扩 6 列
# - 新增 4 表：discovery_baseline / source_cutover / active_cutover / cycle_lease
# 所有新列在 target 均 NOT NULL 无业务 DEFAULT（新空库无存量行；存量回填属 B-3b）。

def _t(*xs):  # type: ignore[no-untyped-def]
    """构造单列元组便捷别名。"""
    return xs


TARGET_QFQ_2_1_FINGERPRINT: FingerprintDB = {
    # ---- qfq_anchor_state：+ source_generation，PK 扩 4 列 ----
    "qfq_anchor_state": {
        "columns": [
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", True, None),
            ("price_source", "VARCHAR", True, None),
            ("source_generation", "VARCHAR", True, None),  # B-3 新列
            ("detection_source", "VARCHAR", False, None),
            ("detection_anchor_factor", "DOUBLE", False, None),
            ("factor_date", "BIGINT", False, None),
            ("anchor_version", "BIGINT", False, None),
            ("status", "VARCHAR", True, None),
            ("locked_detection_factor", "DOUBLE", False, None),
            ("locked_price_anchor_version", "BIGINT", False, None),
            ("last_ex_date", "BIGINT", False, None),
            ("last_event_id", "VARCHAR", False, None),
            ("retry_count", "INTEGER", False, "0"),
            ("last_stale_probe_at", "TIMESTAMP", False, None),
            ("last_stale_probe_error", "VARCHAR", False, None),
            ("probe_fail_count", "INTEGER", False, "0"),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["asset_type", "code", "price_source", "source_generation"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    # ---- qfq_reanchor_event：+ source_generation/cutover_id ----
    "qfq_reanchor_event": {
        "columns": [
            ("event_id", "VARCHAR", True, None),
            ("event_type", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", False, None),
            ("price_source", "VARCHAR", False, None),
            ("source_generation", "VARCHAR", True, None),  # B-3 新列
            ("cutover_id", "VARCHAR", True, None),          # B-3 新列
            ("detection_source", "VARCHAR", False, None),
            ("old_factor", "DOUBLE", False, None),
            ("new_factor", "DOUBLE", False, None),
            ("old_factor_date", "BIGINT", False, None),
            ("new_factor_date", "BIGINT", False, None),
            ("daily_method", "VARCHAR", False, None),
            ("minute_ratio_plan", "VARCHAR", False, None),
            ("xtquant_price_ratio", "DOUBLE", False, None),
            ("ratio_dispersion", "DOUBLE", False, None),
            ("ratio_cluster_count", "INTEGER", False, None),
            ("golden_check", "VARCHAR", False, None),
            ("status", "VARCHAR", True, None),
            ("block_reason", "VARCHAR", False, None),
            ("error", "VARCHAR", False, None),
            ("precheck_summary", "VARCHAR", False, None),
            ("postcheck_summary", "VARCHAR", False, None),
            ("rows_detail", "VARCHAR", False, None),
            ("rows_stock_daily", "BIGINT", False, None),
            ("rows_stock_minutes", "BIGINT", False, None),
            ("rows_etf_daily", "BIGINT", False, None),
            ("rows_etf_minutes", "BIGINT", False, None),
            ("collection_mode", "VARCHAR", False, None),
            ("trigger_surface", "VARCHAR", False, None),
            ("bootstrap_run_id", "VARCHAR", False, None),
            ("cycle_business_date", "BIGINT", False, None),
            ("occurrence_count", "BIGINT", False, "1"),
            ("started_at", "TIMESTAMP", False, None),
            ("finished_at", "TIMESTAMP", False, None),
            ("created_at", "TIMESTAMP", True, None),
            ("first_seen_at", "TIMESTAMP", True, None),
            ("last_seen_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["event_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    # ---- qfq_pending_backfill：+ price_source/source_generation，PK 扩 8 列 ----
    "qfq_pending_backfill": {
        "columns": [
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", True, None),
            ("table_name", "VARCHAR", True, None),
            ("freq", "VARCHAR", True, None),
            ("range_start", "BIGINT", True, None),
            ("range_end", "BIGINT", True, None),
            ("price_source", "VARCHAR", True, None),       # B-3 新列
            ("source_generation", "VARCHAR", True, None),   # B-3 新列
            ("reason", "VARCHAR", True, None),
            ("anchor_version", "BIGINT", False, None),
            ("status", "VARCHAR", True, None),
            ("attempt_count", "INTEGER", False, "0"),
            ("last_error", "VARCHAR", False, None),
            ("trigger_id", "VARCHAR", False, None),
            ("last_event_id", "VARCHAR", False, None),
            ("next_retry_at", "TIMESTAMP", False, None),
            ("claimed_by", "VARCHAR", False, None),
            ("claimed_at", "TIMESTAMP", False, None),
            ("dead_letter_at", "TIMESTAMP", False, None),
            ("created_at", "TIMESTAMP", True, None),
            ("updated_at", "TIMESTAMP", True, None),
            ("resolved_at", "TIMESTAMP", False, None),
        ],
        "primary_key": ["asset_type", "code", "table_name", "freq",
                        "range_start", "range_end", "price_source", "source_generation"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_bootstrap_run": {
        "columns": [
            ("bootstrap_run_id", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", False, None),
            ("params", "VARCHAR", False, None),
            ("resume_cursor", "VARCHAR", False, None),
            ("total_count", "BIGINT", False, None),
            ("completed_count", "BIGINT", False, None),
            ("blocked_count", "BIGINT", False, None),
            ("failed_count", "BIGINT", False, None),
            ("status", "VARCHAR", False, None),
            ("schema_version", "VARCHAR", False, None),
            ("config_hash", "VARCHAR", False, None),
            ("baseline_version", "VARCHAR", False, None),
            ("price_source", "VARCHAR", True, None),       # B-3 新列
            ("source_generation", "VARCHAR", True, None),   # B-3 新列
            ("cutover_id", "VARCHAR", True, None),          # B-3 新列
            ("started_at", "TIMESTAMP", False, None),
            ("updated_at", "TIMESTAMP", False, None),
        ],
        "primary_key": ["bootstrap_run_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_bootstrap_item": {
        "columns": [
            ("bootstrap_run_id", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", True, None),
            ("status", "VARCHAR", True, None),
            ("attempt_count", "INTEGER", False, "0"),
            ("block_reason", "VARCHAR", False, None),
            ("last_error", "VARCHAR", False, None),
            ("started_at", "TIMESTAMP", False, None),
            ("finished_at", "TIMESTAMP", False, None),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["bootstrap_run_id", "asset_type", "code"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "trade_calendar": {
        "columns": [
            # 列顺序 = 正式库实际 introspection 顺序（P1-3：与 LEGACY 一致，避免 B-3b 重建）
            ("cal_date", "BIGINT", True, None),
            ("is_open", "BOOLEAN", True, None),
            ("source", "VARCHAR", False, None),
            ("updated_at", "TIMESTAMP", False, None),
            ("exchange", "VARCHAR", False, None),
            ("pretrade_date", "BIGINT", False, None),
        ],
        "primary_key": ["cal_date"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    # ---- qfq_cycle_run：+ price_source/source_generation/cutover_id ----
    "qfq_cycle_run": {
        "columns": [
            ("cycle_id", "VARCHAR", True, None),
            ("business_date", "BIGINT", False, None),
            ("trigger_surface", "VARCHAR", False, None),
            ("config_hash", "VARCHAR", False, None),
            ("schema_hash", "VARCHAR", False, None),
            ("phase", "VARCHAR", True, None),
            ("discovered_count", "BIGINT", False, "0"),
            ("executed_count", "BIGINT", False, "0"),
            ("success_count", "BIGINT", False, "0"),
            ("failed_count", "BIGINT", False, "0"),
            ("pending_count", "BIGINT", False, "0"),
            ("status", "VARCHAR", True, None),
            ("started_at", "TIMESTAMP", True, None),
            ("finished_at", "TIMESTAMP", False, None),
            ("error", "VARCHAR", False, None),
            ("detector_degraded", "BOOLEAN", False, "0"),
            ("price_source", "VARCHAR", True, None),       # B-3 新列
            ("source_generation", "VARCHAR", True, None),   # B-3 新列
            ("cutover_id", "VARCHAR", True, None),          # B-3 新列
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["cycle_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    # ---- qfq_trigger_queue：+ 6 列 ----
    "qfq_trigger_queue": {
        "columns": [
            ("trigger_id", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", True, None),
            ("trigger_type", "VARCHAR", True, None),
            ("detection_source", "VARCHAR", True, None),
            ("source_key", "VARCHAR", False, None),
            ("effective_date", "BIGINT", False, None),
            ("payload_hash", "VARCHAR", False, None),
            ("factor_old", "DOUBLE", False, None),
            ("factor_new", "DOUBLE", False, None),
            ("factor_revision", "BIGINT", False, None),
            ("status", "VARCHAR", True, None),
            ("attempt_count", "INTEGER", False, "0"),
            ("next_retry_at", "TIMESTAMP", False, None),
            ("claimed_by", "VARCHAR", False, None),
            ("claimed_at", "TIMESTAMP", False, None),
            ("last_event_id", "VARCHAR", False, None),
            ("last_error", "VARCHAR", False, None),
            ("dead_letter_at", "TIMESTAMP", False, None),
            ("trigger_id_version", "INTEGER", True, None),  # B-3 新列（v1/legacy=1；MCP v2=2）
            ("price_source", "VARCHAR", True, None),        # B-3 新列
            ("source_generation", "VARCHAR", True, None),    # B-3 新列
            ("cutover_id", "VARCHAR", True, None),           # B-3 新列
            ("retired_at", "TIMESTAMP", False, None),        # B-3 新列（退役时间，nullable）
            ("retire_reason", "VARCHAR", False, None),       # B-3 新列（退役原因，nullable）
            ("created_at", "TIMESTAMP", True, None),
            ("updated_at", "TIMESTAMP", True, None),
            ("completed_at", "TIMESTAMP", False, None),
        ],
        "primary_key": ["trigger_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    # ---- qfq_watermark_intent：+ source_generation/cutover_id，PK 扩 6 列 ----
    "qfq_watermark_intent": {
        "columns": [
            ("cycle_id", "VARCHAR", True, None),
            ("source", "VARCHAR", True, None),
            ("table_name", "VARCHAR", True, None),
            ("freq", "VARCHAR", True, None),
            ("source_generation", "VARCHAR", True, None),   # B-3 新列
            ("cutover_id", "VARCHAR", True, None),           # B-3 新列
            ("old_watermark", "VARCHAR", False, None),
            ("candidate_watermark", "VARCHAR", False, None),
            ("status", "VARCHAR", True, None),
            ("hold_reason", "VARCHAR", False, None),
            ("committed_at", "TIMESTAMP", False, None),
        ],
        "primary_key": ["cycle_id", "source", "table_name", "freq",
                        "source_generation", "cutover_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    # ---- qfq_fresh_capture：+ source_generation/cutover_id ----
    "qfq_fresh_capture": {
        "columns": [
            ("capture_id", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", True, None),
            ("code", "VARCHAR", True, None),
            ("source", "VARCHAR", False, None),
            ("daily_range_start", "BIGINT", False, None),
            ("daily_range_end", "BIGINT", False, None),
            ("minute_range_start", "BIGINT", False, None),
            ("minute_range_end", "BIGINT", False, None),
            ("daily_row_count", "BIGINT", False, None),
            ("minute_row_count", "BIGINT", False, None),
            ("daily_min_time", "BIGINT", False, None),
            ("daily_max_time", "BIGINT", False, None),
            ("minute_min_time", "BIGINT", False, None),
            ("minute_max_time", "BIGINT", False, None),
            ("daily_sha256", "VARCHAR", False, None),
            ("minute_sha256", "VARCHAR", False, None),
            ("metadata_sha256", "VARCHAR", False, None),
            ("download_trace", "VARCHAR", False, None),
            ("status", "VARCHAR", False, None),
            ("source_generation", "VARCHAR", True, None),   # B-3 新列
            ("cutover_id", "VARCHAR", True, None),           # B-3 新列
            ("created_at", "TIMESTAMP", True, None),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["capture_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    # ---- qfq_observation_cursor：+ price_source/source_generation，PK 扩 4 列 ----
    "qfq_observation_cursor": {
        "columns": [
            ("detector_name", "VARCHAR", True, None),
            ("asset_type", "VARCHAR", True, None),
            ("price_source", "VARCHAR", True, None),       # B-3 新列
            ("source_generation", "VARCHAR", True, None),   # B-3 新列
            ("cursor_as_of", "BIGINT", False, None),
            ("last_run_id", "VARCHAR", False, None),
            ("scan_range_start", "BIGINT", False, None),
            ("scan_range_end", "BIGINT", False, None),
            ("status", "VARCHAR", False, None),
            ("last_error", "VARCHAR", False, None),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["detector_name", "asset_type", "price_source", "source_generation"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    # ============================ B-3 新表 ============================
    "qfq_discovery_baseline": {
        "columns": [
            ("cutover_id", "VARCHAR", True, None),
            ("price_source", "VARCHAR", True, None),
            ("source_generation", "VARCHAR", True, None),
            ("event_logical_key", "VARCHAR", True, None),
            ("applied_payload_hash", "VARCHAR", False, None),  # v2.4：允许 NULL（新事件首行）
            ("pending_trigger_id", "VARCHAR", False, None),
            ("pending_payload_hash", "VARCHAR", False, None),
            ("last_trigger_id", "VARCHAR", False, None),
            ("applied_at", "TIMESTAMP", False, None),
            ("baselined_at", "TIMESTAMP", True, None),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["cutover_id", "event_logical_key"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_source_cutover": {
        "columns": [
            ("cutover_id", "VARCHAR", True, None),
            ("price_source", "VARCHAR", True, None),
            ("source_generation", "VARCHAR", True, None),
            ("cutover_time", "TIMESTAMP", True, None),
            ("price_snapshot_version", "VARCHAR", False, None),
            ("factor_snapshot_version", "VARCHAR", False, None),
            ("baseline_version", "VARCHAR", True, None),
            ("schema_version", "VARCHAR", True, None),
            ("config_hash", "VARCHAR", False, None),
            ("aux_db_path", "VARCHAR", False, None),
            ("status", "VARCHAR", True, None),
            ("evidence_path", "VARCHAR", False, None),
            ("created_at", "TIMESTAMP", True, None),
            ("updated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["cutover_id"],
        "unique": [], "check": [], "foreign_keys": [],
    },
    "qfq_active_cutover": {
        "columns": [
            ("price_source", "VARCHAR", True, None),
            ("cutover_id", "VARCHAR", True, None),
            ("activated_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["price_source"],
        "unique": [], "check": [],
        "foreign_keys": [
            {"columns": ["cutover_id"],
             "referenced_table": "qfq_source_cutover",
             "referenced_columns": ["cutover_id"]},
        ],
    },
    "qfq_cycle_lease": {
        "columns": [
            ("price_source", "VARCHAR", True, None),
            ("source_generation", "VARCHAR", True, None),
            ("cycle_id", "VARCHAR", True, None),
            ("owner_pid", "BIGINT", True, None),
            ("owner_cmdline_hash", "VARCHAR", True, None),
            ("acquired_at", "TIMESTAMP", True, None),
            ("expires_at", "TIMESTAMP", True, None),
        ],
        "primary_key": ["price_source", "source_generation"],
        "unique": [], "check": [], "foreign_keys": [],
    },
}


# ---------------------------------------------------------------------------
# 聚合主库指纹（QFQ 管理表 + source_watermark）
# ---------------------------------------------------------------------------

def _merge(*dbs: FingerprintDB) -> FingerprintDB:
    out: FingerprintDB = {}
    for db in dbs:
        out.update(db)
    return out


LEGACY_MAIN_DB_2_0_FINGERPRINT: FingerprintDB = _merge(
    LEGACY_QFQ_2_0_FINGERPRINT,
    {"source_watermark": LEGACY_SOURCE_WATERMARK_FINGERPRINT},
)

TARGET_MAIN_DB_2_1_FINGERPRINT: FingerprintDB = _merge(
    TARGET_QFQ_2_1_FINGERPRINT,
    {"source_watermark": TARGET_SOURCE_WATERMARK_2_1_FINGERPRINT},
)


# ---------------------------------------------------------------------------
# 旧契约形态投影（供 _verify_duckdb_contract 等旧消费者确定性引用）
# ---------------------------------------------------------------------------

def project_legacy_contract_shape(fingerprint: FingerprintDB) -> Dict[str, Dict]:
    """把 full 物理指纹投影成旧 ``_parse_ddl_contract`` 形态：

    ``{table: {"columns": {name: TYPE}, "not_null": [...], "pk": [...]}}``

    来源固定：SCHEMA_CONTRACT_DUCKDB = project_legacy_contract_shape(TARGET_QFQ_2_1_FINGERPRINT)。
    不再从当前 DDL 文本运行时解析（消除"当前 contract 自动等同 2.1"的隐患）。
    """
    out: Dict[str, Dict] = {}
    for table, spec in fingerprint.items():
        columns: Dict[str, str] = {}
        not_null: List[str] = []
        for (cname, ctype, cnotnull, _default) in spec.get("columns", []):
            columns[cname] = ctype
            if cnotnull:
                not_null.append(cname)
        out[table] = {
            "columns": columns,
            "not_null": not_null,
            "pk": list(spec.get("primary_key", [])),
        }
    return out


# ---------------------------------------------------------------------------
# pre-cutover 静态写入映射（B-3a 扩列兼容桥；B-5 替换为动态 active generation）
# ---------------------------------------------------------------------------

# 世代哨兵（与 mcp-cutover-design-v2.md §3.2.1b 一致）
GENERATION_LEGACY_XTQUANT = "xtquant-legacy"
GENERATION_NOT_QFQ_MANAGED = "not-qfq-managed"
CUTOVER_LEGACY_XTQUANT_PRE_CUTOVER = "legacy-xtquant-pre-cutover"
CUTOVER_NOT_APPLICABLE = "not-applicable"


def pre_cutover_generation(table_name: str, source: str) -> Tuple[str, str]:
    """返回 (source_generation, cutover_id) 的 B-3a 静态 pre-cutover 哨兵值。

    规则（mcp-cutover-design-v2.md §3.2.5 source_watermark 世代审计契约）：
    - 四张 QFQ 价格表（stock_daily/stock_minutes/etf_daily/etf_minutes）→
      (xtquant-legacy, legacy-xtquant-pre-cutover)；
    - 其它非 QFQ 表（MCP 财务/基础表、akshare 等历史源、非 QFQ 数据集）→
      (not-qfq-managed, not-applicable)；
    - **source 保留真实值不改写**（source=mcp 仍是 mcp；source=xtquant 仍是 xtquant）。
      generation/cutover 的 legacy 哨兵只表示 active MCP cutover 尚未激活，
      不代表价格实际来自 xtquant。

    本函数是 B-3a 扩列后的静态兼容桥：让既有生产 INSERT 在不查询 active cutover、
    不实现动态世代接管的前提下提供 NOT NULL 审计列确定值。B-5 把这些静态值替换为
    动态 active generation/cutover；B-6 才允许正式 active MCP 提交（mcp/mcp-gen1/<active>）。
    """
    if table_name in QFQ_PRICE_TABLES:
        return (GENERATION_LEGACY_XTQUANT, CUTOVER_LEGACY_XTQUANT_PRE_CUTOVER)
    return (GENERATION_NOT_QFQ_MANAGED, CUTOVER_NOT_APPLICABLE)


def pre_cutover_qfq_identity(price_source: str) -> dict:
    """B-3a QFQ 重锚表写入的**统一静态 pre-cutover identity**（P0-2 冻结）。

    返回 ``{"price_source": price_source, "source_generation": "xtquant-legacy",
    "cutover_id": "legacy-xtquant-pre-cutover"}``。

    关键规则（v2.4 B-3a.3 P0-2）：
    - ``price_source`` 用调用方传入的**真实配置值**（mcp 仍是 mcp，绝不改写为 xtquant）；
    - ``source_generation`` **固定** ``xtquant-legacy``——即使配置显式传 ``mcp-gen1``，
      B-3a 运行路径也**只能**持久化 legacy pre-cutover 哨兵；
    - ``cutover_id`` **固定** ``legacy-xtquant-pre-cutover``——即使配置显式传
      ``cut_not_active`` 等占位值，B-3a 也只能持久化 legacy 哨兵；
    - ``trigger_id_version`` 配合用 ``1``（legacy/v1）。

    这样保证 B-6 active cutover 校验通过前，库中**不出现**看似正式的 mcp-gen1/<active-id>
    状态。B-5 接入动态 generation 传递；B-6 校验 active cutover 后才允许正式
    mcp/mcp-gen1/<active-cutover-id>。

    **所有 B-3a QFQ 重锚表写入（cursor/3类trigger/TriggerRecord/cycle/bootstrap/backfill/
    fresh_capture/event/anchor/intent）必须调用本函数**，不得各自直接读
    ``cfg.source_generation``/``cfg.cutover_id``（静态扫描禁止）。
    """
    return {
        "price_source": price_source,
        "source_generation": GENERATION_LEGACY_XTQUANT,
        "cutover_id": CUTOVER_LEGACY_XTQUANT_PRE_CUTOVER,
    }


# B-3a 静态 pre-cutover trigger_id_version（legacy/v1；MCP v2 由 B-5 写）
PRE_CUTOVER_TRIGGER_ID_VERSION = 1
