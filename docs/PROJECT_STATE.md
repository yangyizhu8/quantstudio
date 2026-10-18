# PROJECT_STATE · QuantStudioHybrid

> 跨会话状态快照。新会话首动作：读本文件 + `docs/RUST_HYBRID_INITIATIVE.md` 恢复上下文。
> 更新纪律：每完成一个强制确认节点或迭代轮实施申报后立即刷新（簿记豁免，无需单独审批轮）。

## 当前阶段

**④确认待呈 / ⑤并回准备完毕**——R 终止令已归档（卷宗终止裁决）；并回包 `docs/MERGEBACK_MANIFEST.md` 已产出待呈总调度审 → 用户确认 → 另令并回（⑤总调度辖）。**副本分支终态锚 = 收尾 commit（本 commit）。**

## R 终止归档（2026-09-21 总调度裁定）

- **裁定**：路线 R（Rust 下沉）终止；触发 = 路线 P 判定门双口径越线（U2 真口径 2.50×/2.81×，B 段 80.67%→2.49%）触发审核要点原文条款"P 达 ≥2× → Rust 下沉性能理由即消失，须重新呈裁"；决策链 = 审核席建议（三理由）→ 总调度裁定
- **ISS-003 转写**：装载扫描残余转数据层 SQL 预筛新题候选，入主仓 pending 池（并回后生效）
- **卷宗**：变更日志终止裁决条目（四要素齐）+ §8 状态板终态（③完成·两段/R 终止/当前④⑤）
- **项目最终成果**：分钟回测 U2 真口径 **2.50×（门禁）/2.81×（三跑中位 173.2s）**，引擎侧 Python 优化达成（M1 聚合 + M2 向量化），零 Rust 代码——立项令"Rust 混合改造"经实测收敛为"实测驱动的 Python 纯优化"，全程六步流水线 + 逐位门纪律闭环

**测量口径勘误（本轮自我检出的方法学缺陷）**：`runner_minute_clean.py`（探针等价重写件）sys.path 指向主仓——M1 轮"净口径三跑中位 520.5s"与 M2 轮首轮两跑（451-462s）实为**主仓引擎**口径（主仓无 M1/M2 改动，即基线行为）。**已纠正**：M2 后三跑改用 `--worker U2`（副本引擎）173.2/168.8/191.4s；M1"甲收益 ≈0"结论不受影响（其证据为 gate 两态对=副本代码正确口径）；主仓口径两跑恰构成意外基线参照（451-462s ≈ 基线，与门禁 A 487.4s 同域互证）。

## 已锁定基线

- 副本：`C:\QuantStudioHybrid`（真浅克隆 --depth 1 --no-local --single-branch）
- 基线提交：main@366596d；当前 HEAD **`e63dab7`**（M1 commit；前序：02b073f merge origin/main / bb80115 M0 / 72e8c6e v1.1 / 29b2dbb 交接）
- git 身份：yangyizhu8（副本 local config）
- 权威立项文件：`docs/RUST_HYBRID_INITIATIVE.md`（含 2026-09-20 勘误登记）
- **方案件：`docs/RUST_HYBRID_DESIGN.md` v1.3（已入库）**
- **M0 工件：`scripts/double_run_engine.py`（双通道零容差比对器）+ `docs/evidence/double_run/` 样例**
- **M1 改动：`duckdb_data_access.py` 单码回看式聚合（wrapper/_impl 拆分，+64 行）**
- **Phase 0 证据：`docs/evidence/phase0_hotspots.md`（含 943s 归因勘误）；原始剖析产物 `agent_workspace/phase0/`（.gitignore 已隔离）**

## ②审归档（2026-09-20 总调度审通过）

- **裁定 1 · universe 口径声明**：方案件 v1.2 三处加注（§0 前提判定 / §4.2 场景 1 表头 / evidence §2 交叉跑节）；§4.4 Amdahl 表加注"场景 1 为亏损 universe 口径，满 universe 先验参照=场景 2"；**v1.3 定谳升版：成因=影子库陈旧 + 基线族系统性低估声明 + 唯一有效口径=U2 族**
- **裁定 2 · M2 性能门禁换窗**：性能复测与 ≥2× 验收以 2026-07+ 有数窗口为准（U2 窗口 07-15~17 直接任性能基准用例，或提名新满 universe 分钟用例呈批）；U1 保留为行为回归用例，卸任性能门禁口径
- **裁定 3 · 段一后复测窗口锁定**：§1.2 实施路径 A 复测步骤明确"净口径复测须用 2026-07+ 满 universe 窗口，Amdahl 与 2× 在满 universe 口径下复证"（②审硬条件原文落入）
- **随附**：ISS-004 定谳更新（影子副本陈旧，补救归数据拉取线）；M0 审重点四项预置（自校验运行证据 / 零容差落地验证 / 段门同步记录 / v1.2 落位核对）

## Phase 0 核心结论（详见方案件 §4；v1.3 定谳后口径）

- 锚点复现 PASS：853.25s vs 797.33s（+7.01% ≤10%）
- 四场景可下沉合计 96.14% / 99.54% / 98.03% → **前提存活**（②审注：场景 1 为亏损 universe 口径，满 universe 先验参照=场景 2）
- 迁移顺序实测重排：查询面批量化（C-api 17~86%）→ 每 bar 循环（B 8~80%）→ 撮合账户留 Python（裁定②，实测 D 0.00%）
- **路线已裁定（2026-09-20 用户 + ②审）**：分阶段——M0 对比器 → 段一甲（Python 批量化）先行 → 满 universe 复测复证 → 乙增量决策；丙实测排除（1.09×）
- 成本模型：T(D日) = 670.4s 日1装载 + 94.06s/日（8 点校验闭合）

## 已完成里程碑

1. 2026-09-20 前置 C：副本构建 + 交接三工件（29b2dbb）
2. 2026-09-20 总调度裁定落档（不上 GitHub / 段门同步）
3. 2026-09-20 段门同步执行（fetch+merge，零漂移）+ 008 行号全量核对一致
4. 2026-09-20 Phase 0 全矩阵：冒烟 → A-0a/b/c（探针/净口径/归因双工具）→ SNAP003 交叉（结构性失败归因：3 月分钟数据 BR 独有）→ 三轮有成交试跑（全零成交）→ 扩展窗/overnight 3d/日线对照；双库写遏制 PASS
5. 2026-09-20 方案子 v1.0-draft 落盘 + 卷宗勘误登记 + 证据文档 + .gitignore 两行 + ISS-003 登记/归因落定
6. 2026-09-20 用户验收（四裁定：分阶段路线/段三留 Python/overnight 终局/commit）+ 四修订（R1-R4）→ v1.1 commit **72e8c6e**
7. 2026-09-20 **②审通过（附硬条件：universe 口径声明/M2 换窗/段一复测窗口锁定）** → ③实施 M0 轮启动；ISS-004 量级更新+台账关闭追踪
8. 2026-09-20 **M0 完成**（bb80115）：对比器落地 + U1-U5 全 PASS + 四谱负样本 + v1.2 勘误
9. 2026-09-20 **M1 段一完成（e63dab7）**：单码回看式聚合落地；逐位门五 PASS；甲收益 ≈0（gate 两态对为据）。**【测量口径勘误补注 2026-09-21（M2 自检）】M1 轮"三跑中位 520.5s"系主仓引擎口径（runner sys.path 陷阱），M1 结论以 gate 两态对为据不受影响**
10. 2026-09-20/21 **M2 段二路线 P 完成（ebecd96）**：构价向量化 + 映射缓存；逐位门五 PASS（U2 2.50×）；U2 真口径判定门双口径越线（2.50×/2.81×=173.2s 三跑中位）；后段二 B 80.67%→2.49%；回归 102 passed + 契约门 PASS；测量口径陷阱自检自纠
11. 2026-09-21 **R 终止归档 + 收尾轮（本 commit）**：ISS-003 转写（SQL 预筛新题入主仓 pending 池）；卷宗终止裁决（四要素 + §8 状态板终态）；MERGEBACK_MANIFEST.md 产出（逐文件清单 + gate 转正建议呈裁 + 并回后验证建议）；段门同步第 4 次（ddaf083，AGENTS.md）——**副本分支最终态**

## 未闭合 Issue 清单

见 `docs/ISSUES.md`：ISS-001（iterrows→段二候选）、ISS-002（DataDict contains，优先级降）、ISS-003（get_history 查询链→段一首选，86.35% 实证）——候选未立项，随 M1 立项；ISS-004 已台账关闭（处置归总调度线）。

## 下一步待确认事项

1. **M2 段二轮 Plan 呈批中**（路线 P 先行：Python 向量化构价 dict(zip)+映射缓存，ISS-001 转正主内容；P 后 U2 <2× 才启动 R 评估——分阶段裁定延续，不许跳步）
2. M2 完成申报 → 用户验收 → R 评估决策（P 达 2× 则 Rust 下沉仅剩架构理由须重裁）
3. 规范指令台账：申报行号对最终入库版本（e63dab7 后行号基准）

## 当前有效迭代范围

③实施 M0 轮（双跑对比器基建）。产码范围=scripts/double_run_engine.py（旁路工件，零引擎代码改动）；.rs 仍在 M1 复测决策之后。

## 基线差异备忘

主仓工作树在 366596d 之上有 395 个未提交文件——副本不含；M0 轮首动作=段门同步（fetch+merge，铁律 9）；工作树现存簿记改动（PROJECT_STATE ②审归档 + ISS-004 量级更新）随 M0 轮产物一并 commit。

## 2026-10-10 M5.1b 状态快照（⑤确认已获，⑥挂起）

- 当前阶段：M5 批2 陪同执行——M5.1b（D-1 count 形态未来函数 + D-9 Panel 解析）③④⑤ 完成。
- ⑤确认（用户裁定，总调度转达）：④验收采信（27 单测+6 桩冒烟+矩阵零漂移+六产物重转+文件7 byte-identical+审计三要点落证）；复测指引确认已转用户。
- ⑥推送裁定：**复测通过后并入下批推送批统出呈批**（不单独批复，批次惯例）——当前挂起，等待文件7 用户域复测日志回传（判据：FUN 行窗口首/尾逐bar变化、E1 端点定谳、无 FETCH_FAIL/UNDETERMINED 刷屏）。
- 门禁落位：主仓 AGENTS.md ✅ / 副本跟随 ✅ / 全局预设模板 ✅ / 方案文档留痕示范 ✅ / **退回演练留证＝下轮待办（已照准）**。
- 回退基线：output/backups/m5.1b-20261010/（三 .bak + deployed-file7-m51a.py.bak）；git stash 基线沿用 M5.1a。
- 未闭合：D-2b（BLOCKED 暂缓，解除条件=首个持仓交易链复测）、stub 保真度第二批、M5-11（BLOCKED D-2）、退回演练留证、六产物中其余五产物部署（等文件7 复测通过后建文件8-12）。
- 下一步：等待复测日志 → 判定 PASS/差异归因 → 并入下批推送批统出呈批。

## 2026-10-11 02:00 M5 批2 状态快照（D-12 final 通过，F=0 新瓶颈待定位）

- D-12 final 验收通过：01:48 复测 A=5227（原 0）→ 单参 get_stock_list_in_sector 唯一可用形态（PROBE 定谳：realtime 实参任何值回测模式均空）；33 passed / 矩阵零漂移 / 文件7 sha 77f83acc76ffb152。证据 §8.1。
- M 闸门 m=1 全程稳定（D-1/D-9 持续有效）。
- **新瓶颈（D-13 候选，未定谳）**：FUN 漏斗 A=5227 F=0——filter_stock_by_status 把 5227 全剔。
  - 已核：_qs_filter_stock_by_status 实现（source_import_qmt.py:951-979）语义正确（剔除命中/保守保留+UNDETERMINED 登记行）；
  - **未读**：_qs_status_flags（约 :850-908）——ok/flags 提取逻辑；F=0 且无 UNDETERMINED 刷屏 → 每只 ok=True 且三 flag 恒命中，或循环未执行即空转（下轮首选：读 _qs_status_flags 全文 + 核对产物 :1311 调用实参）；
  - 定位后修复走 D-12 同款微批 profile（PROBE 取证→修 wrapper→33+ 门禁→重转→部署文件7→复测）。
- 直达闭环授权仍有效；下一步 = D-13 定位修复 → 五产物部署（文件8-12）→ 下批推送批呈批（M5.1a+M5.1b+D-12+D-13 合并）。
- 待办不变：退回演练留证、D-2b（BLOCKED）、Obsidian 回灌（count 形态未来函数/Panel shape/realtime 参数回测语义三条候选，结案批统一做）。

## 2026-10-11 03:1x M5 批2 状态快照（D-13 修复完成，待文件7 复测）

- **D-13 定谳并修复**：PROBE（02:31，文件8）实锤 `ExpireDate=99999999 type=int`（YYYYMMDD 永续，OpenDate 同形态佐证）→ 旧代码按毫秒 /1000 得 1970-01-02 → now()>1970 恒真 → 5227 全判退市（F=0 根因）。修复：wrapper `_qs_status_flags` 数值分流（>=1e10 毫秒防御 / 否则 strptime YYYYMMDD，非法月日 ValueError 保守不过期）；ST/HALT 路径零改动。判型=数据修正型（影响面=DELISTING 行集）。
- **门禁**：38 passed（+5 D-13 测试）+ 矩阵零漂移（中途白名单拦 `_qs_dte(...)` 直接名调用一次——改 strptime 属性形态后绿，校验器未放宽）。
- **六产物重转+部署**：文件7 69067B sha=17af261996c02af6 byte-identical；五产物 output/qmt_import/ 待部署文件8-12。
- **D-12 关单**：01:48 复测 A=5227 实证（ISSUES 已更新）。
- **回退链**：stash 1498d3d9（baseline-before-d13）+ .bak-d13-pre 两件 + deployed-file7-d12.py.bak。
- **下一步**：等文件7 复测日志（预期 F≈5000+ → H/q/b → selected>0 → 买卖链）→ 五产物部署（用户建文件8-12）→ 下批推送批呈批（M5.1a+M5.1b+D-12+D-13 合并）。
- 待办不变：退回演练留证、D-2b（BLOCKED，解除条件=首个持仓交易链复测——本轮复测若出持仓即触发重评）、Obsidian 回灌四条候选（D-1/D-9/D-12/D-13）。

### 快照 2026-10-11 03:1x（D-16a 实施完成，待部署裁定+复测）
- 当前阶段：M5 批2 微批推进中（M5.1a ⑤done/⑥pending；M5.1b ③④⑤done/⑥pending）。
- D-13 复测通过（F=4414 实证）；D-14 吞吐降级非阻断（缓存生效）；D-15/D-16 双探针定谳 volume 单位=手（04-数据结构.md:24），q=0 根因=D-16a 单位错位（低估 100 倍）。
- D-16a 已实施：wrapper 双层 volume×100（:435-437/:581-592）；pytest 36 passed；六产品重转 +942B 同构（CANSLIM 70009B cf2feefe0f906550）。
- **待用户裁定**：file7/file8 被 QMT 客户端回测后自动改写为 token 加密形态（新发现）——D-16a 部署落点（新文件名 or 覆盖）。
- D-16b（批竞态）证伪关闭。回滚链：stash 1498d3d9 + bak-d13-pre ×2 + deployed-file7-d12.py.bak + 本地产物在位。
- 推送批（M5.1a+M5.1b+D-12+D-13+D-16a）待 file7 复测通过后呈批；矩阵 reverify 随推送批同 commit（wrapper 模板已改）。

### 快照 2026-10-11 03:4x（D-17 修复完成，待复测）
- D-16a 复测 q=0 恒定 → 深挖定谳 **D-17**：get_history 纯关键字形态 field= 路由洞（high/volume 根本未请求）；D-16a 换算逻辑本身正确。
- D-17 已修复（get_history 适配层无条件 field→fields 归一）+ pytest 39 passed + stub 冒烟 6 + 六产品重转（CANSLIM 70484B 673cb230564a24be）+ 部署 QMT转换测试.py。
- 待用户复测：FUN q 预期打开（~4300+）；q>0 后 b/selected/tradable 链条依次验证。
- 推送批（M5.1a+M5.1b+D-12+D-13+D-16a+D-17）待复测通过呈批；矩阵 reverify 同 commit。
- 主库回灌已提交 b25cc62（QuantStudio QMT管线两件+MOC 索引）。

### 快照 2026-10-11 03:5x（漏斗全通里程碑 + D-18/D-19 待授权）
- **里程碑**：M5 批2 漏斗 A→F→H→q→b→selected 全通（03:24 复测 q=4135+/b 非零/selected=5）——D-13+D-16a+D-17 三层修复链闭环。
- 新登记：D-18（P0 回测资金源恒 0，buy 链断）/ D-19（P1 fundamentals NaT）——均待用户授权推进（探针→修复→重转→复测）。
- 推送批（M5.1a+M5.1b+D-12+D-13+D-16a+D-17）待 buy 链打通后一并呈批更完整；矩阵 reverify 同 commit。
- 主库已提交 b25cc62；回灌候选累积 D-1/D-9/D-12/D-13/D-16a/D-17。

### 快照 2026-10-11 16:0x（D-18 修复落位待复测）
- D-19（fundamentals 毫秒直通）+ D-18（回测资金源/交易转发四处）模板修复完成；门禁 46+6+66 全绿；六产品重转全 PASS（CANSLIM 73072B 337b1c32 已部署 QMT转换测试.py）；矩阵 reverify 7a424f397983 同批追认。
- 待用户 QMT back test 复测：验收判据 cash_ratio 非零 / buy_submitted>0 / 无 NaT 报错 / EPS 因子恢复。
- 推送批（M5.1a+M5.1b+D-12+D-13+D-16a+D-17+D-18+D-19）待复测通过后一并呈批。

### 快照 2026-10-11 16:2x（D-18 关闭里程碑 + D-19 残余待授权）
- **里程碑**：QMT 回测主链全通（16:05 复测九个月 0105→1009 无崩溃）：买卖/止损/调仓/持仓管理全活——D-13+D-16a+D-17+D-18 四层修复链闭环。
- D-19 残余（NaT 仍报）：第二层根因新定谳=wrapper 单行形态 vs 本地多行历史契约错位 + NaN 行未剔除（登记 ISSUES）。
- D-20（P3 显示层 gross_exposure 恒 0）登记。
- 推送批待 D-19 残余处置后一并呈批（或用户裁定接受现状先呈批）。

### 快照 2026-10-11 16:4x（D-19 二层修复部署完成）
- D-18 已关闭（16:05 复测四判据全过，回测主链全通）。
- D-19 二层修复已部署：历史表多行契约 + 完整行过滤（49 测试全绿 / 六策略 portability 全 PASS / CANSLIM sha16=522db759 已部署 QMT转换测试.py）。待用户复测：NaT 消失 + EPS 因子恢复。
- 基线 stash：e40e3ba1（D-19 二层写前快照）。
- 推送批（M5.1a+M5.1b+D-12+D-13+D-16a+D-17+D-18+D-19）待 D-19 复测过后一并呈批。

### 快照 2026-10-11 16:5x（D-21 编码契约修复 + D-19 二层重新部署）
- D-21：临时脚本 UTF-8 落盘违反 GBK 产物契约 → scripts/qmt_reconvert_six.py canonical 重转门固化（四自检）；六策略 GBK 重转全 PASS。
- CANSLIM（D-19 二层 + D-21 编码修复）76540B sha16=23f79b1a 已部署 QMT转换测试.py，待复测（无 NaT / EPS 因子恢复 / 漏斗维持 + 无编码错误）。
- canonical 重转入口此后固定为 scripts/qmt_reconvert_six.py，禁止手搓临时重转脚本。

### 快照 2026-10-11 17:1x（17:00 复测 ①②④ PASS + QSFUNDHIST 审计行）
- 17:00 复测：编码/NaT/漏斗三判据 PASS，回测全区间 183 轮无崩溃；③ EPS 因子恢复受 composite skipna 归一影响不可从选股结果区分。
- 已部署 QSFUNDHIST 审计行版 CANSLIM（sha16=fc8e76d1），复测 kept>0 即 D-19/D-21 全关。
- 门禁实录：⟺ 注释被 GBK fail-closed 测试拦截——D-21 修复后写盘点门禁首次实战拦截。

### 快照 2026-10-11 17:4x（D-22 endDate 空串修复部署）
- 17:31 复测：QSFUNDHIST cc=0 定谳数据源为空（D-19 二层消灭 NaN 契约行为真，数据空是独立缺陷 D-22）。
- D-22 修复：endDate 缺省补 20991231（文档依据：innerApi/data_function.html 必填参数），审计行增 raw 形态；CANSLIM sha16=330e16e5 部署待复测。
- 快照链：1498d3d9 -> e40e3ba1 -> e4be74ff -> 9f349c05。

### 快照 2026-10-11 17:5x（D-22 复测全过，四案全关，待推送批审批）
- 17:45 复测：QSFUNDHIST kept>0 恒成立 cc=n 全覆盖，raw=Panel 确认；判据③关闭（选股 diff 可归因：002908 出/603055 进）。
- D-18/D-19/D-21/D-22 四案全关。M5 batch2 累计：M5.1a+M5.1b+D-12+D-13+D-16a+D-17+D-18+D-19+D-21+D-22。
- 下一步：推送批呈批件（精确文件清单 + 快照链 1498d3d9/e40e3ba1/e4be74ff/9f349c05 + 双远程核验 + QuantStudio-trading 同步门），待用户批准。
- 可选优化（非缺陷）：get_fundamentals 窗口起点 19900101 可收窄（本地契约仅需 3 年年报，当前全历史窗口单轮 3-10s）。

### 快照 2026-10-11 21:4x（M5 batch2 推送批七步执行完成·终态）
- ①全库回归：61 failed/3461 passed 与 V1a 基线逐位一致（27:00，零新增失败）；②strategy_toolbox 交叉引用 +5 行；③commit ccbd97c（14 文件 +2090/-49）；④push origin 双远程（prepush-gate 裁定通道，rulings.log 在案）；⑤双远程 HEAD 逐位一致（21:23:17 fetch 核验）；⑥trading 同步门：merge 8f3e195（台账 union）+ 共享代码路径 diff=0 + ci-smoke 49/49 副本绿（drift FAIL 9 项=trading 合法本地增量已归因，sync-ledger 在案）；⑦Obsidian 回灌 3 条目 + D-9 条目补案例。
- M5 batch2 六步流水线全闭。D-18/D-19/D-21/D-22 四案关单。P3 队列（矩阵门 QMT 覆盖缺口升级、D-20 显示层、E1 端点、D-2b、M5-11、qmt_paste_utf8 清理）候下次排期。

### 快照 2026-10-12（M5.2 闭环基建批开工：件1/3/4/5 落地，总调度批复+加速令在案）
- 立项：docs/qmt-m5.2-closure-design.md v1.0 落盘（总调度批复全文+四审计要点+判型+验收 a-g+知识反哺留痕 4 命中）。
- 件1 矩阵门：check_fund_matrix.py SRC 扩 qmt 双哈希分立（meta.qmt_wrapper_hash=9b4892f7eeff 首录，PTrade 7a424f397983 隔离不变）；负向实证 FAIL+还原 PASS；tests/test_fund_matrix_qmt_gate.py 5 用例；AGENTS.md 矩阵铁律 QMT 扩充电。
- 件3 收敛台账：docs/evidence/qmt-pipeline-convergence.md（batch1 N=11/R=0 + batch2 N=11/R=0 回填，R>0 处置预案在案）。
- 件4 部署器：内核 quantstudio/strategy_compiler/qmt_deploy.py（三级目录解析+SHA256 双端核对+manifest+幂等覆盖+CJK 文件名保留）+ CLI scripts/qmt_deploy.py 层1（--all/--strategy/--file/--list/--qmt-path --save）+ 层2 钩子（orchestrator --deploy-qmt：产物落盘自动部署，run_card artifacts 带 deployed 条目）；tests/test_qmt_deploy.py 10 用例（含 canonical 六名单一致性守护）；六产物实测部署 D:/QMT/国金QMT交易端模拟/python 全 verified（层2 端到端 sha=b945cb56542b521d 与目录实际逐位一致）。
- 件5 目录配置化：config/qmt_deploy.json（deploy_dir 持久化，--save 实测写入 D:/QMT/国金QMT交易端模拟/python）；无自动探测（用户裁定）；PyQt 选择器归件6。
- 测试：新增 15 用例（5+10）+ 既有 49 用例零衰减 = 59 passed。
- 事故 1 起自愈：手写清理清单与 target_filename 漂移误删 4 个正确部署文件→幂等重部署 <1min 恢复（manifest 18 条可追溯）；教训=清理清单必须由部署函数同名生成（候回灌 Obsidian）。
- 待续：件6 PyQt（QMT tab+口径选择器+CLI/PyQt 哈希一致门）、件2 对齐执行器、件7 双撮合口径（Context7 前置查询+双列证据链+口径标签全链路）；⑥推送随下批统出。
- 快照链：f44674d7（M5.2 pkg135 写前）。
### 快照 2026-10-12（M5.2 件6 落地：PyQt 双端对等——用户验收令+件6 继续令在案）
- 件6：qmt_export_tab.py 新建（独立 tab row10 界面现状裁定：平台转换均独立 tab 同构；FluentIcon.SYNC）；转换区（策略选择+deploy checkbox→QmtExportWorker 调 orchestrate_source target=qmt deploy_qmt 同内核）+ 部署目录设置区（件5 PyQt 落点：QFileDialog 手选→save_deploy_dir 持久化，无自动探测）+ 批量部署区（canonical 六产物 ListWidget 多选→deploy_strategy 层1 内核）+ 结果区（artifacts sha/deployed 标记/warnings）；main_window 注册 +4 行（_NAV_ITEMS 第 11 项+工厂 row10）。
- 双端一致性门禁：tests/test_qmt_export_parity.py 3 用例（内核确定性两次转换 sha 逐位一致+薄壳纯透传静态断言+导航注册检查）——单一内核架构下测内核一次=证两端。
- 回测 tab 撮合口径选择器：依赖件7 match_profile 参数，随件7 落地后补（依赖序自然结果）。
- 测试：62 passed（59+3）零衰减。事故教训（清理清单禁手写）候 Obsidian 回灌已获准。
- 待续：件2 对齐执行器 ∥ 件7 双撮合口径（件7 实施前复核权保留——参数定谳件呈总调度）。
### 快照 2026-10-12（M5.2 件7 引擎落地：总调度三裁定在案——A/B 入库+C 采(a)+节奏确认）
- 件7：backtest_engine.py match_profile 参数（ptrade|qmt；缺省 ptrade 零行为改变）+ qmt 口径 logger 标签快照 + metrics_summary 注入 match_profile/match_price_mode 两键（批复③全链路可追溯审计点：GUI/CLI/run_card/对照报告统一消费位）；backtest_tab Match profile 选择器三处落位（combo 构建/取值/params dict）+ workers.py 透传（GUI→引擎链闭合）。
- 关键发现：引擎 TradeCost 已对齐市场真实费率（印花税 2023-08-28 减半分段 :864-868、过户费万0.1、佣金万3.5）——B 类参数引擎现行已具，件7 实质=口径标签体系+参数快照（C 类候反推）。
- 定谳呈报件：docs/evidence/qmt-match-profile-params-20261012.md（六参数双列证据链；A 类 prType=5/枚举表/wrapper 三方交叉，B 类央视网/浙商公示来源，C 类批复②a 反推路径）。
- 测试：撮合相关 24 用例零衰减 + test_match_profile.py 5 用例（参数校验/B 类值锚点/印花税分段/缺省零行为/标签契约）+ M5.2 四件套 18 passed 全绿。
- CLI 回测入口 match_profile 透传：候下轮（GUI 链已闭合，CLI 壳 BacktestEngine 调用点透传一行待补+测试）。件2 对齐执行器：未启动（context 预算），候下轮 zcode 委派任务包（总调度已照准）。
### 快照 2026-10-12（M5.2 件2/件7 残项全闭：继续令三项完成）
- ①CLI 透传：run_ptrade_strategy.py run_backtest 增 match_profile='ptrade' 缺省+透传（CLI/GUI 共用入口），test_match_profile 增透传链静态断言=6 用例绿。
- ②件2 对齐执行器（E7 委派）：任务包 docs/zcode-taskpkg-m52-item2.md 落盘→zcode_code 交付两新文件（634+290 行）→O5 验收五步过环（17 passed+--help 契约+diff 零越界+ISSUES.md E7 登记）。纯观测铁律：脚本零引擎 import（AST 机检）。
- 真实 CANSLIM 对照（判据 b）：候用户 QMT 端复测日志到位后跑执行器出报告（验收 g 不阻塞）。
- M5.2 七件套全落地：件1/3/4/5/6/7+件2。全批候⑤用户确认→⑥下批统推（61/3461 全库大回归随推送批）。
### 快照 2026-10-12（QMT 端策略落地机制定谳 · 剪贴板直通路线）
- **重大定谳**：QMT 内置 Python 策略为**加密存储**（Base64 形态，体积=明文×1.3333）+ 策略名大写规范化；**明文 .py 拷入 python\ 目录对 QMT 模型树无效**（树条目由 QMT 内存态权威，外部改 indexUserConfig.xml 被 QMT 保存时回写覆盖）。
- **落地路线改定**：GBK 解码读产物→剪贴板→QMT 编辑器粘贴保存（QMT 自行加密落盘）。六份全部落地（10:18-11:10），比值逐份 1.3333 一致=粘贴内容与产物原文一致的证据链。
- **件4 部署器设计前提被证伪**（验收判据 d 失效）——「拷明文 .py 即部署」不成立，须重新设计（粘贴文本生成+界面引导，或探索 QMT 导入通道）；属框架层改动，回退六步流水线方案环节，候呈批。
- 备份/归档：`config\indexUserConfig.xml.bak-20261012-m52`（127537B）；`python\_qs_plaintext_bak\`（5 份明文，fall_reversal 明文被 QMT 覆盖）；`新建策略文件6.py` 残留已清理（用户授权）。
- 待办：六策略 QMT 端回测运行（判据 b/g 数据源）+ 件4 部署器重设计呈批。
### 快照 2026-10-12（M5.2 件8 · D-23 QMT `get_stock_info` 日期契约键缺失修复：实施+验收完成）
- **根因定谳**（QMT 端实测实证）：`QMT_WEEKLY_SMALLCAP` 零交易 = L1 全滤（`L0_all=5227 → L1_listed=0`）。链：策略 L1 读 `listed_date`（`weekly_smallcap...:180`）→ wrapper `get_stock_info` 按 `field` 白名单取键（`source_import_qmt.py:1531-1533`）→ `_qs_get_stock_info` 返回字典**无 `listed_date` 键**（只有 QMT 原生 `start_date`/`end_date` 等）→ 恒 None → `continue` 全滤。本地契约（`ptrade_api.py:2481-2523`）listed_date = `'YYYY-MM-DD'` 字符串。
- **消费面取证**：12 策略 / 54 处消费点**全部只用 `listed_date`**（个别带 `list_date` fallback）→ 修复面收敛为单键。
- **修复**（框架层，策略源码零改动）：`_QS_QMT_STOCK_INFO_EXT` 内新增 `_qs_norm_ymd` 归一 helper（int/str YYYYMMDD → 'YYYY-MM-DD'；0/负/None/bool/非 8 位/非法月日含 99999999 → None，与本地「取不到」同判）+ `_qs_get_stock_info` 增 `listed_date`/`de_listed_date` 两契约键，**既有 9 键零改动**。
- **判型=数据修正型**：diff `1 file changed, 42 insertions(+)` 零删除；六份产物统一 **+2007B** （无其他漂移）→ diff 完全源于归一化行集。
- **验收证据**（`docs/evidence/qmt-l1-listed-date-norm-20261012.md`）：新增单测 `tests/test_qmt_stock_info_date_norm.py` **24 passed**（含边界矩阵 18 例 + L1 阻断回归闸 + 既有键零回归断言）；既有 `test_source_import_qmt.py` **49 passed 零衰减**；QMT 族五套件 **100 passed**；六策略 canonical 重转 **ALL 6 PASS (enc=gbk)** + portability/selfcheck 全 PASS；矩阵 `--check --reverify` → `qmt_wrapper_hash=c45941bf5399`（同批追认）+ `--check` 绿。
- **写前回退点**：`b248385`（`baseline-20261012-qmt-l1-normalize` @15:17:37，四步配方）+ 文件级定向备份 `docs/evidence/_bak/source_import_qmt.py.bak-20261012-qmt-l1`（SHA256 `C60FEADF…`）。**附注**：首跑快照因工作区含 `agent_workspace/_wt_*` 嵌入仓库致 `git add -A` 超时被 kill 并残留 `.git/index.lock`（0B）；核实无 git 进程后清理，属本轮阻断性清理（局部豁免申报）。
- **零越权登记**：wrapper 其余键名（`stock_name`/`stock_type`/`exchange_type`/`code`）与本地契约差异，策略侧零消费，本轮未改 → `docs/ISSUES.md` P2 open 候批。
- **当前阶段**：④验收完成 → **⑤用户端测/确认**（GBK 产物经剪贴板粘入 QMT 复测，主判据 `L1_listed > 0` 且产生交易）→ ⑥推送随统出批。
- **未闭合**：D-23 端测（用户端）；件4 部署器重设计（候呈批）；键名差异补全（候裁定）；`python\_qs_plaintext_bak\` 与 `indexUserConfig.xml.bak-20261012-m52` 清理（候答复）。
### 快照 2026-10-12（D-24 进度审计行：③实施+④验收完成）
- **判型＝新增检测型**。五落点：`_QS_QMT_LOG_EXT` helper（`_qs_progress_start`/`_qs_progress`，local import time）+ `filter_stock_by_status`/`get_stock_info`/per-code 三处 tick + `get_financial_data` begin/end（`finally` 配对 + `err=` 尾键）。
- **验收全绿**：新单测 15 passed（判据 a/b/h/i，含 vs 改动前副本 `bak-20261012-d24` 的真黄金对照）+ 既有 49 用例零衰减 + 六策略 `ALL 6 PASS (enc=gbk)` + 产物**统一 +2969B** + `qmt_wrapper_hash=5c3586666747`（yaml+md 同批追认，`--check` 绿）。
- **实施中自测发现**：t0=0.0 → `elapsed=1791806094.2s`（epoch 天文数字，误导判读）→ 按局部豁免加固（`t0 > 0` 才计时）；契约描述澄清 `done=<n>/<total>` 为复合字段。
- **回退**：stash `cc8c087`（`baseline-20261012-d24-progress` 19:48:33）+ 文件级备份 SHA256 `694C45D9…`。
- **未闭合**：端测（D-23+D-24 并轮，候用户）；**正式 archify 影响面图**（本会话 context 不足，如实申报，候下轮）；D-25 另起一轮；wrapper 键名差异候裁定。
### 快照 2026-10-12（D-23/D-24/D-26 三卷端测贯通 · QMT 端首次产生交易）
- **里程碑**：QMT 端 `QMT_WEEKLY_SMALLCAP` 全区间回测**首次贯通并产生交易**——`QS_FUNNEL_AUDIT L0=5227 → L1_listed=4410 → L2=3816 → L3=3666 → L3v_complete=3663 → L4=366 → L5=30 → L6=17 → R_rankable=16 → R_selected=10 note=ok`；`buy_submitted=7`、持仓 7 只、总资产 1,003,261.49。
- **三卷达成**：D-23（listed_date 契约键，L1 由 0 → 4410）；D-24（取数层进度审计行，全程无静默段）；D-26（hist 段 509s → 127/109s，4×）。
- **D-26 卷四轮**：D-26（批量+去 iterrows）→ D-26b（回落修 `kept=0`）→ D-26c（结构探针：`to_frame()` index=(major=日期,minor=字段) × columns=code，`.loc` 方向错；契约超量 200×）→ D-26d（`dropna(how='all')`，纯性能型）。
- **教训固化**：self-made mock Panel 无法代替真实结构——D-26 本地黄金对照全绿、真实环境却破（`kept=0`）；真实结构以端测探针定谳。
- **验证链**：70 passed 零衰减 + 六策略 ALL 6 PASS + `qmt_wrapper_hash=75108c301bf3`；回退点链 `9912f07→2266e36→bdd7efb→b23e724→26cf345`。
- **阶段**：④验收完成 → **⑤用户确认**（呈批中）→ ⑥双仓推送（待用户点头）。
- **未闭合**：archify 影响面图；D-27（m_anntime 过滤 / query_last_close 批量化 / 消费面 mask 审计）；wrapper 键名差异（P2）。
### 快照 2026-10-13（D-27a 自保护版：收敛未生效但正确性零漂移 · 交接新会话）
- **D-27a**：`_qs_fin_to_rows_hist` 新增「`m_anntime` 收敛 + **自保护**」（仅当被删行 value 全为 NaN 才收敛，否则退回 `dropna(how='all')`）。端测（00:03–00:12）：`QSFUNDHIST kept` 未变（9,718,706 / 9,692,826）；`R_selected=10` 保持 → **正确性零漂移**（纯性能型判据满足）。单周区间耗时 572s。**S3 更正（2026-10-13）**：`kept 未变 = 无行被删（m_anntime 全非空或全 NaN 二义），未获收敛效果`——原「QMT 对 m_anntime 亦做日频填充 → 不可作收敛键」为证据不足断言，已废（方案 §1.3）。
- **消费面审计（D-27 第 3 项）完成**：四大主策略全部显式 mask NaN——`weekly_smallcap :149/:158`、`周频三层 :145`、`低流动性 :121`、`CANSLIM :394/:490` → **D-26d 纯性能型判型成立、无残留口径风险**。
- **git**：`e269fc8`（三卷，15 项精确清单）**已提交未 push**；D-27a 改动未提交；`main...origin/main [ahead 11]`（用户裁定 B：并入明晨批统出批）。
- **交接**：`docs/handoff/handoff-20261013-d27-resume.md`（新会话首读）；回退点 `da21e0d`；`qmt_wrapper_hash=a3719f737dfb`（`--check` 绿）。
- **明晨轮待办（按序）**：① archify 影响面图（预检已过 → 走 Edge）② D-27b 探针（`m_anntime`/`m_timetag` 唯一值分布 → 定收敛键）③ `query_last_close` 批量化（**先用 Context7 复核 `get_market_data_ex` 批量语义**）④ ⑥双仓推送收口（含 QuantStudio-trading 同步门）。

### 快照 2026-10-13（D-27b 实施验收完成 + M1 增补：B 探针 + C/D/E 分批 + asc 机检 · 候⑤用户端测确认）
- **明晨待办①②③全部完成**：① archify 影响面图已过四门禁+用户审阅（交付目录 `docs/evidence/archify-qmt-fetch-chain-20261013-005255/`，S1 用户目验 index.html 仍未闭合）；②③ 方案 v1.4（两轮 ZCode 审计：一轮 FAIL→修订、二轮 PASS+N1-N8）→ 写前快照（stash `00b0c5a3`，add 路径排除实测 17.5s）→ E7 委派实施（sess_51082fe4，两文件零越界）→ O5 五步全过。
- **M1 增补（用户批复方案 A）**：判据 3 由人工改机检——`_qs_close_batch` 首调逐帧升序断言汇入 `CLOSE_EQC asc=1|0|-1`；门禁终态 **81 passed** / 六策略 ALL 6 PASS（weekly_smallcap `d298641ba3a0f4c8` 已入剪贴板）/ `qmt_wrapper_hash=92c05ce43085`。
- **验收证据**：`docs/evidence/qmt-d27-close-batch-20261013.md`（含 M1 增补节 + 验收期局部阻断修复 3 处申报 + M1 实施期过程申报——1 次编辑事故当轮修复净零 + e4 两次测试侧迭代）。
- **判型落定**：B＝新增检测型（候端测 HISTDBG3 `uk` 定谳）；C/D/E＝纯性能型（候端测 `CLOSE_EQC diff=0 且 asc=1` + `L5=30/R_selected=10/buy_submitted=7` 逐位不变 + 耗时<572s）；A 折叠＝D-28 下轮（数据修正型，BLOCKED 候探针读数）。
- **M2 呈报（候裁）**：D-29 详情已呈（映射表 `_QS_QMT_FUND_FIELD_MAP:1581-1590` 三表缺 `profit_ability` → `:1600-1602` 早退空帧 → `:1643` hist 分支死代码；4 消费点均不在六策略门禁集 → **不阻断本批推送**，候用户裁定单独立项）。
- **未闭合**：D-28（候 HISTDBG3 读数三分支）；D-29（候裁定立项）；R1-B（archify 交付目录随本批 commit，候用户确认）；R1-C（双仓推送门禁，需新确认——携他线 ahead 提交）；S1（archify index.html 用户目验）。
- **git**：HEAD `e269fc8` + D-27a/D-27b 改动未提交（回退点 stash `00b0c5a3` + 文件备份 `_bak/source_import_qmt.py.bak-20261013-d27b`）；`ahead 11`（含他线）。
- **端测第一轮裁定（2026-10-13 用户批复三项）**：①判据 1 → **(a) 键可用，D-28 正式立项**（数据修正型；立项要件 4 项已登记 ISSUES：79 期更正 / drop_duplicates 组合键 / 三项证据含 SG-MS-PEG ROE 前后对照 / 226s→~10s；m_anntime ffill 之谜解开——单独不可作键与组合键可用同时成立）；②判据 3/5 → **(a) 盘后 A/B 黄金对照**（A=当前工作树批量版 vs B=stash `00b0c5a3` D-27a 态逐只版，背靠背同窗，L1/L2/L3/L5/L6/R_selected/buy_submitted/positions/total_value 逐位一致 + close 段 A≪B；任一不同→回退批量）；③D-29 → 暂不裁定候详情（本轮已补完整详情呈报）。证据链之①已按批复升级（L1/L2 纯本地也变＝输入数据源变了，最强证据）。纪律固化：**端测须盘后**（已回灌主库 + 项目记忆）。
- **下一步（按序）**：① 盘后（≥15:30）执行 A/B：detached worktree 从 `00b0c5a3` 提取 B 版 source_import_qmt.py 重转旧产物（不触主工作树），先 A 后 B 背靠背，读数并列进证据文档 → ② 定谳判据 3/5（逐位一致→R1-B/R1-C 呈报；不同→回退批量）→ ③ D-28 数据修正型方案起草（含 SG-MS-PEG ROE 均值前后对照设计）→ ④ D-29 候裁 → ⑤ R1-B 提交 + R1-C 推送（S1 archify 目验仍开放）。

### 快照 2026-10-16 晨（D-28 全链收口＋簿记预置＋D-33 立项：方案 v1.0 成稿＋registry QMT 维度落地 · 候总调度外审）

- **D-28 全链闭环（2026-10-15 用户裁定）**：端测盘后正式归档**验收通过**（判据 1/2/3＋四轮交叉一致性）；提交 `8be00aa`（24 文件 +6798/−38）；**簿记批预置**（3 路径 sha256 锚定＋教训 44 命令清单，`docs/handoff/d28-ledger-batch-preset-20261015.md`，候令执行）；R1-C 候批推。
- **D-33 立项（2026-10-16 02:31 用户三裁定＋08:51 Plan 照准＋三预裁）**：QMT 对齐闭环收口统一立项——B·对等双库（主仓 `knowledge/qmt/`〔已裁〕，schema 对齐 PD，`fix_layer` 五枚举〔已裁〕，**迁移自包含**〔trading 合并预案写死：未来回测部分并入 trading 时整体 mv 至 `vault/knowledge/qmt/` 与 ptrade-deploy 同仓对等〕）＋registry QMT 维度＋P1 台账回填（R 口径〔已裁〕：GBK 例计 R_i>0→先修管线）＋P2 回哺机器化（预检读取器＋GBK 写前拦截）＋P3 端测配额＋黄金 fixture＋P4 N 值呈裁（建议 6）。
- **本轮已执行（2026-10-16 晨）**：①方案 v1.0 成稿 `docs/qmt-alignment-loop-closure-design.md`（六件 A-F＋反哺留痕 8 条命中〔含「产物落盘编码契约」＝R 口径直接库证〕＋影响面 mermaid 降级载体＋分批实施计划＋三终裁附呈）；②**registry QMT 维度落地**：`knowledge/registry.md` 100→136 行——三端口径＋五节「QMT 锚点」列＋QLC/QMCH/QCAL 三专属面节（QCAL-01/03/05/06 已锚定、QCAL-02 已排查〔D-31〕、QCAL-04 已知分歧〔D-29 承接〕）；写前快照 `cd4a9b9d`；**机检回归退出码 0→0**；sha16 `98193e9f`→`9e47a4ac`。
- **三终裁落位（2026-10-16 09:35 用户）**：①**N=6 采信**；②**端测配额分级照准**（必端测三边界＝新 API 面首用/新数据面接入/撮合口径变更；行为不变类＝契约门＋黄金 fixture，D-28 四轮读数为首批黄金）；③**QD 首批＝P1 级 6 件先行照准**。方案升 **v1.1**（§十 裁定记录）。**②审通过（2026-10-16 10:33）＋批 1 放行**——两条件（仅约束批 2：GBK 钩子落点文件＋矩阵哈希同批追认／件 D BLOCK-WARN 边界先呈后落）已折入方案 **v1.2**；导读卡三自陈风险照准；**批 1 已完成（2026-10-16 10:4x，候⑤呈批）**：`knowledge/qmt/` 建库（README〔schema＋迁移节〕＋6 条 QD 条目 ↔ D-21/22/28/30/31/32）＋`knowledge/README.md` 导览入口；**schema 机检 SCHEMA_OK（6×8 必填）／QD↔registry 互链 5/5**；写前快照 `93a914e8`。导读卡：`docs/qmt-alignment-closure-review-card.md`。**QD-0006 枚举裁定（②审 2026-10-16）**：不扩枚举＋机械判定规则入册（fix_target 落点域⇒matrix/ledger/wrapper/portability_rule/probe）⇒ QD-0006 判 `matrix`（契约测试域），README 待确认项已销。**批 1 ⑤用户批准（2026-10-16 14:4x）**；**批 2 启动**：件 C 台账回填已实施（batch3 行 N=9／**R=1**〔GBK `≪` 复发→先修管线〕＋载体清单扩 `knowledge/qmt/` QD 库）；件 D 前置**已过审**（②审三裁定：B1–B3 BLOCK／B4 降 WARN 待证／W＋豁免照准）。**批 2 已完成（候⑤）**：件 C 台账回填＋件 D 读取器（`scripts/qmt_kb_preflight.py`）＋写前拦截强化（`orchestrator.py::_write_qmt_product`，B1 实效位置）＋**10 passed**＋**哈希未变**（不触 `_QMT_HASH_SCOPE`）＋**六策略零回归**；**R=1 处置闭环**。**批 3 已落**：件 E 端测配额手册（`docs/qmt-e2e-quota-manual.md`：三档配额＋黄金 fixture 规则＋执行 SOP）；件 F S4 台账 QMT 收敛段（`knowledge/alignment-convergence.md` §5：台账判据＋**N=6 终局判据**＋终点仪式＋逐轮登记）。**D-29 呈裁件已出**（`docs/d29-ruling-submission.md`，含文档查证：QMT 无 `profit_ability` 原生表，候选映射＝`PERSHAREINDEX`/`s_fa_roe`）＋**②审意见入卷（§七）**——**候用户裁定，裁定前不实施**。**批 3 验收通过 ⇒ D-33 六件 A–F 实施阶段全部完成**；批 2＋批 3 候⑤（专轮或直批）。**R1-C 统推完成（2026-10-16 21:2x）**：提交 `4764a0e`（26 文件）；prepush 门 PASS（逗号放行清单、未用 --no-verify）；**双远程逐位一致**（`4764a0e2caa…`）＋三方一致（0/0）；快照 `88c4bb88`；**叠加披露**：`orchestrator.py` 他线 `deploy_qmt` 未纳入；**远端 bypass 分支保护披露**；**trading 同步门受阻**（工作树 93 项脏，候四线清理同窗补跑）；**D-29 S2 探针 v1 端测＝无效读数**（五字段含无效字段全 `rows=2/nonnull=-1`；根因＝平台返回非简单 DataFrame，裸 len()/notna() 仪器错配；**结论作废不得引用**）；**v2 端测＝形态定谳（Panel）但形态不可区分**（无效字段同形同维；判别须读值）；**v3 端测＝内置拒判 INSTRUMENT_FAIL**（正对照亦 nonnull=0 ⇒ 本轮不入结论；三轮零误判）；根因为**假设**（v3 列名假设 vs wrapper 生产口径 `list(raw.items)`+`to_frame()` 按 code 取子，见 `source_import_qmt.py:1462`）候 v4 证实；**用户裁定停手**：v4 须据生产 Panel 取值分支重铸、一次到位，**v4 已出件**（`docs/probe/qmt_profit_ability_probe_v4-20261016.py`：生产口径复刻 `raw[_code]`＋自身列名取非键列；真实 pandas 离线自测 4 场景全过）**S2 定谳完成（23:48）**：正对照 9383 有效行／负对照 0 ⇒ 判据成立；**三候选全 0 ⇒ `NO_ROE_FIELD` ⇒ 路径①否证**；`ASHAREINCOME`/`ASHAREBALANCESHEET` 未测（合成未验）。**终裁＝裁②′（先探合成）**；**v4 候选集命名错误已更正**（QMT PERSHAREINDEX 比率类字段**无 `s_fa_` 前缀**：`du_return_on_equity`/`equity_roe`/`net_roe`/`total_roe`/`gross_profit`/`net_profit`，依据 Context7 xtdata 全字段表）⇒ **v4 `NO_ROE_FIELD` 结论作废**；**v5 端测定谳（00:21）＝FIELD_CONFIRMED 4 命中**（`du_return_on_equity` 9460/0.541/7.81、`net_roe` 9460/7.53、`net_profit` 9460/22.75；`equity_roe` 7891/**16.0 整数异常**排除；`total_roe`/`gross_profit` 无效）⇒ **QMT 有原生 ROE/净利率、无须合成**；**用户三裁**（①`roe`→`du_return_on_equity`／②`net_profit_margin`→`net_profit` 独立登记／③口径三件套＋阈值档）；**六步①方案 v1.0 已落盘**（`docs/qmt-profit-ability-mapping-design.md`）**②审通过 →③实施完成（01:5x）**：MAP 增一行＋单测 **7 passed**＋既有 49/53 passed 零衰减＋哈希同批追认（`6927447ea23c`）＋CONTRACT GATE PASS＋矩阵 qmt_rows 两行＋QD-2026-0007。**④判据 3 PASS（15:23 端测）**：`QSFUNDHIST kept=152`（改前 0）／`rows=152 nonnull=152`／`HISTFOLD after=79 intravar=0`；**判据 4 进行中**（两侧同为年内累计 % ROE 形态一致；**本地覆盖最早 2018Q1** vs QMT 79 期至 1990s ⇒ 仅 2018+ 可同期比对）；**④验收通过（15:45）**：判据 1/2/3/4/6/7 PASS；**判据 4＝口径一致（逐位一致）**（000001 11 期＋600000 10 期全部 IDENTICAL，无单位差/口径差）；判据 5 不适用（ROE 仅排序无阈值）；披露：本地覆盖 2018Q1+ vs QMT 1990s+（非口径差）、600000 缺 2024-03-30 期、`qmt-pa-02` 本地无列 A/B 不可比。证据 `docs/evidence/d29-acceptance.md`。**→ ⑤已入今晚呈批专轮（用户裁定：与出库首推等同批）**。**提交预置件已出**：`docs/handoff/d29-commit-preset-20261017.md`（22 项精确清单＋时点 sha256＋教训 44 命令＋写前快照＋三条失效条件；基线 HEAD `f4d9d80`，ahead=4 全为他线；我的 `4764a0e` 已在 origin/main）——**预置≠提交，候批令**。**R_i 复发候选**：GBK 编码契约族第三次在案（非编码字符＝`U+FFFD` 写盘往返损坏；探针件不经转换器写盘点＝载体缺口，候独立立项）。**D-29 红线不动**。**备件批已出（2026-10-17 17:0x，用户照准）**：批次设计件 `docs/qmt-shared-surface-probe-batch.md`——**A · lifecycle**（`docs/probe/qmt_lifecycle_probe-20261017.py`，2,386B）目标 QLC-01/04；**B1 · DAT 族**（`docs/probe/qmt_shared_surface_dat_probe-20261017.py`，3,976B）目标 DAT-01/02/04/05..15；两件 GBK 物理／无 BOM／`#coding:gbk`／compile OK；**B2 · POS 族**待建（wrapper 装配型）；**B3 · FEE 族**转**人工核对清单**（回测参数面板＝UI 面不可 API 探）；**B4 · MCH 族 10 格**不双建、**并入 QMCH probe-first 排期**。**端测请求**：A（2024-01-01~03-31）＋B1（2024-01-02~01-31），回抄 `[LC_PROBE]`／`[DAT_PROBE]` 全行。**同步门复核（独立取证）**：`4764a0e` **已在 trading HEAD 内** ⇒ 共享层确已同步 ✓（trading HEAD `daa30f5a`／主仓 origin/main `1b092649`；trading 工作树仍 4 项他线在途）。**QMCH 立项**：用户裁定**照准＋probe-first 排期**（先真机盘后撮合探针标定回填 4 格；完整六步排 10-20 呈审窗后，先保 10-19/10-22 两主节点）。**B2 备件验收 ✓＋排期裁定（二批·10-19 后择窗，交易端语境，非关键路径）**；**门读数归档缺口已闭合**（trading `999a5cf8`：drift 两读数＋smoke 105＋vault 归属读数，系 QMT 复核指出后由线调度补档）；A/B1 端测仍候用户（上轮步骤）。**⑤批准 → ⑥执行（2026-10-17 20:4x-20:5x）**：**提交完成 `7c2cf40`**（27 文件 +7473/−7；写前快照 `ad1ba32d`；精确 add 27 项 + 教训 44 暂存面核对 **27/27 全为我方**；`source_import_qmt.py` **+13/−0 纯增益**；污染扫描命中 1 处经查为**我方预置件中「描述他线提交」的文字**，非他线内容）。**推送受阻（门判据，非故障）**：`prepush-gate.ps1 -ReportOnly` 判定「**检出 8 笔非本件提交，且未提供裁定引用 ⇒ 拒绝推送**」——他线 8 笔：`3359789`／`301daa7`／`0f43718`／`1b09264`／`f4d9d80`／`47d6927`／`486882d`／`0ea425a`。**处置（候裁定，二择一）**：(a) **用户裁定的捆绑推送放行**（须 `QS_PUSH_BUNDLE_RULING=<裁定标识>` ＋ `-AllowAlso` **逐一覆盖 8 笔**；门强制留痕 `docs/handoff/push-bundle-rulings.log`）；(b) **候他线先自行推送**其 8 笔（各自过闸门）→ 本线单笔推送时清单仅剩本件 ⇒ 门自动 PASS。**纪律声明**：**未使用 `git push --no-verify`**（C7 叠加门照旧执行）；按 C7 教训「不得让他线未过闸门的提交随推泄漏」，本线**不自行断言捆绑放行**。**推送裁定（2026-10-17 21:2x）＝采 (b) ＋ 明晨专轮后统推整合**（裁定理由采信：他线 8 笔中 件3 `1b09264`/`f4d9d80` 的 ⑤未齐〔候明晨 -2+⑦ 补完〕、D1-补1/通知 `301daa7`/`3359789` 的 ⑤＝通知呈审未过 ⇒ **(a) 捆绑放行会把未过⑤的提交推出，门拦得正确**；本线「不自行断言捆绑放行」处置获采信）。**统推整合令**：明晨呈批专轮（件3 ⑤＋出库首推＋QMCH＋D-29〔已直批〕）＋通知审定齐后**统推一次**；执行人＝我方；裁定标识＝**专轮批准记录**（`QS_PUSH_BUNDLE_RULING` 引用之）；`-AllowAlso`＝**逗号拼接**（教训 24，禁空格分隔）；推后**双远程逐位核验＋回报**；随后**线调度**跑下一道同步门（ps1 口径＋读数归档新制）。**预置件已出**：`docs/handoff/unified-push-preset-20261018.md`（六节：裁定理由留档／执行前置 6 项齐备判据／一步执行序列 ①-⑦／失效条件 3 条／纪律与留痕／时点快照〔9 笔，**禁照抄**〕）。**`7c2cf40` 提交面验收 ✓**（快照 `ad1ba32d`／27 项 27/27 全为我方／纯增益 13/0／污染扫描 1 处＝描述性文字）。**A／B1 端测读数已到并完成对表（2026-10-18）**：证据件 `docs/evidence/qmt-probe-batch-ab-readings-20261018.md`。**registry 升 5 格**：QLC-01→**已锚定**（逐 bar 驱动、`barpos=1321+n` 恒等、`period='1d'`、`bar_timetag` 日期锚定 00:00 CST、**`C.stock_code` 不存在**）；QLC-04→**已排查**（`run_time` 接口存在，调度语义待细验，不以属性存在冒充已锚定）；DAT-01→**已锚定**（平台**无** `pctChg` 字段⇒须合成 `close/preClose-1`，`preClose` 可用）；DAT-02→**已锚定**（000300.SH 日线可得）；DAT-04→**已排查**（`dividend_type` 生效：back 133.756 vs none 9.67；`front≡none` 于无除权窗口⇒含除权窗口对照待补）。**新契约事实（候裁）**：`get_market_data_ex` **`count` 优先于 `start_time`**（同传时 start_time 被忽略、静默取最新 N 行）——与「endDate 空串→静默空」同类静默陷阱，建议升 registry DAT 新行并检查 wrapper 是否有 count＋start_time 同传的潜在偏差面（**本轮不擅自新增行**）。**精确计数（含口径更正）**：对齐面未验证 **36 格**（共享面 32：POS16/MCH10/FEE5/DAT1；专属面 4：QMCH）；**诚实更正**先前「45 格」源自取格缺陷脚本（行尾 `|` 空末格 + MCH「未验证（QMCH 系承接）」误计）。**A 件两处诚实标注**：实际区间≈2026-01-05~01-30（非建议 2024Q1，对语义判据无影响）；`END n_total=` 未出现⇒末 bar 语义本轮未取到（不据此下结论）。
- **未闭合**：D-28 簿记批（预置候令）/R1-C 批推/trading 同步门（候四线）/D-29 候裁（已由 registry QCAL-04 承接登记）/S1 archify 目验。

### 快照 2026-10-15 凌晨（D-30 R1-B 提交 `0b88728` ＋ D-28 ③实施④验收全过 · 双候⑤确认）

- **D-30 ⑤批准落地——R1-B 提交完成**：commit **`0b88728`**（2026-10-15，15 文件 +16479/−17；父 `43f985c`）。精确清单＝wrapper + 导出脚本 + 15 用例 + 矩阵 yaml/md + 影响面图目录 10 文件；**AGENTS.md hunk 与他线 40 项 M 均未纳入**。写前快照 `d6a6140f`（全树超集，stash store 持久化）。矩阵 `--check` 复跑 OK。**R1-C 并入统出批，勿单独推**（本会话无 dev 成员可寻址 → 提交事实经台账＋本轮申报转 dev）。E2E 盘后端测并入用户端测窗。
- **D-28 A 批准——③实施轮**：①图：archify `dataflow` 型，预检 READY（本机无 Chrome → 写死 Edge x86，`ARCHIFY_CHROME` 已置）；**7 轮修复后版式闸不可收敛**（showcase 41→31、standard 18→16→13，全为走廊穿越/净空类；证据类诊断已清零）→ 按「工具故障不得变成流程故障」**降级 mermaid 载体**并留 7 轮记录：`docs/evidence/archify-qmt-hist-fold-20261015-0115/IMPACT-DIAGRAM.md`（HTML 未生成，四门状态如实标注）；②写前快照 `17040123`＋`agent_workspace/_d28_backup/`；③实施 **E7 ZCode**（wrapper `_qs_hist_fold` :1383＋两路径接入 :1612/:1642＋键恒附 :1696-1700；测试文件 354→382 行）；④**就地修复三项（披露）**：F1 `≪`(U+226A)→`<<`（破坏渲染 GBK 契约，连带 17 例失败）/ F2 测试机制改 exec 命名空间 / F3 用例 5 改调真实 `_latest_by_code` 并按码折叠。
- **④O5 五步全过**：diff⊆清单 ✓ / py_compile ✓ / ruff 基线对照**零新增** ✓ / `check_fund_matrix --check` OK（`qmt_wrapper_hash: d89e3aa103e6→20f58868117f` 同批追认）＋**CONTRACT GATE : PASS（含 6 策略 api_portability 全过）** ✓ / 七套件 **111 passed / 3 failed**（3 例系 **D-32 既有遗留**、已归因登记候裁）＋新套件 **12/12 passed** ✓。证据：`docs/evidence/qmt-d28-hist-fold-acceptance.md` 等 5 份。
- **编码委派台账**：`zcode_code` 5 次——1 次产出 wrapper；测试文件 2 次回执中断（产物实际落盘 02:35:08）＋2 次 `[zcode-fallback:ratelimit]`（GLM 5 小时窗口、重置 02:42:30、两次重试均在其前 3 分钟）。最终测试文件仍为 ZCode 产物，DSH 未重写。
- **候⑤双项**：①D-30 R1-B 提交确认（→ dev 统推/R1-C 并入统出批）；②D-28 ③④验收确认（→ 提交批次精确清单：wrapper＋`tests/test_qmt_hist_fold.py`＋矩阵 yaml/md＋图交付＋证据文档＋簿记）。
- **新增候裁**：**D-32**（`test_qmt_progress_audit` 3 例 NameError：exec 命名空间缺 D-30 的 MODULE_HEADER；修点不在 D-28 清单 → 零越权登记，建议并入 D-28 提交批次或独立 mini 轮）。
- **未闭合**：D-32、D-29 候裁；D-28/D-30 端测判据候用户端测窗；统出批（R1-C）；trading 同步门 BLOCKED（候四线清理回报齐后本线补跑，D-27b+本批欠账同窗）；S1 archify 目验。
- **端测轮启动（2026-10-15 09:4x）**：D-28 A/B 背靠背材料已产出并过门禁——**A（折叠前）**`9d00de2fc2d58d4a` 101,999B、**B（折叠后）**`9d54605aeb83d556` 106,423B，均 `enc=gbk`/无 BOM/首行 `#coding:gbk`、portability+selfcheck PASS（A 折叠标记 0、B 5；隔离性＝两件**只差 D-28**，D-30 同在两件且默认关）；产物 diff `ADD=113/REM=31/5 hunks` **全落 wrapper 注入段（行 1246–1564）**。落盘：`docs/evidence/d28-e2e-pair-20261015/`（两件＋`SHA256SUMS.txt`）＋操作手册 `docs/evidence/d28-e2e-checklist-20261015.md`（含判据 1/2/3 判定表、回抄清单、diff 处置次序、回退条款）。**纪律**：判据取证须**盘后同窗（≥15:30）**背靠背；盘中跑仅算冒烟轮、不入判据。临时 detached worktree 已清理。
- **端测 E2E 判据结果（2026-10-15 用户裁定「判据 1/2/3 验收通过」）**：**判据 1 PASS**——`HISTFOLD t=growth_ability/t=eps before=8742 after=79 intravar=0`（`before` 与 HISTDBG3 `rows=8742` 逐位吻合；**`intravar=0` 证明「组内值列恒等」前提在真实数据成立**）；**判据 2 PASS（超设计预期）**——处理段 `fin_to_rows_hist` 313.0s+284.8s → 6.1s+6.1s（**597.8s→12.2s，≈49×**），未改动段（拉取 / close）B 轮整体更快属窗口负载波动，**诚实归因不归功折叠**；**判据 3 PASS**——**14/14 逐位零漂移**（L0…total_value 全等，`CLOSE_EQC n=20 diff=0`），QSFUNDHIST kept 9.74M/9.72M → 148,555/148,196＝折叠后行数（预披露口径变更）。**判据 4**：D-29 未裁不判，由 `test_qmt_hist_fold.py::test_12` 真实函数合成对照代偿。旁证：**L1_listed 4409→4408**（合约信息表再次变动，**D-30 快照层立项依据第三次得证**）；eps `uv=71 vs uk=79` 与立项要件一致。证据：`docs/evidence/d28-e2e-run-20261015.md`。
- **三裁（2026-10-15 用户裁定）**：①**盘后复跑照准**——本轮为「盘中同窗背靠背」强证据轮，盘后 ≥15:30 静止窗复跑**同一对件**（sha16 不变：wrapper 提交只改版本控制不改内容）作正式归档；②**D-28 提交批次照准（含 D-32 两行）**——commit 后**随下次批推**（与 dev ahead=1 同队，**不单独推**）；③D-32 在案。
- **D-32 修复落地**：`tests/test_qmt_progress_audit.py::_ns()` 的 exec 序列补 **`_QS_QMT_MODULE_HEADER`**（`_assemble` 序首项 wrapper :2735；不动 `_TEMPLATES` 以免波及 :250 扫描断言）→ 该套件 3F → **15 passed**，七套件 **114 passed / 0 failed**。证据 `docs/evidence/d28-d32-progress-audit-fix.txt`＋`d28-seven-suites-final.txt`。
- **提交批次执行（2026-10-15 11:1x）**：R1-B 式精确提交 **`8be00aa`**（24 文件 +6798/−38；父 `a5f192f`；写前快照 `8174677a`）。清单＝wrapper＋`tests/test_qmt_hist_fold.py`＋`tests/test_qmt_progress_audit.py`＋矩阵 yaml/md＋设计 v0.2.1＋证据 11 件＋影响面图目录 4 件＋簿记两件；**他线在途 M 零卷入**（暂存集合经枚举核验）。**R1-C 候下次批推**（与 dev ahead 同队，不单独推）。
- **盘后复跑归档（2026-10-15 已完成）**：A `15:50:32→16:07:31`｜B `17:01:26→17:07:21`，盘后静止窗、同一对件（跑前复核 sha16 未变）。**判据 1 PASS**（HISTFOLD 两表 `before=8742 after=79 intravar=0`；A 无此行属预期）／**判据 2 PASS**（处理段 **554.8s→22.8s，≈24.3×**；单表 323.4→11.1s、231.4→11.7s）／**判据 3 PASS**（**14/14 逐位零漂移**，`CLOSE_EQC diff=0`）。**四轮交叉核对**（盘中 A/B ＋ 盘后 A/B）：14 项漏斗与指标四轮全等；A 对两次 QSFUNDHIST `9,742,023/9,168`＋`9,716,108/35,083` 完全相同、B 对两次 `148,555/3,800`＋`148,196/4,159` 完全相同 → **折叠行为确定性、跨窗复现**。未改动段（拉取/close）互有快慢属负载波动，不归功折叠。证据：`docs/evidence/d28-e2e-run-20261015.md` §七（待随下批入库）；ISSUES D-28 行状态已更新为「盘后正式归档完成」。



- **D-28 方案①完成**：`docs/qmt-hist-fold-design.md` **v0.2.1**——按裁定 (a) 立项要件 4 项成文（79 期全史/`drop_duplicates(subset=['m_anntime','m_timetag'], keep='last')`/数据修正型三项证据含 SG-MS-PEG ROE 前后对照/处理段 226s→~10s+拉取段不削披露）。核心设计四件：①折叠 helper `_qs_hist_fold` 取代 D-27a 自保护块（`:1536-1549`，ffill 已证空操作）+单码路径（`:1570-1578`）对称接入；②**键字段恒附**（P1-1：返回列=请求列结构洞——SG-MS-PEG:358/F-Score:329 缺 publ_date 折叠不生效 → `_qs_get_fundamentals` hist 表 native_fields 恒附两键，辅助键经 `:1660-1677` 既有投影自动剥离，返回契约零变化）；③**组内恒等前提明示+intravar 哨兵**（P1-2，三层设防：语义论证/每表一次零热路径哨兵/单测 8 预期语义）；④HISTFOLD 审计行与 HISTDBG3 并存互证+QSFUNDHIST kept 语义变更披露。影响面六变化项+不变面穷举（含单码路径二阶变化 P2-9）。
- **②审计两轮（均 E7 外部委派，ISSUES D-28 表已登记）**：第一轮 sess_c910dab0 **PASS-with-findings**（P1×2+P2×9，tokens 818555）→ v0.2 逐条采纳；第二轮增量复审 sess_075aae9a **P1×2 均闭环·判「进③实施」**（tokens 1508362；无行号漂移/未偏离 §9.2/要件 4 项完整承载；D-P2-1 F-Score:77 空引更正 :329+P3×2 已随 v0.2.1 更正）。知识反哺留痕在位（[无命中] 折叠类 + [命中] 端测纪律条目）。
- **⑤阶段进展（2026-10-13 用户裁定）**：①②**验收通过**——「两轮审计闭环（P1-1 结构洞经键字段恒附+投影剥离零契约变化无条件闭合 / P1-2 暗前提三层设防〔语义论证+运行时哨兵零热路径+单测〕）+知识反哺留痕+判型数据修正型+回退齐备——合格，**v0.2.1 在卷**」。**A/B 裁定随 2026-10-14（周三）08:00 呈批轮**（附总调度审计意见：A=D-28 v0.2.1 进③实施，建议批准——②已判进③、实施轮流程与 D-30 同款合规；B=排期口径认可，建议认可——D-29 决定折叠对 profit_ability 的意义、不决定对 eps/growth_ability 的可实施性，D-29 候独立裁定）。**A 终裁通过前不启动③**。
- **A 通过后即启动③实施**（流程与 D-30 同款）：archify 预检（Chrome→Edge 降级链）→ 影响面图随 Plan 第⑧项子项提审 → 写前快照四步配方 → O4 七件套委派 E7 → O5 五步（邻接六套件+12 新用例/六策略重转 ALL PASS/矩阵哈希同 commit 追认）；B 认可后 D-29 维持候独立裁定。
- **验收设计要点（供⑤审阅）**：单测 12 例（含 importlib 加载 SG-MS-PEG 真实函数前后对照——要件③合成级代偿）；端测四判据（HISTFOLD 读数/处理段性能口径/盘后背靠背 A/B 零漂移+窗污染处置次序/D-29 条件项）。
- **未闭合更新**：D-28 ①②完成候⑤；D-29 候裁（与 D-28 §七 排期口径同呈）；E2E 盘后判据（D-30/D-28 同队随用户排期）；trading 同步门 BLOCKED 候协调；S1 archify 目验；统出批（D-30 提交+R1-C，候用户调用）。

### 快照 2026-10-13 晚（D-30 实施+O5 验收全过：快照层落地 3 文件+矩阵哈希同 commit 追认 · 候⑤用户确认提交）

- **实施轮 Plan 照准**（用户+总调度审核：v0.3 两轮审计闭环+⑤已批；影响面图降档 standard 披露合规——showcase 版式闸 5 轮拓扑收敛不纳四消费点扇出，四门+visual-check+AI 目验全过；E2E 盘后端测并入用户排期与 D-23/D-24 粘贴同队；D-28 排队维持）。影响面图：`docs/evidence/archify-qmt-instr-snapshot-20261013-162500/`（基线 e06ed52）。
- **③实施（E7）**：写前快照 `c2ef3f0f`（stash store 持久化，含全部在途态）；ZCode sess_5f0b2f67（tokens 4252906）三文件：`source_import_qmt.py` +141/−15（全落 `_QS_QMT_MODULE_HEADER`：`_QS_QMT_INSTR_SNAP_PATH`+`_qs_snapshot_load()` 四态缓存+`_qs_instr_detail()`/`_qs_instr_list()`+四消费点原位拦截+首印/first_miss 两审计行；ZCode 侧 Bash 权限阻断 pytest 如实呈报未虚称）；`docs/probe/qmt_instrument_snapshot_export-20261013.py`（QMT 粘贴运行型，100 行）；`tests/test_qmt_instrument_snapshot.py`（15 用例，设计 §五 8 组全覆盖）。
- **④O5 五步全过（DSH 侧）**：① diff⊆允许写入清单（3 文件+矩阵 yaml/md 追认 1 行各）② pytest 三套件 **70 passed**（15 新+49/50 存量零衰减）③ py_compile ✓+`run_contract_gate.py` 契约套件绿 ④ 契约零变化（四消费点原位、live 调用/异常语义逐点保留）⑤ 六策略 api_portability 冒烟 **全部通过**。矩阵哈希同 commit 追认 `92c05ce43085→d89e3aa103e6`，追认后 --check OK+`test_fund_matrix_qmt_gate` 5/5。
- **缺陷处置台账**：DSH 侧修 1（导出脚本 GBK 重编码，披露）；委派轮规格缺口裁定 5 项（ISSUES D-30 行）；CR 备注 1（`not d` 分支移位 edge 不可达）。
- **当前 HEAD `9fd8cc1`**（他线推进，wrapper 文件两版间逐位一致已核）；**候⑤**：用户确认本轮验收 → R1-B 式精确清单提交（6 文件：3 实施+矩阵 yaml/md+簿记 2——ISSUES/PROJECT_STATE 已含本轮）→ 双仓推送 → trading 同步门（与 D-27b 欠账同批协调）。
- **未闭合**：E2E 盘后判据 A/B/C（用户排期）；D-28 方案①起草排队；D-29 候裁；trading 同步门（BLOCKED 候协调）；S1 archify 目验。

### 快照 2026-10-13 盘后（D-27b 全链收口：R1-B 提交 e06ed52 + R1-C 双仓推送三方一致；trading 同步门受阻候协调）

- **裁定落地**：用户裁定批量版**保留**（EQC 护栏价值）+ 指令「走 R1-B→R1-C」。
- **R1-B 提交 `e06ed52`**（16:09，24 文件 +19585/−11）：wrapper（D-27a+D-27b，+206/−5 归属自查无他线叠加）+ check_fund_matrix.py（M5.2 件1 `_QMT_HASH_SCOPE`）+ tests/test_fund_matrix_qmt_gate.py + 矩阵 yaml/md（qmt_wrapper_hash 92c05ce43085 同批追认，reverify 闸 PASS）+ 证据文档 §八 + archify 交付目录 + handoff/D-30 方案/D-31 探针 + ISSUES/PROJECT_STATE 簿记。写前快照 `dd254d0b`（stash store 已持久化）；精确 add 12 路径，未卷入任何他线 M 文件。
- **R1-C 双仓推送完成**（16:11）：prepush-gate 裁定捆绑放行（QS_PUSH_BUNDLE_RULING=用户令「走 R1-B→R1-C」，14 笔他线提交逐一 AllowAlso 列明，留痕 `docs/handoff/push-bundle-rulings.log` 第 3 行）；两远程 `ccbd97c..e06ed52`，**双远程 HEAD+本地三方逐位一致 `e06ed52e2de1…`**，ahead/behind=0/0。GitHub「Bypassed rule violations」系管理员直推信息提示非错误。
- **⚠ QuantStudio-trading 同步门 BLOCKED（候协调）**：推送触及共享层 → 同步义务成立；但副本工作树脏 94 项（含 `M AGENTS.md`/`docs/sync-ledger.md`/`review-kb/` 等他线在途改动），前置条件「工作树干净」不满足——按零越权不清理，**候用户协调他线后补跑**（fetch origin → merge origin/main → check-drift 九项 + ci-smoke 共享层段 → 台账登记）。副本 HEAD `a8f5eb6` 落后 origin/main（`e06ed52`）。
- **D-30 实施解锁**（R1-C 完成即解缠绕前置）：下一轮 = D-30 实施 Plan-Mode（v0.3 已⑤批准）+ archify 影响面图（四消费点分支）+ O4 七件套 → zcode_code（E7）+ O5 五步验收。

### 快照 2026-10-13 盘后·前一条（A/B 黄金对照定谳：等价 PASS / 性能预期 FAIL——批量保留裁定已落）

- **A/B 对照（15:11 A → 15:28 B 背靠背，方法＝用户批复②）**：A=批量版产物 sha16 d298641（canonical 重转）vs B=stash `00b0c5a3` D-27a 态逐只版（`git show` 单路径字节级提取→`--only weekly_smallcap` canonical 重转 sha16 69966ef2，portability/selfcheck PASS）；转换后 wrapper+产物 sha 校验逐位还原，工作树零残留。**判据 3 ✅ PASS：14/14 读数逐位一致**（L0…total_value + QSFUNDHIST 两表 kept/drop/cc 全等），且 A 与上午 10:12 r1 亦逐位一致（同窗无推送）——纯性能型行为等价闭环。**判据 5 ❌ FAIL：close 段 A=182.4s vs B=182.9s 持平（批量零净收益）**——瓶颈在每只全史数据服务端扫描/传输量，非调用开销（EQC 热缓存 1.1ms/只为证）；未改动段噪声（valuation 33.6↔8.9s 等）反证总墙钟差 67s 非批量功劳。证据：`docs/evidence/qmt-d27-close-batch-20261013.md` §八。
- **待裁定（阻塞 R1-B/R1-C）**：批量版**保留**（推荐：等价已证+EQC 每轮运行内生护栏+零成本保留）vs **回退**（零收益换简化）。回退条款「任一不同→回退」未触发（无不同）。裁定后 → R1-B 精确清单提交 → R1-C 双仓推送（携他线 ahead 提交，需新确认）。
- 其余未闭合：D-30 实施候 R1-C 后（方案 v0.3 已⑤批准）；D-28 方案①起草排队；D-29 候裁；S1 archify 目验。

### 快照 2026-10-13 午后（机理二次修正 + D-30/D-31 立项：PIT 冻结合约快照层）

- **机理二次修正（用户据官方文档质证）**：回测＝逐K遍历固定本地历史数据（dict.thinktrader.net + wrapper `_qs_snap_kwargs:316-329` 锚定回测 bar 当日）→ K线读数跨运行稳定；**漂移源＝本地合约信息表**（`get_instrument_detail` 无 as-of 参数，`07-行情函数.md:2446-2475`），随供应商推送更新 → L1/L2 漂移、下游继承。原「query_last_close 盘中读实时价」归因**已撤回**（过度归因）——证据文档 §六 / 主库端测纪律条目 / 项目记忆 630903d5 三处同步修正。盘后 A/B 结论不变（理由更正为：压名单推送窗口 + 负载回落）。
- **D-30 立项（用户裁定）**：wrapper 数据访问层加 PIT 冻结合约快照层。**②审计两轮闭环（2026-10-13 午后）**：第一轮 ZCode sess_29a2d2aa FAIL（P0=第 4 消费点 `:502` `_QmtDataBar` 三字段遗漏，已独立复核属实；P1=判型枚举+三问审计；P2×6）→ v0.2 修订 → 第二轮增量重审 sess_82a9a1c0 **PASS-with-findings**（7 项全闭环，grep 证实无第 5 消费点）→ 2 项新 P2（模板落点钉死 MODULE_HEADER / broken 态 n=-1）已折叠入 **v0.3**。**现候⑤用户审批**（含判型映射裁定：新增检测型↔新增能力型措辞）。实施排在 D-27b R1-C 之后；实施轮附 archify 影响面图+矩阵哈希同 commit 追认。E7 台账：两轮审计 tokens 418723+229616 已登记 ISSUES。
- **D-31 已定谳（2026-10-13 14:35 实测，closed）**：`get_stock_list_in_sector(sector, realtime)` 传毫秒时间戳（now/2026-01-05/2020-01-01 三档均同）→ **静默返回空列表 0 只不抛错**；不传参正常（base=5227）→ **B4=无源**（QMT innerApi 板块成份/合约信息无 as-of 能力，ST/板块历史正式登记为能力边界）；静默空返回坑已回灌主库（06-数据与工具/QMT大转换管线…形态坑.md 复现固件节）。推论：D-30 快照层+B1 在场性推导是可复现性唯一路径，方案 §七已更新。
- **未闭合**：A/B 盘后执行（≥15:30）；D-30 方案②审计→审批→实施；D-31 探针读数；D-28 方案；D-29 候裁；R1-B/R1-C；S1 archify 目验。