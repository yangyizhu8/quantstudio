# 会话交接说明：pipeline-etf-dividend 集成（QFQ canary 阶段）

- **会话**：QuantStudio 管线接入任务（docs/mcp_migration/pipeline-etf-dividend-integration-plan.md 步骤 1→10）
- **交接时间**：2026-08-17 02:10（约）
- **暂停指令**：《项目稳定化治理方案》（docs/project-stabilization-plan.md v2）冻结窗口期，停止全部写路径任务
- **执行者**：本会话（DeepSeek Harness agent）

---

## 一、进行中任务清单及状态

| # | 任务 | 状态 | 涉及表/范围 | 可否安全中断 |
|---|------|------|-------------|-------------|
| 1 | QFQ canary（9 codes reconcile-once 第四轮） | **已终止**（job pwsh-22 于 02:0x kill） | stock_minutes/stock_daily/qfq_trigger_queue 等 | 是——本轮未 claim 任何 trigger（进程在 fresh 拉取阶段被杀），无写残留 |
| 2 | QFQ canary 前三轮结论（已落库） | 已完成，未通过 | — | — |
| 3 | 分钟 qfq 污染修复（close 因子恢复） | **已完成并落库** | stock_minutes 1412 只/9180 污染日/220.9 万行；etf_minutes 35 只/2610 污染日/47.5 万行 | 数据已写死，无需回滚（ratio 备份在 data/minute_fix_ratio_*.csv） |
| 4 | 分钟 front/back 列随 close 同步缩放 | **已完成并落库** | 同上两表（front/back 8 列 × ratio） | 同上 |
| 5 | canary 9 codes 云端 raw 精确覆盖（1e-9 对齐） | **已完成并落库** | stock_minutes 9 codes 14035 行（open/high/low/close = 云端还原 raw） | 同上 |
| 6 | 118 个 dead_letter trigger 重开为 pending | **已完成并落库** | qfq_trigger_queue（b6_formal_20260807_v2 世代） | 同上 |
| 7 | stock_minutes export 缓存清空（TD-QFQ-FRESH-CACHE 处置） | **已完成** | data/mcp_landing：stock_minutes 40 网格 1953 分片已删；etf_daily/etf_minutes/stock_daily 缓存保留 | — |
| 8 | 全量 trigger 放行（受控 wave，预计 ~11.6 万） | **未开始**（canary 未通过，按 zcode 硬门槛禁止放行） | qfq_trigger_queue pending 33281 个（4999 codes）已就绪 | — |
| 9 | 步骤 10 回归测试 | 未开始 | — | — |
| 10 | 步骤 11 GitHub 双仓库推送 | 未开始（需用户确认 + 治理方案解冻后） | — | — |

## 二、QFQ canary 进展（关键证据）

- **第一轮（08-17 00:38，修复前）**：claimed=5、dead_letter=5，错误 `freq=1min 分钟末bar front vs 日线 front dev=2.37e-02`（600519 6/15）+ `rebase raw OHLC 2165 行不一致`（601628）→ **根因：本地分钟 raw 与云端 fresh raw 未 1e-9 对齐**（本地是 qfq 污染值/因子恢复值，云端是还原 raw）。
- **第二轮（01:21，覆盖后但缓存未清）**：claimed=5、retryable_failed=5，`601628 raw OHLC 2401 行不一致` → **根因：canary fresh 命中 08-11 旧 export 缓存（stale），旧缓存值 ≠ 本地新覆盖值**（TD-QFQ-FRESH-CACHE）。
- **第三轮（01:22，同第二轮）**：同 2401 行不一致。
- **处置已完成**：清空 stock_minutes export 缓存（manifest 40 网格 + 1953 分片文件），使下次 fresh 强制重新 export 当前云端数据。
- **第四轮（02:0x）**：清缓存后重跑，**在 fresh export 阶段被暂停令终止**，未产生 claim/写库，可安全重启。
- **600519 6/15 15:00 修复已验证**：分钟 close=1271.1、close_front=1241.71，与日线 front 1241.7125 一致（dev 0.0002%）。

## 三、新发现未处理问题清单（仅记录，未处理）

| # | 现象 | 疑似根因 | 层级 |
|---|------|---------|------|
| 1 | stock_minutes 6/18 全市场 5227 只末 bar 均为 14:55（缺 14:56-15:00 尾盘 5 分钟），600519 6/18 14:55 close=1251.48 vs 日线 1215（dev 3%） | 云端 stock_minutes 6/18 数据整体截断/缺失尾盘（或云端该日 export 异常）；QFQ cross-check 对"末 bar < 收盘时刻"的日子自动 skip（不 BLOCK），但数据本身缺失仍待核实 | D2（数据层） |
| 2 | canary 9 codes 仍存 47 个"末 bar front vs 日线 front">0.1% 不一致日（如 300750 恒定 0.364%、000858 7/15 dev 3.5%） | 一部分是 6/18 尾盘缺失（skip 不阻塞）；另一部分（300750 恒定偏差）疑似因子基准/除权处理差异，待 canary 重跑后确认是否阻塞 | D2/D3 |
| 3 | 全市场 front 口径不一致剩余：stock 18281 日、etf 19937 日（>0.1%） | 除权日分钟未除权类（600649 6/25 close=57.13 vs daily=3.5，因子表当天有 1.0 重置行）——**另一类问题**，非 qfq 污染，本次未修复 | D2 |
| 4 | 分钟表时间基准混乱：stock_minutes 2026-06-15 起、etf_minutes 2025-01-02 起（窗口不对称）；600519 等缺 08-14 后增量（max=08-13 14:56/15:00） | 08-13 后增量未续跑（曾被 20h 全量重拉占用）；窗口差异是历史拉取范围所致 | D2 |
| 5 | `MCPAdapter._resolve_shard_paths` 在 export_dataset 返回空/缓存 miss 时空 shard_paths → `_read_ckey_cached` IndexError（本次脚本 fetch 踩中，daemon 路径未踩中） | export 缓存 manifest 与分片目录不一致时缺防护；建议加空列表防御（框架层小修，需走六步流水线） | D3（框架） |
| 6 | `_export_cache_manifest.json` 与 `mcp_landing/export`（另一缓存空间）并存，canary 用后者、部分脚本读前者 → 缓存命中判定不一致 | landing_subdir 配置差异（mcp_landing vs mcp_landing/export）；建议统一缓存空间并加 TTL | D3 |
| 7 | ConfigLint WARNING：`qfq_orchestrator.source_generation` 未知键（非阻塞，历史遗留） | 配置键拼写/未注册 | D4 |
| 8 | 215 个 canary 首轮 trigger 中 98 committed 保留、117 failed 已重开为 pending 后部分再次 retryable_failed（601628 5 个，因旧缓存） | 根因同问题 1/2（缓存+数据），重开后待 canary 通过后自然消化 | D2 |

## 四、锁/资源状态

- **QuantStudio daemon**：未运行（本会话未启动；最近一次 daemon 进程已被终止，无 collector lock 文件）。
- **DuckDB 库**：`data/quantstudio.db` 当前无写连接持有者（本会话所有任务已结束/终止）。**注意**：`data/mcp_landing/_audit_scan.py` 进程（PID 24856/33464，02:01 启动，非本会话发起，疑似治理方案审计脚本）仍在运行，若其打开 quantstudio.db 写连接，门槛检查可能被锁——建议治理会话确认该脚本归属。
- **Trae IDE Jedi LSP 进程**（4 个）：无害，仅编辑器语言服务。
- **已创建脚本**（scripts/，均为 dry-run 默认，--apply 才写库）：
  - `restore_minutes_raw.py`（因子恢复 close，已执行完）
  - `restore_minutes_frontback.py`（front/back 缩放，已执行完）
  - `overwrite_minutes_from_cloud.py`（云端 raw 1e-9 覆盖，已执行完）
  - `reopen_deadletter.py`（dead_letter→pending，已执行完）
  - `purge_stock_minutes_cache.py`（清 export 缓存，已执行完）
  - 诊断脚本 `_dbg_*.py`（只读，可留可删）

## 五、恢复建议（用户另行下发指令后）

1. 门槛检查/快照/黄金基线完成前：**禁止**任何 daemon、reconcile-once、数据重拉、缓存清理。
2. 解冻后第一步：重跑 canary（命令见下），验证 fresh export（新缓存）与本地 1e-9 对齐后 canary 全绿：
   ```
   python -m quantstudio.pipeline.qfq_orchestrator_cli --db data\quantstudio.db --config-dir config\profiles\mcp_only --execute --allow-production reconcile-once --codes "000001,000651,000858,002107,300750,600000,600519,601398,601628"
   ```
3. canary 通过（全 committed、单条耗时 <1s）→ 按 zcode 3 项硬门槛进入全量受控 wave（batch 2000 + 积压 >5000 暂停）；不通过 → 回到 §三问题清单处置。
4. 治理方案登记表首批存量输入：可直接采用 §三清单。
