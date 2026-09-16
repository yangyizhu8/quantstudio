# 终版键表（DTS 兼容重排版）· 2026-09-15 · v2 补全版

- 出具：数据拉取线（C 组核对责任方）｜裁定：总调度
- 权威原文：docs/cgroup-key-definition-list-20260915.md（89e3b7c4）—— 本表第 2/3/4 行键值自此直读，与两线中继三值互相印证
- 硬约束（引擎层，N5，**2026-09-16 订正为包含性**）：**DEDUP 键必须【包含】designated 列（位置不限）** —— 原「键首列必须是 designated」系过度推广；实测 7 表落地中 5 张键首列≠designated 却全部 dedup=True 成功。

## 一、键表（8 张）

| # | 表 | designatedTimestamp | 键列（权威清单）| 重排后键（DTS 打头）| 原清单状态 |
|---|---|---|---|---|---|
| 1 | decision_log | ts | decision_id | (ts, decision_id) | 合法多重 |
| 2 | feedback_log | ts | feedback_id | (ts, feedback_id) | 污染(ts全同) 15→13 |
| 3 | evolution_candidates | ts | candidate_id | (ts, candidate_id) | 污染(ts全同) 16→13 |
| 4 | daily_evolution_reviews | ts | trade_date | (ts, trade_date) | 污染(ts全同) 2→1 |
| 5 | llm_ingest_runs | started_at | run_id | (started_at, run_id) | — |
| 6 | llm_label_audit_runs | started_at | run_id | (started_at, run_id) | — |
| 7 | llm_reextract_runs | started_at | run_id | (started_at, run_id) | — |
| 8 | qfq_checkpoint | fix_time | ts_code | (fix_time, ts_code) | — |

> 勘误（权威原文 vs 实测）：原清单对第 2/3/4 行标「需先补 designated」——**该标记已被 2026-09-15 运行态前置筛推翻**：`tables()` 实测 9/9 表**均已有 designatedTimestamp**（第 2/3/4 行 = `ts`）。原标记反映的是清单出件时的状态，**早于 designated 落地**。

> **同口径订正（2026-09-16）**：本表「重排后键（DTS 打头）」做法**保留为无害**（重排后键仍【包含】designated，语义不变），依据由「首列红线」改为「**包含性**」。Trae 预检按实测**无需回退**。

## 二、冻结成员（不进键表）

| 表 | 标记 | 理由 |
|---|---|---|
| tdx_theme_news | NO_IDEMPOTENT（--fix 永不触）| 引擎层两向皆不可行：(ts,doc_id,nlp_model) 因 ts=入库时间每行唯一 ⇒ DEDUP 恒 no-op；去 ts 则 ALTER 被拒。治理=应用层收敛（写入方 aggregate_tdx_theme_sentiment.py 属主域）|
| sw_classify | 冻结现状（引擎约束不可定键）| 按裁定②标注，非留空 |

## 三、注记 A · survey_sentiment 侧别（DEDUP 状态两真）

| 侧 | dedup | 依据 |
|---|---|---|
| 云侧 | True | M3 ALTER 落地（Trae 执行）|
| 本机侧 | False | 本机 QDB 127.0.0.1:8812 运行态 tables() 实测 |

归一注记：M3 ALTER 只动云侧，本机侧未同步。本地表级 DEDUP 属根治项（M3 后与 Trae 评审），不在本键表内。
方法提示：survey_sentiment 的 DTS = inserted_at（TIMESTAMP，PARTITION BY MONTH）；surv_date 为 STRING（YYYYMMDD 8 位），不可作 DTS（类型硬约束）。

## 四、Trae 预检口径（勘误后）

```
对每张表：
  distinct(重排后键) == 云行数  -> 直接 ALTER DEDUP UPSERT KEYS
  distinct(重排后键) != 云行数  -> TRUNCATE + 重推 + 本地去重，再 ALTER
```

依据：重排后键含 designated 列 ⇒ distinct == 行数则无重复可收（ALTER 直接生效）；否则须先去重（否则 ALTER 语义 = 保留末值，可能非预期）。

## 五、方法论入册

1. 「已有 DTS 前置筛」：补 designated 前先查 tables() 的 designatedTimestamp —— 本次 9/9 表均已有，避免了无效 re-ALTER（并推翻原清单的补 DTS 标记）；
2. 「键首列 × designated 兼容核对」：任何 DEDUP 键提案须先核 designated 列与键首列一致性（N5）；
3. 「入库时间不可入键」：以入库时间（ts / inserted_at）为键首 ⇒ 每行唯一 ⇒ DEDUP 静默 no-op（无报错）；识别法 = 查该列与行数是否 1:1；
4. 「键是否含时间列取决于键上重复的时间形态，而非表的主题」：同为演进型的 feedback_log（污染）与 decision_log（合法多重）结论相反，必须逐表实测（权威清单通则）；
5. 查询通道注记：QDB HTTP /exec 对字符串字面量极敏感 ⇒ 用 PG wire 8812 + psycopg2；count_distinct() 拒收 DOUBLE ⇒ 数值列唯一度需 cast(x AS string)。
