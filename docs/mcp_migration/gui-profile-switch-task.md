# GUI 数据源模式切换任务书（CodeBuddy 执行）

> **版本**：v2.1（2026-08-02，统一正式库+审核全部修订采纳）
> **执行智能体**：CodeBuddy（本地 QuantStudio）
> **审核**：ZCode（方案经严格复审，全部修订意见+默认模式需求采纳）
> **范围**：PyQt采集任务Tab加「数据源模式」切换
> **默认模式**：**MCP权威源为默认显示**，传统多源在切换后显示

---

## 0. 核心需求

在PyQt采集任务Tab加「数据源模式」下拉切换：
- **MCP权威源（默认）** → config_dir = `config/profiles/mcp_only/`
- **传统多源(xtquant/tushare)** → config_dir = `config/`（切换后显示）

旧源配置冻结不删，随时可切回。

---

## 1. 改动清单（6个文件）

### 1.1 main_window.py（核心）
- **root与config_dir解耦**：构造时固定存 `_root`（=项目根），config_dir做成可切换属性
- root_path属性改为返回固定_root（不再=config_dir.parent）
- **新增 set_profile(config_dir) 方法**：
  - 更新 self.config_dir
  - **统一库后DbHelper无需重建**（MCP和传统都用data/quantstudio.db）
  - 逐个通知 Tab 刷新（task_tab/source_tab/其他绑db的Tab）
- check_db_openable 按当前模式检查对应库（统一库后都查quantstudio.db，但仍兜底空库）
- **构造时默认config_dir = ROOT/config/profiles/mcp_only**（MCP为默认）
- **启动模式优先级**：运行中daemon的config_dir > gui_state.json > 默认MCP

### 1.2 db_helper.py
- **文件不存在/表不存在时优雅返回空**（不raise IOException）—— P0
- DuckDB read_only=True打开不存在文件时catch IOException返回空DataFrame/空列表
- **注意：MCP模式和传统模式现在统一使用 data/quantstudio.db**（不再用staging库），DbHelper无需切换库路径

### 1.3 task_tab.py
- 工具栏加 QComboBox「数据源模式」：
  - **默认选中「MCP权威源」**（userData = ROOT/config/profiles/mcp_only）
  - 「传统多源(xtquant/tushare)」（userData = ROOT/config）
- 切换时：
  - 调 mw.set_profile() 切换config_dir（**不重建DbHelper**，统一库无需切换库路径）
  - 重新加载 collector_tasks.json + sources_config.json
  - 刷新任务表格
- **水位列**：用当前DbHelper（不再硬编码db_path()）
- **reset_watermark**：**必须按source过滤**（P0安全）——统一库后两种模式共享source_watermark表，reset只能清当前模式任务的源：
  - MCP模式：`DELETE FROM source_watermark WHERE source = 'mcp'`
  - 传统模式：`DELETE FROM source_watermark WHERE source IN (<当前模式enabled任务的源集合>)`（**动态收集**：从当前config_dir的collector_tasks.json的source/source_priority提取源集合，不硬编码——用户日后换源也能正确过滤）
  - **绝对不能无WHERE全表清空**（现有task_tab.py:678是无WHERE的DELETE，必须改）
- **轮询比对daemon status的config_dir**：不一致给冲突提示
- **切换守卫**：_running_tasks非空或daemon过渡态时禁止切换
- **MCP_API_KEY双重检查**：切到MCP模式时+启动daemon前
- _get_collector用self.mw.config_dir（不硬编码ROOT/config）
- tooltip更新（不再写"编辑配置文件"，改为"通过上方下拉切换数据源模式"）

### 1.4 source_tab.py
- config_path**刷新时重解析**（不缓存__init__里的值）
- **source_defs按当前profile动态生成**：
  - MCP模式：mcp源(base_url/tls_verify/MCP_API_KEY提示) + 旧源(disabled,**只读不可编辑**——profile禁止xtquant/tushare，误启用会污染冻结配置)
  - 传统模式：8个旧源（当前硬编码行为，可编辑）
- MCP源卡片显示：endpoint/MCP_API_KEY配置提示（**静态显示，不做实时ping探测**——ping_health status:error是server侧遗留问题未闭环）

### 1.5 main_gui.py
- **启动时默认config_dir = ROOT/config/profiles/mcp_only**（MCP默认）
- 读 data/gui_state.json 的持久化profile（**存profile键名**"mcp_only"/"default"，不存绝对路径）
- **gui_state写入时机**：切换成功后**立即原子写**（json.dump→临时文件→rename），确保验收#8有实现依据
- **启动模式优先级**：运行中daemon的config_dir > gui_state.json > 默认MCP
- **MCP_API_KEY检查**：启动时如果是MCP模式且缺key，用**非模态InfoBar**提示（附"切换到传统模式"快捷操作，不用模态框每次打断）
- **config_editor_tab**：顶部显示当前编辑的配置目录路径（防止MCP模式下误以为在改默认config/）

### 1.6 daemon_process.py
- check_db_openable接受目标库路径参数（不查全局db_path()）

---

## 2. 不改的东西（冻结）

- 默认 `config/` 的所有文件不改（旧源配置原样保留）
- `config/profiles/mcp_only/` 已就绪不改
- 旧源Adapter代码不删
- daemon的authority guard不改
- aligner/validator/writer不改
- 回测引擎不改

---

## 3. 验收标准

1. **启动GUI默认MCP模式** → 任务列表显示mcp_only的task，数据源列显示"mcp"
2. 切换到传统模式 → 任务列表恢复旧task，数据源列显示旧源
3. MCP模式下点"增量拉取" → daemon用mcp_only配置启动，从MCP取数
4. **首次启动MCP模式（quantstudio.db不存在或空）→ 不崩**，水位列显示"无"（优雅降级）
5. MCP模式下reset_watermark → **只清source='mcp'的水位行**，xtquant/tushare水位行原样保留（P0安全）
6. MCP模式下数据源Tab → 显示mcp源卡片（endpoint/key提示）
7. daemon运行中切模式 → 冲突提示 + 禁止切换（过渡态）
8. **重启GUI → 自动恢复上次的模式**（持久化gui_state.json）
9. **首次启动（无gui_state.json）→ 默认MCP模式**
10. 缺MCP_API_KEY → **非模态InfoBar提示**，附"切换到传统模式"快捷操作（不用模态框每次打断）
11. 旧源配置冻结：切换不修改config/下任何文件
12. MCP模式下手动"全量/增量拉取"也写正式库quantstudio.db（LockedTaskWorker吃config_dir，采集与回测统一库）
13. **QFQ同库共存**：MCP模式daemon首轮运行后，具体判据：①daemon日志出现fail-closed关键字（无completed bootstrap→不写生产价格）；②data/qfq_aux.db无新增bootstrap记录（继承传统模式观察期状态）；③4张价格表水位行为不受破坏（不因切config-dir出现状态错乱）
14. **跨源幂等**：同一交易日先后用两种模式增量写stock_daily，逐列比对一致（含16列复权字段，upsert last-writer-wins不振荡）
15. **首次MCP增量实为全量回填**：正式库无source='mcp'水位行，首次增量从start_date起全量拉（stock_minutes百万行Parquet分片耗时较长），GUI提示或建议首次用"全量拉取"按钮
16. **staging/qfq_aux.db为孤儿**：data/staging/qfq_aux.db（P3验证staging期的QFQ状态）不迁移不删除，正式库用data/qfq_aux.db

---

## 4. 关键约束（P0安全）

- **reset_watermark绝对不能全表清空**：统一库后必须按source过滤（mcp模式只清source='mcp'，传统模式只清旧源），现有task_tab.py:678的无WHERE DELETE必须改
- **root_path绝对不能变成config/profiles**：root固定为项目根
- **空库/表不存在绝对不能崩**：DbHelper优雅降级（兜底首次启动或表未建场景）
- **daemon运行中绝对不能裸切换**：守卫+冲突检测
- **QFQ同库共存不能状态错乱**：统一库后两模式共享data/qfq_aux.db，MCP首轮继承传统模式观察期fail-closed语义

---

## 5. 治理

- 完成后汇报改动面+验证结果，等用户确认才同步GitHub
- 更新实时进度报告（如实记录：staging隔离策略废止→统一正式库的理由+QFQ同库验证证据）
- 不擅自stage/commit/push

---

## 6. 分支约定

在 `feat/gui-profile-switch` 独立分支开发，与前两轮一致。完成后汇报，等用户确认才合并+推送GitHub。
