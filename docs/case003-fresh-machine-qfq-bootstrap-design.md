# CASE-003 修复设计：全新机 QFQ 冷启动分支（方案②·用户裁定 2026-09-08）

> 状态：方案（六步第 1 步，压缩排期）——呈线调度审计
> 背景：客户首次部署报 `CutoverError: MCP 配置未找到匹配的 staging cutover: 'legacy-xtquant-pre-cutover'`
> 根因：全新机无迁移历史（qfq_source_cutover 空）× 出厂配置 generation_mode=dynamic
> 假定历史存在 → fail-closed 无全新机分支
> 用户裁定：实施方案②（代码层自动分支），不改出厂配置

## 1. 两道门与修法

### 门 1：身份解析（qfq_cutover.resolve_runtime_identity，L93-103）——硬阻断点

现状：无 active 记录 + source_generation≠legacy → 查 staging 记录 → 全新机表空 → CutoverError。

**修法**：新增全新机分支——当 `无 active` 且 `qfq_source_cutover 中该 price_source 零记录`
（= 机器从未经历任何迁移）→ 记日志 → `return pre_cutover_qfq_identity(ps)`（预切换哨兵身份，
price_source 保留真实值 mcp）。**既有机器（有任何记录）走原逻辑不变**——含"有记录但 cfg
不匹配"仍 fail-closed（防配置错误的保护不削弱）。

### 门 2：bootstrap 门槛（qfq_resident_orchestrator，require_bootstrap）——水位保持点

现状：无 completed bootstrap → 每轮 `finalized_held`（数据照写、**水位永不推进**）→
全新机增量模式每天全窗重拉，不可持续。

**修法**：`bootstrap_completed` 检测"全新机且零 bootstrap 运行记录"时，**自动播种一条
completed 的 fresh-deploy 引导记录**（空基线、source 标记 `fresh-deploy-auto`）并返回 True
——全新机本无可对账的旧基线，bootstrap 语义为空集，自动通过即正确语义。既有机器零影响。

### 辅助谓词（共享）

`is_fresh_qfq_machine(conn, price_source) -> bool`：无 active cutover 且该 price_source
在 qfq_source_cutover 零行。纯只读。

## 2. 影响面

- 改动：`qfq_cutover.py`（门 1 分支 + 谓词）、`qfq_resident_orchestrator.py`（门 2 播种）；
- **零改动区**：既有机器全路径（有记录→原逻辑）；QFQ 数据语义/复权口径/快照/门禁阈值；
  出厂配置（保持 enabled=true + dynamic + mcp-gen1）；
- etf_basic"无可用数据源"预计随门 1 修复连带消除（适配器链初始化恢复）——模拟验收中确认，
  若仍存在则另立子项。

## 3. 验收标准

| # | 判据 |
|---|---|
| C1 | 单测：空 cutover 表 → resolve 返回预切换哨兵（不再抛错）；有记录但 cfg 不匹配 → 仍抛错（保护保留） |
| C2 | 单测：空 bootstrap + 全新机 → 自动播种 completed 记录 + 门通过；已有 failed bootstrap 记录的机器 → 不播种（走原门槛） |
| C3 | 我方机器回归：身份解析结果与改动前逐位一致（有 active 记录场景） |
| C4 | **空目录模拟部署**（E2E）：临时空 data/ 目录 + 现行出厂配置 → full_range 启动不抛 CutoverError、etf_basic 源链解析成功、采集批次落库、水位正常推进 |
| C5 | 既有测试套件抽样回归全绿 |

回退条件：任一验收失败 → revert（两文件独立提交）。

## 4. 排期

今日方案+审计 → 用户批准 → 实施+单测（今晚）→ 空目录模拟验收（明晨）→ 随 F 系列批次推送
→ 客户更新通知追加本项（与既定委托合并出具）。

## 5. 变更记录

| 版本 | 内容 |
|---|---|
| v1 | 2026-09-08 初稿（总调度），方案②按用户裁定 |
