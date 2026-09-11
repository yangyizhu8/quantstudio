# -*- coding: utf-8 -*-
"""questdb_table_map.json 生成器（64 表 · T1 解锁交付物 1/2）

数据来源（全部实测，无手工结构猜测）：
  ① QuestDB `SHOW CREATE TABLE <t>` → 列/类型 / 指定时间列 / DEDUP UPSERT KEYS
  ② data/logs/questdb_structure_baseline.json → 行数 / 日期范围
  ③ collector_tasks.json → freq / dataset_kind / 任务名
  ④ ADJUDICATION（下方常量）→ 9 张多时间列表的 watermark_basis 裁决（ETL 更新语义所有者裁定）

双字段设计（总调度 2026-09-12 采纳）：
  date_basis      业务事件时间（语义键）
  watermark_basis 水位推进基准（对齐云端更新语义：晚到/重刷行按 ingest/fetch 追踪）
"""
import json, re, urllib.parse, urllib.request
from pathlib import Path

QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
OUT = QS / 'config/profiles/mcp_only/questdb_table_map.json'

def q(sql):
    url = 'http://127.0.0.1:9000/exec?query=' + urllib.parse.quote(sql)
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode('utf-8'))

# ── 9 张多时间列表裁决（watermark_basis）─────────────────────────────
# 原则（总调度 2026-09-12）：watermark_basis 必须对齐云端更新语义——
# 可能存在晚到行/重刷行的表取 ingest/fetch 类列；日频就地重刷表取业务日。
ADJUDICATION = {
    'index_classify': dict(date_basis='in_date', watermark_basis='ingest_time',
                           rationale='成分类生效日=业务键；out_date 回填属晚到更新，按入库时间追踪'),
    'ths_member': dict(date_basis='in_date', watermark_basis='ingest_time',
                       rationale='成分生效日=业务键；调入调出/回填按入库时间追踪（源止 8/13 属源内现状）'),
    'sector_top300_daily': dict(date_basis='trade_date', watermark_basis='trade_date',
                                rationale='日频截面快照，按业务日就地重刷；created_at 为本地生成时间，重刷即变，不可作水位'),
    'rsshub_raw': dict(date_basis='pub_time', watermark_basis='fetched_at',
                       rationale='抓取型源，晚到抓取普遍 → 按抓取时间追踪'),
    'news_sentiment': dict(date_basis='news_date', watermark_basis='trade_date',
                           rationale='新闻发布日=业务键；增量按对齐交易日窗重拉（需 lookback，见 note）',
                           note='建议回填/增量窗口含 7 日 lookback 覆盖晚到新闻',
                           confidence='assumed'),
    # —— 2026-09-12 实测修正：ingest_time / create_time 在源表中**非空计数=0（全 NULL）**，
    #    窗口过滤 wm >= start 会把全表排除（T2 首轮这 4 表 rows=0）。总调度对 llm_text 三表的
    #    "watermark_basis=ingest_time" 预声明在此与"源内现状"冲突——源内现状即该列为空。
    #    故 watermark_basis 回落到实际有值的事件时间列（date_basis 与 watermark_basis 合流）。
    'llm_text_events': dict(date_basis='publish_time', watermark_basis='publish_time',
                            rationale='源 ingest_time 全 NULL（实测非空=0）-> 水位回落事件时间；'
                                      '通道止 5/7 按源内现状'),
    'llm_text_events_enriched': dict(date_basis='publish_time', watermark_basis='publish_time',
                                     rationale='同 llm_text_events（同族派生表；ingest_time 全 NULL）'),
    'llm_text_raw_feed': dict(date_basis='publish_time', watermark_basis='publish_time',
                              rationale='同 llm_text_events（同族原始流；ingest_time 全 NULL）'),
    'report_rc': dict(date_basis='report_date', watermark_basis='report_date',
                      rationale='源 create_time 全 NULL（实测非空=0）-> 水位回落研报日'),
}

# ── 单位字典（B1-4 纪律：每源声明单位；按列名解析，生成期固化）──────
UNIT_DICT = {
    'vol': '股', 'volume': '股', 'amount': '元', 'turnover': '元',
    'total_mv': '元', 'float_mv': '元', 'circ_mv': '元',
    'total_share': '股', 'float_share': '股', 'share_float': '股',
    'open': '元', 'high': '元', 'low': '元', 'close': '元',
    'pre_close': '元', 'change': '元', 'avg_price': '元',
    'pct_change': '%', 'pct_chg': '%', 'pctChange': '%',
    'pe': '倍', 'pe_ttm': '倍', 'pb': '倍', 'ps': '倍', 'ps_ttm': '倍',
    'dv_ratio': '%', 'dv_ttm': '%',
    'adj_factor': '倍(复权因子)', 'weight': '%', 'float_ratio': '%',
    'price': '元', 'nav': '元', 'unit_nav': '元', 'acc_nav': '元',
    'net_profit_min': '万元', 'net_profit_max': '万元',
    'p_change_min': '%', 'p_change_max': '%',
    'last_parent_net': '万元', 'first_ann_date': '日期',
}
TIME_UNIT = 'QuestDB TIMESTAMP（微秒精度 UTC 存储，原样搬运不加换算）'
CODE_UNIT = '裸 6 位码（QuestDB SYMBOL 原样）'


def parse_create(ddl):
    """解析 SHOW CREATE TABLE → (columns[(name,type)], designated, dedup_keys[])"""
    cols, designated, dedup = [], None, []
    for line in ddl.splitlines():
        s = line.strip().rstrip(',')
        m = re.match(r"^(\w+)\s+([A-Z0-9_]+(?:\(\d+(?:,\d+)?\))?)$", s)
        if m:
            cols.append((m.group(1), m.group(2)))
            continue
        m2 = re.search(r"timestamp\(([\w, ]+)\)", s)
        if m2 and 'PARTITION' in s:
            designated = m2.group(1).strip()
        m3 = re.search(r"DEDUP UPSERT KEYS\(([^)]+)\)", s)
        if m3:
            dedup = [x.strip() for x in m3.group(1).split(',')]
    return cols, designated, dedup


def main():
    baseline = json.loads((QS / 'data/logs/questdb_structure_baseline.json').read_text(encoding='utf-8'))
    tasks = json.loads((QS / 'config/profiles/mcp_only/collector_tasks.json').read_text(encoding='utf-8'))['tasks']
    tmeta = {t.get('table'): t for t in tasks}

    tables, adj_report = {}, []
    for t in sorted(baseline):
        d = q(f'SHOW CREATE TABLE "{t}"')
        ddl = d['dataset'][0][0]
        cols, designated, dedup = parse_create(ddl)
        b = baseline[t]
        adj = ADJUDICATION.get(t, {})
        date_basis = adj.get('date_basis') or designated or (b.get('basis_col'))
        wm_basis = adj.get('watermark_basis') or date_basis
        if t in ADJUDICATION:
            adj_report.append((t, b.get('timestamp_cols'), date_basis, wm_basis,
                               adj.get('confidence', 'decided')))
        units = {c: UNIT_DICT[c] for c, _ in cols if c in UNIT_DICT}
        tm = tmeta.get(t) or {}
        tables[t] = dict(
            source_table=t, target_table=t, source='questdb',
            freq=tm.get('freq', 'daily'), dataset_kind=tm.get('dataset_kind') or 'passthrough',
            task_name=tm.get('name'),
            date_basis=date_basis, watermark_basis=wm_basis,
            watermark_confidence=adj.get('confidence', 'derived'),
            watermark_rationale=adj.get('rationale', 'QuestDB 指定时间列（单时间列，水位=业务键）'),
            dedup_keys=dedup or ([date_basis] if date_basis else []),
            qdb_rows=b.get('qdb_rows'),
            date_min=b.get('basis_min'), date_max=b.get('basis_max'),
            columns=[dict(source=c, target=c, type=ty,
                          unit=('时间' if ty == 'TIMESTAMP' else
                                ('代码' if c in ('ts_code', 'code') else units.get(c, 'source_native'))))
                     for c, ty in cols],
            units_basis='source_native（单位与 QuestDB 源一致，逐列声明见 columns[].unit）',
            date_max_qdb=b.get('basis_max'),
        )

    out = dict(
        _comment='QuestDB 合规源 → duckdb 映射表（64 表）。生成器 scripts/gen_questdb_table_map.py，'
                 '结构来自 QuestDB SHOW CREATE TABLE（实测），双字段 date_basis/watermark_basis 见下。',
        schema_version='1.0',
        source='questdb',
        generated_at='2026-09-12',
        conventions=dict(
            target_name='identity（passthrough 表：duckdb 目标表名 = QuestDB 源表名）',
            column_mapping='identity（列名原样；类型取 columns[].type）',
            time_unit=TIME_UNIT, code_unit=CODE_UNIT,
            unit_basis='source_native + 逐列声明（B1-4 纪律）',
            date_vs_watermark='date_basis=业务事件时间；watermark_basis=水位推进基准（对齐云端更新语义）',
            dedup_source='QuestDB DEDUP UPSERT KEYS（DDL 实测）',
        ),
        adjudication_note='9 张多时间列表的 watermark_basis 由 ETL 更新语义所有者裁定；'
                          'confidence=assumed 者建议首次拉取时复核',
        tables=tables,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')

    print(f"written: {OUT}")
    print(f"tables: {len(tables)}")
    print()
    print("=== 9 表裁决（多时间列）===")
    print(f"{'表':<26} {'候选时间列':<52} {'date_basis':<14} {'watermark_basis':<16} conf")
    for t, ts, db, wb, cf in adj_report:
        print(f"{t:<26} {','.join(ts):<52} {db:<14} {wb:<16} {cf}")
    print()
    miss = [t for t, v in tables.items() if not v['dedup_keys']]
    print(f"无 DEDUP 键表: {len(miss)} {miss}")
    print(f"无 date_basis 表: {len([t for t,v in tables.items() if not v['date_basis']])}")


if __name__ == '__main__':
    main()
