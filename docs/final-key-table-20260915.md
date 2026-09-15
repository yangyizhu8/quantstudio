# 终版键表（DTS 兼容重排版）· 2026-09-15

- 出具：数据拉取线（C 组核对责任方）｜裁定：总调度
- 范围：8 张（tdx_theme_news 除外，见注记 B）
- 硬约束（引擎层，N5）：DEDUP 键首列必须是 designated 列 —— 键去 designated 则 ALTER 被拒；designated 在键首但为入库时间则每行唯一 ⇒ DEDUP 恒 no-op

## 一、键表（8 张）

| # | 表 | designatedTimestamp | 原键（C 组清单）| 重排后键（DTS 打头）| 备注 |
|---|---|---|---|---|---|
| 1 | decision_log | ts | (decision_id, ts) | (ts, decision_id) | 首列重排 |
| 2 | feedback_log | ts | (…, ts) | (ts, …) | 首列重排；余项待原 C 组清单逐字填入 |
| 3 | evolution_candidates | ts | (…, ts) | (ts, …) | 同上 |
| 4 | daily_evolution_reviews | ts | (…, ts) | (ts, …) | 同上 |
| 5 | llm_ingest_runs | started_at | (run_id, …) | (started_at, run_id) | 首列重排 |
| 6 | llm_label_audit_runs | started_at | (run_id, …) | (started_at, run_id) | 首列重排 |
| 7 | llm_reextract_runs | started_at | (run_id, …) | (started_at, run_id) | 首列重排 |
| 8 | qfq_checkpoint | fix_time | (ts_code, …) | (fix_time, ts_code) | 首列重排 |

> 待补：第 2/3/4 行的余项需从原对拍件 C 组键定义清单（89e3b7c4）逐字填入 —— 本次会话未持有该清单原文，不臆造。

## 二、注记

### 注记 A · survey_sentiment 侧别（DEDUP 状态两真）

| 侧 | dedup | 依据 |
|---|---|---|
| 云侧 | True | M3 ALTER 落地（Trae 执行）|
| 本机侧 | False | 本机 QDB 127.0.0.1:8812 运行态 tables() 实测 |

归一注记：M3 ALTER 只动云侧，本机侧未同步。本地表级 DEDUP 属根治项（M3 后与 Trae 评审），不在本键表内。
方法提示：survey_sentiment 的 DTS = inserted_at（TIMESTAMP，PARTITION BY MONTH）；surv_date 为 STRING（YYYYMMDD 8 位），不可作 DTS（类型硬约束）。

### 注记 B · tdx_theme_news 除外（NO_IDEMPOTENT 冻结成员）

不进取键表，永久保留 NO_IDEMPOTENT 冻结成员，--fix 永不触。

理由（引擎层，非策略选择）：

| 候选键 | 结果 |
|---|---|
| (ts, doc_id, nlp_model) | ts = 入库时间，每行唯一 ⇒ DEDUP 恒 no-op |
| (doc_id, nlp_model) 去 ts | ALTER 被拒（键首非 designated）|

两向皆不可行。实测：本机 dedup=False；表 16,750 行 / distinct(doc_id) 9,506 / 重复 7,244。

治理归属：7,244 行历史重复 + 跨轮累积 = 应用层收敛，写入方 = aggregate_tdx_theme_sentiment.py（属主域），已登记另单。

## 三、Trae 预检口径（勘误后）

```
对每张表：
  distinct(重排后键) == 云行数  -> 直接 ALTER DEDUP UPSERT KEYS
  distinct(重排后键) != 云行数  -> TRUNCATE + 重推 + 本地去重，再 ALTER
```

依据：重排后键含 designated 列 ⇒ distinct == 行数则无重复可收（ALTER 直接生效）；否则须先去重（否则 ALTER 语义 = 保留末值，可能非预期）。

---

## 四、方法论入册（本轮新增）

1. 「已有 DTS 前置筛」：补 designated 前先查 tables() 的 designatedTimestamp —— 本次 9/9 表均已有，避免了对合规表做无效 re-ALTER；
2. 「键首列 × designated 兼容核对」：任何 DEDUP 键提案须先核 designated 列与键首列一致性（N5）；
3. 「入库时间不可入键」：以入库时间（ts / inserted_at）为键首 ⇒ 每行唯一 ⇒ DEDUP 静默 no-op（无报错）；识别法 = 查该列与行数是否 1:1；
4. 查询通道注记：QDB HTTP /exec 对字符串字面量极敏感 ⇒ 用 PG wire 8812 + psycopg2 参数化；count_distinct() 拒收 DOUBLE ⇒ 数值列唯一度需 cast(x AS string)。
