# Changelog

> 变更日志。框架层**行为变更**必须在此明示（AGENTS.md 铁律：README 正文 + 引用文档 + 变更记录同步更新，缺一不可）。

## 2026-08-16 — ETF T+0/T+1 按代码分类执行（方案二，G1–G4）

**行为变更（带理由）**：`--etf-t0 true` 语义由「全部 ETF T+0」重定义为「按代码分类（per-code）」。
旧语义允许实盘不存在的国内股票型 ETF 当日买入当日卖出，属错误语义；CLI 侧旧「全 T+0」模式不再可达。
仅 `minute-bar-v1` + `etf_t0=True` 生效；`daily-bar-v1` 与 `etf_t0=False`（默认）行为逐位不变（G2 (a) 档 hash 零差异验证）。

- **引擎（G1，代码已含于 ea9cc8a）**：`_is_t0(code)` per-code 分类——`etf_basic.fund_type ∈ {qdii, gold, commodity, bond, money}` → T+0（当日买入即时解锁可卖）；`equity` → T+1（当日新买 `can_sell=0`，卖出成交 0 股，次日盘前解锁）；未知代码（LOF 等）fail-closed 按 T+1。分类缓存经 provider 层装载（仅 minute+true 触发查询；daily/false 零查询零 warning；查询失败 → 全 T+1 + warning）。
- **测试（G1）**：`tests/test_minute_t1.py`（14 例：5 类 T+0、520830、equity 拒单、LOF fail-closed、etf_t0=False 零装载、装载失败 warning、2 日解锁）；`tests/test_minute_order_execution.py` 更新（gold 行）；`tests/conftest.py` 增 `etf_basic` 支持。
- **回归（G2，证据 `docs/evidence/etf-t0-g2-regression-20260816.md`）**：(a) 零差异档——minute 3 格 + daily 3 格全部产物 SHA-256 逐位一致 + 日志零 diff（时间戳/毫秒/导出目录名三类伪影规范化）；(b) per-code 档——24 只差异逐条归因（equity 5 只拒单、520830/LOF 按真实规则 T+0 放行、513100 本地零量仍成交 = 已知撮合近似）。脚本 `scripts/etf_t0_regression.py`（双档自动比对，禁止人工）。
- **skill（G3/G3.5）**：`references/etf-t0-rules.md` 新建；schema 增 `etf_t0_enforcement` / `stop_deferral_semantics`；校验器新增 BLOCK 规则 `STOP-DEFERRAL-SEMANTICS-MISSING` / `ORDER-RETURN-FIELD-READ`（PTrade 可移植策略禁读订单返回字段）/ `NONDETERMINISTIC-ITERATION`（禁 dict/set 迭代顺序依赖）；R5 证据升级 2.1——`reproducibility_artifacts` 第二独立进程三件套 SHA-256 一致才 PASS。
- **平台差异（2026-08-15 PTrade 探针实测，24 只 × 2 轮）**：520830/LOF 平台回测按 T+1 拒单（真实规则 T+0）、513100 平台分钟 bar 零量无法成交（本地零量仍成交 = 已知撮合近似）——策略「触发即锁→尝试卖出→成交 0 股→次日顺延」拒绝处理模式自动吸收（每事件 ≤1 次被拒；不读 `.status`/`.reason`）。
- **文档**：README（per-code 条目 + T2 降级说明）、`docs/strategy_toolbox.md`（§3.7.1 + `order()` 读取边界）、`docs/prompt_engineering.md`（§4 撮合机制两处）、设计文档 `docs/etf-t0-per-code-design.md`（v1.3）。
- **T2 处置（文档显式降级）**：GUI 三态控件未实现——保持布尔 `etf_t0`（默认 False=全部T+1 与 CLI 一致；True=按分类）；「全部T+0 研究模式」暂缓（需引擎扩展，另行立项）。
- **状态**：G4 一次性提交已落本地（含 G1 遗留测试文件 + G2 交付物 + skill 全量 + 上述文档），**未推送**——待 ZCode G4 审核与用户确认后按铁律推送双仓库（`git push origin` 多 push URL）。
