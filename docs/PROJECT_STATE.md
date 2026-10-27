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
- **三终裁落位（2026-10-16 09:35 用户）**：①**N=6 采信**；②**端测配额分级照准**（必端测三边界＝新 API 面首用/新数据面接入/撮合口径变更；行为不变类＝契约门＋黄金 fixture，D-28 四轮读数为首批黄金）；③**QD 首批＝P1 级 6 件先行照准**。方案升 **v1.1**（§十 裁定记录）。**②审通过（2026-10-16 10:33）＋批 1 放行**——两条件（仅约束批 2：GBK 钩子落点文件＋矩阵哈希同批追认／件 D BLOCK-WARN 边界先呈后落）已折入方案 **v1.2**；导读卡三自陈风险照准；**批 1 已完成（2026-10-16 10:4x，候⑤呈批）**：`knowledge/qmt/` 建库（README〔schema＋迁移节〕＋6 条 QD 条目 ↔ D-21/22/28/30/31/32）＋`knowledge/README.md` 导览入口；**schema 机检 SCHEMA_OK（6×8 必填）／QD↔registry 互链 5/5**；写前快照 `93a914e8`。导读卡：`docs/qmt-alignment-closure-review-card.md`。**QD-0006 枚举裁定（②审 2026-10-16）**：不扩枚举＋机械判定规则入册（fix_target 落点域⇒matrix/ledger/wrapper/portability_rule/probe）⇒ QD-0006 判 `matrix`（契约测试域），README 待确认项已销。**批 1 ⑤用户批准（2026-10-16 14:4x）**；**批 2 启动**：件 C 台账回填已实施（batch3 行 N=9／**R=1**〔GBK `≪` 复发→先修管线〕＋载体清单扩 `knowledge/qmt/` QD 库）；件 D 前置**已过审**（②审三裁定：B1–B3 BLOCK／B4 降 WARN 待证／W＋豁免照准）。**批 2 已完成（候⑤）**：件 C 台账回填＋件 D 读取器（`scripts/qmt_kb_preflight.py`）＋写前拦截强化（`orchestrator.py::_write_qmt_product`，B1 实效位置）＋**10 passed**＋**哈希未变**（不触 `_QMT_HASH_SCOPE`）＋**六策略零回归**；**R=1 处置闭环**。**批 3 已落**：件 E 端测配额手册（`docs/qmt-e2e-quota-manual.md`：三档配额＋黄金 fixture 规则＋执行 SOP）；件 F S4 台账 QMT 收敛段（`knowledge/alignment-convergence.md` §5：台账判据＋**N=6 终局判据**＋终点仪式＋逐轮登记）。**D-29 呈裁件已出**（`docs/d29-ruling-submission.md`，含文档查证：QMT 无 `profit_ability` 原生表，候选映射＝`PERSHAREINDEX`/`s_fa_roe`）＋**②审意见入卷（§七）**——**候用户裁定，裁定前不实施**。**批 3 验收通过 ⇒ D-33 六件 A–F 实施阶段全部完成**；批 2＋批 3 候⑤（专轮或直批）。**R1-C 统推完成（2026-10-16 21:2x）**：提交 `4764a0e`（26 文件）；prepush 门 PASS（逗号放行清单、未用 --no-verify）；**双远程逐位一致**（`4764a0e2caa…`）＋三方一致（0/0）；快照 `88c4bb88`；**叠加披露**：`orchestrator.py` 他线 `deploy_qmt` 未纳入；**远端 bypass 分支保护披露**；**trading 同步门受阻**（工作树 93 项脏，候四线清理同窗补跑）；**D-29 S2 探针 v1 端测＝无效读数**（五字段含无效字段全 `rows=2/nonnull=-1`；根因＝平台返回非简单 DataFrame，裸 len()/notna() 仪器错配；**结论作废不得引用**）；**v2 端测＝形态定谳（Panel）但形态不可区分**（无效字段同形同维；判别须读值）；**v3 端测＝内置拒判 INSTRUMENT_FAIL**（正对照亦 nonnull=0 ⇒ 本轮不入结论；三轮零误判）；根因为**假设**（v3 列名假设 vs wrapper 生产口径 `list(raw.items)`+`to_frame()` 按 code 取子，见 `source_import_qmt.py:1462`）候 v4 证实；**用户裁定停手**：v4 须据生产 Panel 取值分支重铸、一次到位，**v4 已出件**（`docs/probe/qmt_profit_ability_probe_v4-20261016.py`：生产口径复刻 `raw[_code]`＋自身列名取非键列；真实 pandas 离线自测 4 场景全过）**S2 定谳完成（23:48）**：正对照 9383 有效行／负对照 0 ⇒ 判据成立；**三候选全 0 ⇒ `NO_ROE_FIELD` ⇒ 路径①否证**；`ASHAREINCOME`/`ASHAREBALANCESHEET` 未测（合成未验）。**终裁＝裁②′（先探合成）**；**v4 候选集命名错误已更正**（QMT PERSHAREINDEX 比率类字段**无 `s_fa_` 前缀**：`du_return_on_equity`/`equity_roe`/`net_roe`/`total_roe`/`gross_profit`/`net_profit`，依据 Context7 xtdata 全字段表）⇒ **v4 `NO_ROE_FIELD` 结论作废**；**v5 端测定谳（00:21）＝FIELD_CONFIRMED 4 命中**（`du_return_on_equity` 9460/0.541/7.81、`net_roe` 9460/7.53、`net_profit` 9460/22.75；`equity_roe` 7891/**16.0 整数异常**排除；`total_roe`/`gross_profit` 无效）⇒ **QMT 有原生 ROE/净利率、无须合成**；**用户三裁**（①`roe`→`du_return_on_equity`／②`net_profit_margin`→`net_profit` 独立登记／③口径三件套＋阈值档）；**六步①方案 v1.0 已落盘**（`docs/qmt-profit-ability-mapping-design.md`）**②审通过 →③实施完成（01:5x）**：MAP 增一行＋单测 **7 passed**＋既有 49/53 passed 零衰减＋哈希同批追认（`6927447ea23c`）＋CONTRACT GATE PASS＋矩阵 qmt_rows 两行＋QD-2026-0007。**④判据 3 PASS（15:23 端测）**：`QSFUNDHIST kept=152`（改前 0）／`rows=152 nonnull=152`／`HISTFOLD after=79 intravar=0`；**判据 4 进行中**（两侧同为年内累计 % ROE 形态一致；**本地覆盖最早 2018Q1** vs QMT 79 期至 1990s ⇒ 仅 2018+ 可同期比对）；**④验收通过（15:45）**：判据 1/2/3/4/6/7 PASS；**判据 4＝口径一致（逐位一致）**（000001 11 期＋600000 10 期全部 IDENTICAL，无单位差/口径差）；判据 5 不适用（ROE 仅排序无阈值）；披露：本地覆盖 2018Q1+ vs QMT 1990s+（非口径差）、600000 缺 2024-03-30 期、`qmt-pa-02` 本地无列 A/B 不可比。证据 `docs/evidence/d29-acceptance.md`。**→ ⑤已入今晚呈批专轮（用户裁定：与出库首推等同批）**。**提交预置件已出**：`docs/handoff/d29-commit-preset-20261017.md`（22 项精确清单＋时点 sha256＋教训 44 命令＋写前快照＋三条失效条件；基线 HEAD `f4d9d80`，ahead=4 全为他线；我的 `4764a0e` 已在 origin/main）——**预置≠提交，候批令**。**R_i 复发候选**：GBK 编码契约族第三次在案（非编码字符＝`U+FFFD` 写盘往返损坏；探针件不经转换器写盘点＝载体缺口，候独立立项）。**D-29 红线不动**。**备件批已出（2026-10-17 17:0x，用户照准）**：批次设计件 `docs/qmt-shared-surface-probe-batch.md`——**A · lifecycle**（`docs/probe/qmt_lifecycle_probe-20261017.py`，2,386B）目标 QLC-01/04；**B1 · DAT 族**（`docs/probe/qmt_shared_surface_dat_probe-20261017.py`，3,976B）目标 DAT-01/02/04/05..15；两件 GBK 物理／无 BOM／`#coding:gbk`／compile OK；**B2 · POS 族**待建（wrapper 装配型）；**B3 · FEE 族**转**人工核对清单**（回测参数面板＝UI 面不可 API 探）；**B4 · MCH 族 10 格**不双建、**并入 QMCH probe-first 排期**。**端测请求**：A（2024-01-01~03-31）＋B1（2024-01-02~01-31），回抄 `[LC_PROBE]`／`[DAT_PROBE]` 全行。**同步门复核（独立取证）**：`4764a0e` **已在 trading HEAD 内** ⇒ 共享层确已同步 ✓（trading HEAD `daa30f5a`／主仓 origin/main `1b092649`；trading 工作树仍 4 项他线在途）。**QMCH 立项**：用户裁定**照准＋probe-first 排期**（先真机盘后撮合探针标定回填 4 格；完整六步排 10-20 呈审窗后，先保 10-19/10-22 两主节点）。**B2 备件验收 ✓＋排期裁定（二批·10-19 后择窗，交易端语境，非关键路径）**；**门读数归档缺口已闭合**（trading `999a5cf8`：drift 两读数＋smoke 105＋vault 归属读数，系 QMT 复核指出后由线调度补档）；A/B1 端测仍候用户（上轮步骤）。**⑤批准 → ⑥执行（2026-10-17 20:4x-20:5x）**：**提交完成 `7c2cf40`**（27 文件 +7473/−7；写前快照 `ad1ba32d`；精确 add 27 项 + 教训 44 暂存面核对 **27/27 全为我方**；`source_import_qmt.py` **+13/−0 纯增益**；污染扫描命中 1 处经查为**我方预置件中「描述他线提交」的文字**，非他线内容）。**推送受阻（门判据，非故障）**：`prepush-gate.ps1 -ReportOnly` 判定「**检出 8 笔非本件提交，且未提供裁定引用 ⇒ 拒绝推送**」——他线 8 笔：`3359789`／`301daa7`／`0f43718`／`1b09264`／`f4d9d80`／`47d6927`／`486882d`／`0ea425a`。**处置（候裁定，二择一）**：(a) **用户裁定的捆绑推送放行**（须 `QS_PUSH_BUNDLE_RULING=<裁定标识>` ＋ `-AllowAlso` **逐一覆盖 8 笔**；门强制留痕 `docs/handoff/push-bundle-rulings.log`）；(b) **候他线先自行推送**其 8 笔（各自过闸门）→ 本线单笔推送时清单仅剩本件 ⇒ 门自动 PASS。**纪律声明**：**未使用 `git push --no-verify`**（C7 叠加门照旧执行）；按 C7 教训「不得让他线未过闸门的提交随推泄漏」，本线**不自行断言捆绑放行**。**推送裁定（2026-10-17 21:2x）＝采 (b) ＋ 明晨专轮后统推整合**（裁定理由采信：他线 8 笔中 件3 `1b09264`/`f4d9d80` 的 ⑤未齐〔候明晨 -2+⑦ 补完〕、D1-补1/通知 `301daa7`/`3359789` 的 ⑤＝通知呈审未过 ⇒ **(a) 捆绑放行会把未过⑤的提交推出，门拦得正确**；本线「不自行断言捆绑放行」处置获采信）。**统推整合令**：明晨呈批专轮（件3 ⑤＋出库首推＋QMCH＋D-29〔已直批〕）＋通知审定齐后**统推一次**；执行人＝我方；裁定标识＝**专轮批准记录**（`QS_PUSH_BUNDLE_RULING` 引用之）；`-AllowAlso`＝**逗号拼接**（教训 24，禁空格分隔）；推后**双远程逐位核验＋回报**；随后**线调度**跑下一道同步门（ps1 口径＋读数归档新制）。**预置件已出**：`docs/handoff/unified-push-preset-20261018.md`（六节：裁定理由留档／执行前置 6 项齐备判据／一步执行序列 ①-⑦／失效条件 3 条／纪律与留痕／时点快照〔9 笔，**禁照抄**〕）。**`7c2cf40` 提交面验收 ✓**（快照 `ad1ba32d`／27 项 27/27 全为我方／纯增益 13/0／污染扫描 1 处＝描述性文字）。**A／B1 端测读数已到并完成对表（2026-10-18）**：证据件 `docs/evidence/qmt-probe-batch-ab-readings-20261018.md`。**registry 升 5 格**：QLC-01→**已锚定**（逐 bar 驱动、`barpos=1321+n` 恒等、`period='1d'`、`bar_timetag` 日期锚定 00:00 CST、**`C.stock_code` 不存在**）；QLC-04→**已排查**（`run_time` 接口存在，调度语义待细验，不以属性存在冒充已锚定）；DAT-01→**已锚定**（平台**无** `pctChg` 字段⇒须合成 `close/preClose-1`，`preClose` 可用）；DAT-02→**已锚定**（000300.SH 日线可得）；DAT-04→**已排查**（`dividend_type` 生效：back 133.756 vs none 9.67；`front≡none` 于无除权窗口⇒含除权窗口对照待补）。**新契约事实（候裁）**：`get_market_data_ex` **`count` 优先于 `start_time`**（同传时 start_time 被忽略、静默取最新 N 行）——与「endDate 空串→静默空」同类静默陷阱，建议升 registry DAT 新行并检查 wrapper 是否有 count＋start_time 同传的潜在偏差面（**本轮不擅自新增行**）。**精确计数（含口径更正）**：对齐面未验证 **36 格**（共享面 32：POS16/MCH10/FEE5/DAT1；专属面 4：QMCH）；**诚实更正**先前「45 格」源自取格缺陷脚本（行尾 `|` 空末格 + MCH「未验证（QMCH 系承接）」误计）。**A 件两处诚实标注**：实际区间≈2026-01-05~01-30（非建议 2024Q1，对语义判据无影响）；`END n_total=` 未出现⇒末 bar 语义本轮未取到（不据此下结论）。**统推完成（2026-10-18 17:34-17:35）**：按执行令 一步执行 —— 清单实测重取 **13 笔**（本件 `f3f3e63`＋他线 12 笔）→ 逗号拼接（教训 24，13/13）→ 门 ReportOnly **放行（裁定捆绑）** → 推送（`QS_PUSH_BUNDLE_RULING=呈批专轮-20261018-五项全批` ＋ `QS_PUSH_BUNDLE_ALLOWALSO` 13 笔）→ **`4764a0e..f3f3e63 main -> main`**，`PUSH_EXIT=0`。**双远程逐位核验**：`quantstudio-plus` ＝ `quantstudio` ＝ **`f3f3e63fc789d94cc9b5d9e852cc12635b8374e7`**；ahead/behind **0/0**；门留痕已写 `docs/handoff/push-bundle-rulings.log`（2 条同刻记录＝双 push URL 各调门一次）。**披露（如实）**：GitHub 回显 **`Bypassed rule violations`**（`Changes must be made through a pull request`／`2 of 2 required status checks are expected`）——本次推送**绕过分支保护**，与 `4764a0e` 先例同款；未用 `--no-verify`。**踩坑记录**：首推被勾子拒（`有裁定引用但未用 -AllowAlso 列明`）——根因：勾子侧从 **`QS_PUSH_BUNDLE_ALLOWALSO`** 取清单，仅设 `QS_PUSH_BUNDLE_RULING` 不够（两个环境变量都须显式导出）；补设后一次推成。**后续链（移交）**：①线调度跑同步门（ps1 口径＋读数归档新制）；②dev §0 复验触发；③客户通知下发链启动；④MCP 服务端需求件经渠道转发云端侧。**两裁落地（2026-10-18 17:5x，统推验收后）**：①**预置件 §三 补勾子坑照准执行**——`docs/handoff/unified-push-preset-20261018.md` 已加「★★ 勾子坑」三条注释（只设 RULING ⇒ 勾子报「未用 -AllowAlso 列明」拒推；**勾子从 `QS_PUSH_BUNDLE_ALLOWALSO` 环境变量取清单**；命令行 `-AllowAlso` 仅用于手工/ReportOnly 调用）＋执行序列补两变量赋值行＋§五 纪律表「放行通道」同步改写。②**registry 升 DAT-17 独立行照准执行**——`get_market_data_ex` **参数优先级 `count` > `start_time`**（同传时 start_time 被忽略、静默返回最新 count 行，无报错）；附证据指针（`docs/evidence/qmt-probe-batch-ab-readings-20261018.md` §二/§三）＋探针件路径（`docs/probe/qmt_shared_surface_dat_probe-20261017.py` P2 三段对照）；状态＝**已锚定（2026-10-18 端测）**；notes 记「与 endDate 空串→静默空（QCAL-05）同类静默陷阱；**wrapper 调用面待排查**（是否存在 count＋start_time 同传的潜在偏差）」。**bypass 处置**：按裁定沿用既有补偿控制（本地契约门＋矩阵）。**门禁**：registry 机检 **0**｜矩阵 `--check` **OK**。**未提交**（两件随下批，候批令）。**B2 二批择窗启动（2026-10-19 19:5x，总调度令）**：`10-19 后`条件已满足 ⇒ POS 族上机排期即刻启动。**操作说明件已出**：`docs/handoff/b2-pos-probe-operation-manual-20261019.md`（六节：前置条件 3 项／操作步骤 6 步／回抄判据 C1-C4／读数分支 4 型处置／时点协调建议／纪律 4 条）。**关键前置（从 wrapper 实现推出）**：①**交易端（模拟盘）语境**——回测语境 `account` 空 ⇒ 查询返回 0 条，属语境不适用；②**账户至少 1 个持仓**——探针靠 dump 首条记录属性全集判字段存在性，空仓 ⇒ `POSITION n=0` ⇒ 字段面不可得。**时点建议**：今日盘后 15:30 后（首选）／明日盘前 09:00-09:15（次选）；避免盘中。**探针件已核**：93,201B／GBK／无 BOM／`#coding:gbk`／compile OK／输出标签 `[POS_PROBE] C1-C4`。**候用户配合时点**（协调人＝本线）。**非交易时间问题已解并改件（2026-10-19 20:0x）**：查文档（`/websites/dict_thinktrader_net`）确认——①`handlebar` 由行情推送驱动，非交易时段仅「服务重启重推最新 bar」时可能触发（`question_answer.html`，**不可依赖**）；②**`after_init` 在 init 后 handlebar 前调用一次、可用于一次性取数**（`system_function.html`）。⇒ **B2 探针改 v2**（`docs/probe/qmt_shared_surface_pos_probe_v2-20261019.py`，94,094B／GBK／compile OK）：探针主体抽函数，**after_init 先跑、handlebar 仅在未取到有效读数时补跑**（去重）⇒ 非交易时间可跑。操作说明已同步（新增 §七 非交易时间可行性＋文档依据表）。**B2 · POS 族读数回表完成（2026-10-19 20:14 端测，QMT 交易端/日线）**：语境成立（`C1 account='8881409880'`）；POSITION **n=15**／ACCOUNT n=1（`m_strStatus=登录成功`）／ORDER n=16／DEAL n=9；**`C4 wrapper_path n=15` ＝ C1 POSITION n=15 ⇒ 生产路径一致性通过 ✓**。**registry POS 面 16 格全部回填**（证据 `docs/evidence/b2-pos-readings-20261019.md`）：**已锚定 6**（POS-02 `m_nVolume`／POS-03 `m_nCanUseVolume`／POS-08 `m_dLastPrice`／POS-09 `m_strInstrumentID`＋`m_strExchangeID`／POS-13 `m_dFloatProfit`＋`m_dPositionProfit`／POS-15 `m_dPositionCost`＋`m_dAvgOpenPrice`）；**派生 1**（POS-04 `m_nVolume−m_nYesterdayVolume`）；**已排查/缺口 3**（POS-05 无今卖量字段／POS-10 `m_eFutureTradeType` 语义不同／POS-17 空串非 None）；**不适用 6**（POS-06/07/11/12/14/16 —— 现货无多空/乘数/保证金率）。**三条关键契约发现（须回灌）**：①**`sid` 需合成**（`m_strInstrumentID` 为裸码 + `m_strExchangeID`）②**空串 ≠ None**（`m_strOpenDate`/`m_strExpireDate` 返回 `''`，判空须 `not v`）③**哨兵/垃圾值禁消费**（`m_dStaticHoldMargin`=1.797e308 DBL_MAX；`m_nCidRateOfCurrentLine`=-1618221407／`m_nCidRateOfTotalValue`=1954115676 不可解释）。**顺带取得**（非本轮目标，候独立立项）：ACCOUNT 资金面（`m_dAvailable`／`m_dAssetBalance`）、ORDER 16 条、DEAL 9 条字段面。**探针缺陷自查**：①`dir()` 对 ORDER 对象吐出数字属性名/枚举类/单字母属性 ⇒ 200+ 行噪音（POSITION/ACCOUNT/DEAL 干净），改进方向＝只取 `m_` 前缀且值为标量；②v1 全逻辑在 handlebar，本次非交易时段**确实触发**（印证文档「可能被调用」）但**不可依赖**，后续统一用 v2 双钩子。**对齐面未验证：36 → 20 格**（POS 16 格清零；余 MCH 10／FEE 5／DAT-05..15 1／QMCH 4）。**两裁落地（2026-10-19 20:5x）**：①**QD-2026-0008 已建**（`knowledge/qmt/QD-2026-0008.md`）——三条静默语义陷阱归拢（sid 形态／空串≠None／哨兵垃圾值禁消费），`fix_layer: wrapper`，`status: open`，与 QD-0001／QCAL-05 同属「静默语义陷阱族」；**附核实更正**：wrapper `_qs_get_positions:761-762` **已实现后缀拼装**（产 `510360.SH`），本地口径为 `.SS`（`ptrade_api.py:175`）⇒ 形态差异存在，但 `DataDict` 按裸码归一不受影响、**`Position` 键路径待验**；该点正是 wrapper 注释所标「【M5 实测项】后缀形态复核」，**本次端测已确证 = `SH`**（不是新缺陷，是待验项被实测坐实）。**Position 键形态项已推进（2026-10-20 00:2x）**：经本地侧自查，该项由「待验」推进为**「跨端键形态差异已确证」**——本地持仓契约唯一产出点 `_get_ptrade_positions`（`backtest_engine.py:2638-2641`）文档明载「**Keys use .SS/.SZ**」且「deliberately keeps **normal dict membership semantics**」（**精确成员语义**）；QMT 侧 wrapper 键＝`.SH/.SZ`（端测实测 `m_strExchangeID='SH'`）⇒ **差异确证**。**影响面分层**：`DataDict` 与 `get_positions(security)` 单标查询**均不受影响**（裸码归一）；**仅字典键路径**（`get_positions()` 无参）本地为精确语义 ⇒ 不一致即 miss ⇒ 控制流改变。**已定位精确键消费方**：`SG-MS-PEG-HL:491/557/595`／`四象限ETF轮动:306/797/1171`／`tech_etf_mvo_rotation:395`／`周频小市值成长动量:344`。**最小验证项已登记为批 C1**（`docs/qmt-shared-surface-probe-batch.md` §三·补）：**1 次读数**（交易端读 `get_all_securities()` 键形态 ＋ 与 wrapper 持仓键同证）即可闭——两端同为 `.SH` ⇒ 端内自洽无 miss（差异仅跨端口径记录）；若为 `.SS` ⇒ **真缺陷**，走六步流水线修 wrapper 后缀映射。**探针改进采信项**同步登记为批 C2（只取 `m_` 前缀且值为标量）与 C3（一律 v2 双钩子）。**QD-2026-0008 ① 条已同步更新**（由「待验」改为「差异已确证＋闭合方案」）。**C1 闭合探针 v3 已出（2026-10-20 01:1x，用户 C1 读数令）**：`docs/probe/qmt_shared_surface_pos_probe_v3-20261020.py`（95,503B／GBK／无 BOM／`#coding:gbk`／compile OK）——在 v2 基础上追加 **C1 专项四段**：**A1** wrapper 持仓字典键样本（`get_positions()` 键 ＋ `sid`/`code`/`amount` 抽样）｜**A2** QMT universe 键样本（`get_Ashares()`，备选 `get_all_securities()`）｜**A3** `get_position(code)` **双形态命中对比**（实际键／`.SS`／`.SZ`／裸码 四写法各是否 HIT）｜**A4 自动判定**（探针自带 VERDICT：端内自洽／真缺陷／读数不足）。**本地侧补充实证**：wrapper `get_position(code)`（`source_import_qmt.py:770`）走 `_qs_get_positions(C).get(code)` ⇒ **也是精确键查找**（非归一）⇒ 键形态不一致即 miss（与本地侧同构）。**判定分支**：A1 与 A2 后缀一致（均 `.SH/.SZ`）⇒ 端内自洽、**C1 结案**（差异仅跨端口径记录）；不一致 ⇒ **真缺陷** ⇒ 走六步流水线修 wrapper 后缀映射（SH→SS）；读数不足 ⇒ 补持仓/环境重跑（不判缺陷）。**操作说明**已补 §八「C1 专项（一次读数闭）」；**批次件 C1 行**已注「探针件已备」。**后续链照常**：9 件未提交面候下批令｜10-20 呈审窗（ACCOUNT/ORDER/DEAL 立项 ＋ QMCH probe-first 同窗呈报，经总调度转呈用户）｜B3 FEE 人工核对按序。**C1 结案（2026-10-20 02:46 端测，v6 定谳）**：A1 持仓 `total=15` 键＝**`.SH/.SZ`**；A2 universe `total=5226`、**`uni_counts={SZ:2908, SH:2318}`**（**同构含 SH+SZ**，v5 采样偏差排除）；A3 `get_position` **精确键**（`.SH` amount=26900 **HIT**；`.SS`／`.SZ`／裸码 **amount=0 MISS(zeroview)**）；A4 `VERDICT=端内自洽且单标精确` ⇒ **C1 结案**（跨端形态差异仅口径记录）。**🔴 新发现（已立 QD-2026-0009）**：`get_position` 未命中返回**零视图**（本地契约要求）⇒ **「真空仓」与「码形态不匹配」不可区分** ⇒ 硬编码 `.SS` 会把有持仓静默误判为空仓 ⇒ **重复买入风险**；成员测试 `code not in get_positions()`（`周频:344`）风险更高（不走归一）。**仪器迭代 4 次（v3-v6）如实登记**：after_init 不可用 → 需显式 C → 零视图判据陷阱 → amount 判据定谳。**证据件**：`docs/evidence/c1-position-key-verdict-20261020.md`；registry POS-09 已升级；批次件 C1 标结案。**知识库体系架构裁定落位（2026-10-20 11:5x，用户逐项裁定）**：①**按项目一库**定稿——QuantStudio 一库全域（对齐/转换/运维/性能/扩展）；trading＝主库只读镜像（同步门覆盖）＋**review-kb 第二库**（因子挖掘/因子监控/复盘，与镜像隔离）；新项目一项目一库（模板实例化）；`knowleage_base`＝通用库**本地 only**（无远程，用户裁定③）。②**platform-docs（182 md）迁主库** `knowledge/reference/platform-docs/` 随镜像同步，trading 旧目录同批删除；三注意点用户全裁定**可接受**（a 客户仓 quantstudio-plus 分发面扩大／b 跨仓迁移不带 git 历史（复制+单 commit 注明来源）／c 门禁白名单排除 reference 区）。③**ptrade-deploy（PD×8）迁主库** `knowledge/ptrade-deploy/` 后解散 trading vault/ 第二知识位。④**授权核验已执行定谳**：`api-semantics.md` 双仓 **blob 同一（607292b5）＋双侧 clean ＝ 无实质差异**（此前"内容不同"为 CRLF/LF 行尾差异＋PS 读取假象）；`registry.md` 真差异＝本会话 POS 升级未提交（预期）；**附带发现：两仓行尾策略不一致（主仓 CRLF/trading LF）→ P1 需 `.gitattributes` 定标，否则同步门永现幻影 diff**；`.obsidian/workspace.json` 建议 gitignore。⑤review-kb **在途 4 文件**（他线）收尾前不扩容，轮询 git status。⑥自动触发机制（沉淀/反哺如何在修复·推进·运维·迭代中自动落地）本轮分析中。**实施路线 P0-P3 候批**：P0＝读取器泛化＋AGENTS.md 触发条款（呈总调度备案）＋条目起草器；P1＝platform-docs/PD 迁移＋prepush 扩展（缺陷修复须引用条目）＋CI schema linter＋行尾定标；P2＝review-kb 提炼节＋六步④沉淀必填＋kb_audit 月度体检；P3＝R/B/H 指标进状态快照＋新项目脚手架内嵌同款闸。**KB 全链交接完成（2026-10-20 21:5x，用户批准）**：14 条审核答复＋附录 B/C 全部采信；**KB 全链（P0-P3）移交知识库运维管理会话**，本会话（QMT 转换管线项目执行会话）**退出 KB 排期、仅在接受咨询时提供上下文**；附录 C 第 7 项「我等其 P0 回报后接续排期」按用户指令修正——**排期权随交接移转**。等窗三文件（AGENTS.md/.gitattributes/.gitignore）因 C7.5 六棒推送未完成继续挂起。**C1 结案采信**（端内自洽定谳＋跨端 .SS/.SH 形态差异记录结案）；**QD-0009 采信并获表扬**（「真空仓与码形态不匹配不可区分→零视图静默 miss」高价值发现；`.SS` 硬编码误判持仓→重复下单风险，成员测试路径风险更高）；四条契约（钩子时机/显式 C/零视图判 amount>0/码一律 universe 形态）登记合格，**保持 open 至 wrapper 层处置方案立项**。**今日呈审窗呈报**：ACCOUNT/ORDER/DEAL 独立族立项 ＋ QMCH-01..04 probe-first ＋ QD-0009 处置定位建议（建议＝**单列六步**，首轮方案取「诊断行」纯增益选项；与本窗同批备案、排在该窗两项标定之后实施）。**16 件未提交面仍候下批令**；**B3 FEE 按序**（建议与 QMCH-04 面板核对合并同窗）。**端不可用窗产出（2026-10-20 晚）**：①**三条新发现登记**——**F1【高】QMT wrapper 未提供 `get_orders()`/`get_trades()` 公开遮蔽**（本地契约在 `ptrade_api.py:2643`/`:2652` 有定义；`source_import_qmt.py` grep `^def get_orders|^def get_trades` 零命中）⇒ 策略在 QMT 端调用将 NameError；归 ACCOUNT/ORDER/DEAL 立项（消费面缺位，非纯字段映射）；**F2【中】DEAL `m_strTradeTime` 缺前导零**（实测 `'94005'`，len=5，应 9:40:05）⇒ 固定位解析会错位，消费方须补位；**F3【低】DEAL 手续费拼写变体并存**（`m_dCommission` 与 `m_dComssion` 同值 15.2028）⇒ 定前者为准、后者登记别名，禁双写。②**段A 探针备件已出**：`docs/probe/qmt_acct_order_deal_probe-20261020.py`（96,110B／GBK／无 BOM／`#coding:gbk`／compile OK；四段 A1 资金白名单＋A2 枚举取值收集＋A3 时间格式与变体核验＋A4 哨兵/判定；硬纪律：显式 C=C、仅 m_ 前缀标量、判命中用 amount>0、码形态一律 universe 形态；**跨时刻双采样＝端恢复后执行两次（间隔≥10 分钟）比对 A1 资金字段**）。③**QD-0009 S1 方案已出**：`docs/qd-0009-s1-diagnostic-design.md` v1.0（选项 A 诊断行·纯增益·新增检测型；判域 **D2**（`_QS_QMT_POSITION_EXT` 模板串）；单点改动 `get_position` 未命中分支；**矩阵哈希同批追认为硬要求**；四例单测判据；含知识反哺检索留痕三命中）。待②审计。④**16 件未提交面复核**：HEAD 已推进至 `8026b8c`（他线六棒第一棒出仓），本线 16 件**完整在位**（已入仓 0／未跟踪 11／已改 5），他线未触碰；工作区总 775 条中 730 条为未跟踪背景噪音（`agent_workspace` 477／`data` 21／`worktable` 16 等，非本线产物）⇒ 精确 add 时须按本线 16 件逐项列出，**禁 `git add -A`**，并在 add 前排除他线 `docs/evidence/` 下 3 个未跟踪件。**四项采信 ＋ 两项裁定（2026-10-21 03:4x）**：①采信：F1-F3 三条新发现登记／段A 探针备件（96,110B GBK compile OK）／17 件更正（原「16 件」实为 15 件＋今日新增 2 件＝17）。②**卷三命名裁定：维持 `docs/qd-0009-s1-diagnostic-design.md` 现名直落**——「卷」是**呈审窗的分组概念**，**不建目录、不改名**（后续同类件沿用现名直落，不再询问）。③**QD-0009 S1 方案 ②审排下轮首项**（本轮卷幅已满）：D2 判域（`_QS_*_EXT` 模板串落点）与「明确不覆盖成员测试路径」边界预告**合格**。④接续（端恢复后，即 2026-10-21）：段A 探针端测读数 → B3／QMCH-04 面板人工核对（同窗合并）→ QMCH P0／P1-P3（C 档授权在位，≤6 次）。**QD-0009 S1 ③实施＋④验收完成（2026-10-21，待⑤确认）**：**②审通过**（选项 A 定型/零行为变更论证/不覆盖成员测试/矩阵同 commit/四例单测/双路回退全合格，附两条件：①诊断复用首次 positions dict ②诊断体 try/except 兜底——**均已落实**）。**写前快照** `3a1dbec0e26ca78901c77311a5dde7e5734a2a15`（HEAD `a56a99e`）。**改动 4 项无越界**：`source_import_qmt.py`（+19/−1，`_QS_QMT_POSITION_EXT` 增 `_QS_QMT_DIAG_FORM_MISS` ＋`get_position` 未命中分支诊断）／新增 `tests/test_qmt_position_form_diag.py`（四例含开关关闭）／矩阵 `qmt_wrapper_hash 6927447ea23c→0c842fddbba9` ＋ md 派生同步。**证据**：新单测 **4 passed**；相关回归 **16 passed**；**会红成立**（变异体去诊断块后断言失配）；矩阵 `--reverify` OK（PTrade 侧 `source_import.py` 干净，`wrapper_hash` 值未变）；**维度门 0**；**六策略契约门 CONTRACT GATE : PASS**（含 api_portability 冒烟）。证据件 `docs/evidence/qd-0009-s1-acceptance-20261021.md`。**⑤待用户确认**：提交（4 文件精确 add、单 commit 含矩阵双件）；**推送候⑥另裁**。回退双路：`git revert` 单 commit / 常量置 False 零输出。**QD-0009 S1 ⑤确认落账（2026-10-21 10:0x，用户提交令）**：commit **`19a8954`**（`fix(qmt): QD-0009 S1 码形态不匹配诊断行（纯增益/新增检测型，开关可关）+ 矩阵 qmt_wrapper_hash 同批`），7 文件 +209/−12（代码+19/−1／四例单测／矩阵 yaml+md qmt_wrapper_hash 6927447ea23c→0c842fddbba9／证据件 102 行／簿记 2）；提交内容与精确清单逐项一致、清单残留空、**矩阵门提交态 OK**。**推送候⑥统推**（与 `a56a99e` 等同批，届时另裁）；**④验收第 4 条（端探针不变）明日端读数时一并复核，不阻断**。**B 选项（.SS→.SH 形态映射）维持另立项挂起**。端恢复后照既定三连推进：段A 读数 → B3/QMCH-04 面板（用户操作）→ QMCH P0/P1-P3。**立项状态勘误（2026-10-21 11:4x，用户澄清）**：此前「呈审窗两项立项待总裁定」为**陈旧表述**——**ACCOUNT/ORDER/DEAL（段A）与 QMCH probe-first 两项立项均已于 10-20 深夜批准**（段A 即刻启动＋QMCH C 档授权在位 ≤6 次）。**端恢复后直接执行三连，无需再等任何裁定**：段A 读数（顺带复跑 v6 四形态判定＝S1 ④第 4 条一次闭）→ B3/QMCH-04 面板（用户操作）→ QMCH P0/P1-P3。**统推批次候客户运维2 ⑤批次成型后统裁**（`a56a99e`/`daf06be`/`19a8954` ＋ 本线 17 件）。**段A 读数到位（2026-10-21 16:02/16:28 双采样 ＋ v7 16:30）**：**① S1 ④第 4 条（端探针不变）已闭**——v7 四形态判定与 10-20 定谳**完全一致**（`.SH` HIT amount=26900；`.SS`/`.SZ`/裸码 MISS(zeroview)）⇒ **wrapper 改动后行为未变，纯增益成立**；**S1 诊断行端上实证生效**（三种形态不匹配各打 1 行 `[QS_QMT_CODE_FORM_MISS] … hit_forms=[...]`；命中路径无误报）。**② 段A 三面读数**：ACCOUNT 65 标量／ORDER 3 笔×60／DEAL 2 笔×47；**新确证三条**：（a）**恒等式 `m_dAssetBalance = m_dAvailable + m_dCurrMargin`** 成立（10-21 精确、10-20 02:29 精确；10-20 20:14 差 2.50 ＝ 该次 Available 与 AssetBalance **更新时刻不同步**）⇒ **ACCOUNT 字段组非原子**，跨字段恒等式校验须容差或单次读取；（b）**本地「总资产」口径 ≠ QMT `m_dAssetBalance`，恒差 646.0**（＝`m_dCurrMargin`−`m_dInstrumentValue`）⇒ 登记口径差，**来源未确证不擅自归因**；（c）**方向真源＝`m_nOffsetFlag`（48=买/开、49=卖/平）**，而 **`m_nDirection` 恒 48（买卖皆同）不可用**；`m_strOptName`（限价买入/卖出）可交叉校验。**③ 枚举标定**：`m_nOrderStatus` **54=未成交（VolumeTraded=0）／56=已成交**（样本推断，各 1 笔待确证）；`m_nOrderSubmitStatus=51`／`m_nOrderPriceType=50`（限价）／`m_eEntrustType=48`／`m_eCashgroupProp=48`／`m_eFutureTradeType=48`／`m_eCoveredFlag`（ORDER=0、DEAL=48）／`m_nHedgeFlag=49` 均占位无语义。**④ 委托↔成交关联键确证**：`m_strOrderRef`（主）＋`m_strOrderSysID`（辅）双键一致。**⑤ 哨兵黑名单 9 项**（INT32_MAX：ErrorID/GroupId/OpType；−1：FrontID/SessionID/TaskId/RealOffsetFlag）＋**新类**：`m_nOrderStrategyType` **两次采样不同＝会话级随机，禁作业务字段**（对照 `m_nStrategyID` 跨会话恒定）。**⑥ F2 修正**：`m_strTradeTime` 本次两笔均 6 位（`145506`/`145001`），10-20 样本为 5 位（`94005`）⇒ **不保证定长，解析须补位至 6 位**；**F3 确证**：`m_dCommission`/`m_dComssion` 同值并存。**⑦ 时变划分**：同日 25.7 分钟内资金/盈亏字段**完全不变**（仅浮点尾差 ≤3e-11）⇒ 变动驱动＝**交易日结算**；**同日内可当常量，跨日必须重采**。**⑧ 观察**：v7 的 A4 判定段自调 `get_position` 三次 ⇒ 诊断行多打 2 行（探针内部重复，非缺陷）；实盘反复查错形态会刷屏 ⇒ 候 S1.1（按 code 去重），**非必需不阻断**。证据件 `docs/evidence/aod-segment-readings-20261021.md`。**结转**：registry 回填须先核 `check_dimension_alignment.py` 是否需登记新族（若需＝脚本改动走框架流程）／口径差 646.0 未归因／order status 54-56 待多样本确证。**三小裁落地（2026-10-21 22:1x）**：①ACOD 独立节**维持**（已落，不改）；②**646.0 口径差维持登记不追查**（来源不归因）；③**DEAL-07（F1 四符消费面缺位）立项照准**——①方案撰写**排 QMCH 六步之后**（同 source_import_qmt.py 串行），**方案骨架已出**：docs/deal07-trade-surface-design.md v0.1（四符遮蔽设计＋本地契约 1:1 复刻＋portability 分级负向用例 N1-N4）。**骨架阶段新实证（决定方案走向）**：本地 `get_trades()` **恒空**（引擎全仓无 `_today_trades` 记录点，grep 命中 0）⇒ **严格 1:1 复刻＝QMT 侧亦恒空**；由此产生三选一裁定（A 严格1:1[推荐]／B 双端同批真实化[另立项]／C 仅QMT真实化[禁止·双端分叉]）。另：`get_open_orders`/`get_order` 的恒空/恒 None 均源自本地 `match_price_mode != 'next_open'` 分支，**next_open 分支显式登记不适用、不静默压平**。**19 件候统推批次**；B3/QMCH-04 面板候用户操作（端已恢复）。**离线突击轮 A 完成（2026-10-21 22:5x，用户批准）**：证据件 docs/evidence/offline-triage-a-20261021.md。**先更正上轮口径错误**：『未验证 37』系机械子串计数所致（MCH/FEE 把 QMT 锚点列的『未验证』也计入→虚高；LC8/IDX10 为散文段落被正则漏计）⇒ **修正后真实未闭合≈93 条未验证＋3 已知分歧**（POS14／MCH7／LC8／IDX8／FEE2／DAT11／API~38／QMCH4／ACOD1）。**结构性发现（POS 面）**：本地 `Position` 契约**仅 6 字段**（sid/amount/enable_amount/cost_basis/last_sale_price/avg_cost，ptrade_api.py:174-175）⇒ POS 17 条中 **12 条本地无字段**：其中 **5 条判不适用（现货）**（POS-06/07/12/14/16）、**7 条为真缺口须立项**（POS-04/05/10/11/13/15/17，策略调用即 AttributeError）、**2 条可锚定**（POS-03/08）⇒ **POS 本地侧真待验证仅 2 条**。**MCH7**：本地实现＋契约测试全在位 ⇒ 5 可立即锚定（MCH-02滑点/03涨跌停/04停牌/09资金不足/10碎仓，证据 test_etf_ptrade_compat:63-78、test_minute_limit_halt、test_fill_audit、test_next_open_limit_and_halt:154-177）／1 需补测（MCH-06 T+0 ETF 未见特化）／**1 建议升已知分歧**（MCH-08：本地整单拒不支持部分成交，而 QMT `m_nVolumeTraded` 可小于原始量⇒真分歧）。**LC8**：test_minute_engine_lifecycle 计数断言在位 ⇒ 4 可锚（四钩子时机）＋4 需补核。**IDX8**：test_dual_end_reconcile:45-48 双端对账在位 ⇒ 6 可锚＋2 需补核＋IDX-07 胜率口径待裁。**可清量**：两轮内可清 27 条（5 判不适用＋15 补证据指针＋7 补核）⇒ 未验证 93→约60；再经端测一轮→约40。**待您选下轮**：X 证据回填（推荐，一次改 registry，未验证−27）／Y 先做 API+DAT 枚举（纯扫描拆聚合行）。本轮零代码零提交；registry 未改动（账本口径变更需您批）。**X 方案证据回填完成（2026-10-21 23:0x，用户选 X）**：registry 单文件回填（+101/−37 行，写前快照 `b54f7c03`）。**账本结构升级**：总览由四态扩为**六态**（新增 `不适用`／`缺口` 两列）——依据 POS 归类实证（本地 Position 仅 6 字段 ⇒ 12 条被误记为『未验证』）。**逐面回填**：**POS** 17 条归零未验证 → 已锚定 3（01/03/08）＋已排查 2（02/09）＋**不适用 5**（06/07/12/14/16，现货无该概念）＋**缺口 7**（04/05/10/11/13/15/17，PTrade 有本地无 ⇒ 候立项）；**MCH** 10 条归零 → 已锚定 6（02 滑点/03 涨跌停/04 停牌/06 ETF T+0/09 资金不足/10 碎仓）＋已排查 3（01/05/07）＋**已知分歧 1（MCH-08：本地整单拒 vs QMT 部分成交 m_nVolumeTraded 可小于原始量）**；**LC** 散文→表格：已锚定 6（01-04 计数断言测试／05 run_daily `ptrade_api.py:1872`／06 盘后快照 `backtest_engine.py:2609`）＋未验证 2（07 上下文语义／08 异常中断，**仍缺证不冒充**）；**IDX** 散文→表格：已锚定 7（01/02/04/05/06/09/10，`test_dual_end_reconcile.py:45-48`＋`ptrade_metrics.py:183/208`）＋已排查 2（03/08）＋**已知分歧 1（IDX-07 胜率口径 38 vs 14 笔）**。**结果**：表内逐条未验证 **93 → 20**（LC2＋FEE2＋DAT11＋QMCH4＋ACOD1），另 API ~38 待枚举；已知分歧 4（MCH-08/IDX-07/ACOD-02 条）；缺口 7（POS 补字段候立项）；不适用 5（终态）。**未闭合主干**：QMCH4（端测 C 档）／FEE2（B3 面板＋本地费率）／DAT11（需枚举拆分）／API~38（需枚举）／POS 7 缺口（需裁定立项或接受）／4 项分歧（需裁口径）。证据件 `docs/evidence/offline-triage-a-20261021.md`。**本轮零代码、零提交**；registry 改动待随批提交。**QMCH 回测探针件已出（2026-10-21 23:2x）**：docs/probe/qmt_qmch_backtest_probe-20261021.py（10,513B／GBK／无 BOM／`#coding:gbk`／compile OK／290 行）。**观测点纠正（关键）**：QMCH-01/02/03 只能由**QMT 回测引擎**观测——实盘/模拟交易 handlebar 里撮合由柜台决定，不含『回测撮合口径』；且 QMCH-02/03 在交易模式须**真下单**才知，故探针设**安全闸**：仅当 `do_back_test/is_backtest/backtest/do_backtest` 任一为 True 才下单，否则只读打印并明确拒下。**三段覆盖**：QMCH-01 = 首 bar 限价买 100 股 → 次 bar 查 DEAL 成交价与 bar open/close/委托价比对（判 OPEN/CLOSE/LIMIT_ORDER_PX）；QMCH-03 = 成交后同 bar 卖同量 → 观察是否成交（T0_ALLOWED vs 拒）；QMCH-02 = 扫 universe 前 60 只找 |涨跌幅|≥9.8% 标的试单（无样本则如实标 NO_LIMIT_SAMPLE）。**IDX-07 无需端**：胜率口径（本地 38 笔 vs 平台 14 笔）属**纯本地计算差**，可离线定级，排下轮。**操作要点**：必须走 QMT「回测」入口（非交易），回测区间≥10 个交易日、初始资金≥10 万，运行后回抄全部 `[QMCH_PROBE]` 行（末行 VERDICT）。**段A 核实 ＋ 回测面板读数回填（2026-10-22 00:4x）**：**①段A 已做（核实确认，无需重跑）**——证据件 `docs/evidence/aod-segment-readings-20261021.md`（11,423B）载明：段A 采样 **16:02:55 ＋ 16:28:31**（间隔 25 分 36 秒，双采样）／v7 **16:30:41**；读数已落 registry **ACOD 30 条** ＋ ISSUES 落账 1 处；交付结论（ACCOUNT 65 标量／ORDER 3 笔×60／DEAL 2 笔×47／枚举 11 项／哨兵 9 项／关联键 m_strOrderRef）均已在案。**②回测参数面板读数回填**（用户截图 2026-10-22，证据件 `docs/evidence/qmt-backtest-panel-fee-qmch-20261022.md`）：**FEE-01** QMT 侧→已锚定（「最低佣金」默认 **5.000 元**可改）；**FEE-02** →已锚定（「买入佣金／平昨佣金／平今佣金」**三项分设**、单位＝比例、默认均 0）＋🔴**本地单一费率模型须三项同值映射**；**FEE-03** →已锚定（**买入/卖出印花税两个独立字段**可双边）＋🔴**本地仅卖出 ⇒ 转换须强制买入印花税=0**；**FEE-04** →已锚定（**QMT 无「过户费」专名**，等价槽位＝手续费类型＋比例/固定）＋🔴命名不对齐须写明；**QMCH-04** →已锚定（面板 15 字段全录）。**③三条风险登记**：🔴回测区间为 **2028-01-01~2028-02-02（未来日期）** ⇒ 直接跑 **无数据、探针无输出**，须改历史区间；🟡**滑点默认 0.0200 百分比**（口述为 0%）⇒ 跑 QMCH-01 前须置 0，否则 MATCH=OTHER 是滑点非口径；🟡保证金比例 5 百分比／最大成交比例 0 的现货语义待验（不以『可配』冒充『语义已对齐』）。**④附带**：面板『可配』≠『引擎按此计费』⇒ 计费行为验证（设极端费率回测反推）建议与 QMCH 回测探针**同窗口合并**。**段A 三件探针均在位**：acct_order_deal 97,000B／v7 96,920B／qmch_backtest 10,513B。**回测面板默认值更正（2026-10-22 00:5x，用户截图核实）**：用户给出**初始默认值**截图并说明「5 是我改的」，据此更正我上一轮三处误记——①**最低佣金默认 0.000 元**（5.000 为用户手改测值，非默认）；②**滑点值默认 0.0000**（非 0.0200）；③**回测区间默认 2017-01-01~2020-12-31**（非 2028，2028 为用户手改）。⇒ **上轮风险 1（未来日期）与风险 2（滑点污染）双双作废**，风险 3（保证金比例默认 5 百分比、现货语义待验）仍成立。**🔴 新增风险 4（比原两条更实质）**：QMT 回测**初始默认＝零费用零滑点**（除初始资金 1,000,000 元与保证金比例 5 百分比外，15 项费用/滑点参数默认全 0），而**本地默认最低佣金 5 元**（FEE-01 已排查）⇒ **默认参数下两端费用天然不可比**，差额会主要来自最低佣金而非撮合/数据口径 ⇒ **会把费用配置差异误读为引擎行为差异**，污染 QMCH-01/02/03 判读。**处置**：对照回测前须显式配基线（最低佣金 5.000 元／三项佣金同值映射／买入印花税保持 0／卖出印花税按本地／滑点保持 0），已写入证据件 §五 基线配置表。**本地↔QMT 费用模型三处结构差异**（转换须处理）：佣金**三项分设**（买入/平昨/平今）vs 本地单一费率；印花税**买卖双字段** vs 本地仅卖出；**无「过户费」专名**（等价槽位＝手续费类型+比例/固定）。证据件 `docs/evidence/qmt-backtest-panel-fee-qmch-20261022.md`（7,593B，5 节，含 §五 基线配置表）。**POS 7 缺口定谳：全仓零消费 ⇒ 接受缺口·不立项（2026-10-22 01:2x）**：按总调度 5.2 裁定执行消费方普查——**7 字段全仓零消费**：全仓 `.py` 属性访问形态 **0 命中**；指定 13 件（6 本地策略＋6 QMT 产物＋1 PTrade 产物）**各 0**；目录级 38 策略／472 生成产物／38 ptrade／327 测试 **均 0**；`skills` 的 2 处为 `get_business_type()` **API 签名参考**，非 `position.business_type` 属性消费（**误命中已排除**）。⇒ registry POS-04/05/10/11/13/15/17 状态改为「**缺口（已裁·接受，登记已知差异，不立项）**」，总览 POS 缺口 7 标注「已裁接受·不立项」。**已知差异三条**：①平台客观有该 7 字段、本地无 ⇒ PTrade 迁入本地会 AttributeError；②现有 6 策略与全部产物零消费 ⇒ 现网零影响；③未来新策略若引用即报错 ⇒ **建议（待另批）在 portability 侧加 WARN 检测**，把时点性结论变成可机检。证据件 `docs/evidence/pos-gap-consumer-scan-20261022.md`。**另**：FEE 计费行为验证照准（极端费率随 QMCH 回测探针同窗口，C 档 ≤6 次不变）；MCH-08 照准（本地保持整单拒＋QD 登记差异＋portability 告警）；§六 三处费用结构差异随 DEAL-07 批次落文档+规则。**本地侧四件补核 ＋ portability WARN 微项登记（2026-10-22 01:5x）**：**①IDX-07 定级**：本地**双口径并存**已查明——主口径按平仓对（卖出笔）`win_rate=len(winning_trades)/len(sells)`（`ptrade_metrics.py:176`→`win_rate_pct`)／副口径按日跑赢基准 `daily_win_rate`（:163）；平台「14」统计单位未知 ⇒ 分歧降级 **BLOCKED(外部依赖：平台指标口径定义)**。**②LC-07 → 已排查**：`Context`（`ptrade_api.py:227`）5 字段实勘（current_dt/previous_date/portfolio/blotter/capital_base）；`capital_base` 为**恒定初始资金非活值**（单一真源 engine._initial_capital，4 构造点），且 Context7 两 PTrade 文档源**均未收录**⇒平台证据欠缺不冒充已锚定。**③LC-08 维持未验证**：仅确认辅助路径兜底（`backtest_engine.py:704`），**策略回调异常语义未定位**（下一步定位 `_run_ptrade_strategy` 回调点）。**④FEE-02 → 已锚定**：本地 `set_commission`（`:781-797`）单一 `commission_rate`＋`min_commission`，`type=ETF` 时印花税/过户费置 0；与 QMT 三项分设 ⇒ **三项同值映射**。**⑤FEE-04 → 已排查**：`cost.transfer_fee_rate` 字段确证（ETF 归零），股票路径默认值与公式待补。**⑥portability WARN 正式令已登记**：DEAL-07 骨架新增 **§4.1** 四条规则（`QMT-POS-FIELD-UNIMPL` WARN ＋ 三处费用结构差异规则），**随 DEAL-07 同 commit 落地**（避免二次动 portability_rules）。证据件 `docs/evidence/local-side-4items-20261022.md`。**本地侧续推五件完成（2026-10-22 02:5x）**：**①LC-08 → 已锚定**：策略回调异常行为实勘定谳——6 处调用点（`:2271`/`:2283`/`:2368`/`:2404`/`:2553`/`:2605`）**全部捕获异常、不中断回测、记 error 日志**；日线 Ptrade 路径另 `_lifecycle_errors += 1`。**登记不对称**：该计数**仅 :2271 一条路径**，DailyProxy/PR4 只记日志 ⇒ 异常次数不可只看该计数（观察项，非缺陷）。**②FEE-02 → 已锚定**：`TradeCost`（`backtest_engine.py:227`）默认 `commission_rate=0.00035`（万3.5）＋`min_commission=5.0`。**③FEE-03 → 已锚定（升级）**：卖出单向 `stamp_tax_rate=0.001`，且 `_stamp_tax_rate(date)`（:885-889）**特定日期后封顶 0.0005** ⇒ 🔴 **印花税随日期切换、非常量**（疑对应 2023-08-28 减半，待平台核对切换点）。**④FEE-04 → 已锚定（升级）**：`transfer_fee_rate=0.00001`（万0.1），**买卖双向**按成交额计提（买入 :1167-1168／卖出 :1249）。**⑤IDX-02 → 补实名公式**：`pow(1+total_return, 250/trade_days) − 1`（`ptrade_metrics.py:123`），原「同上」推断已替换为有据证据。**结果**：LC 本地未验证 **1→0**（LC-07 已排查/LC-08 已锚定）；FEE 本地未验证 **2→0**（已锚定 4＋已排查 1）。**DAT 枚举 11 项本轮未做**（需先定拆分口径，属账本结构变更）——已提**按口径维度拆 11 条**建议案待裁。证据件 `docs/evidence/local-side-5items-20261022.md`。**DAT 11 维度枚举回填 ＋ QD-2026-0010 建条（2026-10-22 03:2x）**：**①DAT 聚合行拆分完成**：`DAT-05..15` → **11 条明细**（口径维度：日期边界/停牌日 K 线/fallback 链/复权口径/PIT 时点/指数日线专门 API/ETF 池 PIT/分钟日线一致性/字段缺失表达/单位与精度/跨源一致性）；状态按本地实况判——**已锚定 3**（DAT-09 复用 DAT-03／10 指数日线专门 API／11 ETF 池 PIT）＋**已排查 5**（06/07/12/14/15）＋**不适用 1**（DAT-08 归并 DAT-04，不双计）＋**未验证 2**（DAT-05 日期边界／DAT-13 字段缺失表达）。**DAT 面未验证 11→2**；总览 DAT：已锚定 6／已排查 7／未验证 2／不适用 1（另 DAT-16 已关闭）。**②QD-2026-0010 建条**（印花税分期间切换）：`backtest_engine.py:885-889` `_stamp_tax_rate(date)` 封顶 0.0005、默认 0.001 ⇒ **印花税随日期切换非常量**；跨切换日期每笔卖出费率差一倍、误差线性累积 ⇒ **长周期回测精度口径点**。**端测清单新增**：切换点核对（判据＝切换日前后同一卖出样本两端净额逐笔一致 <1e-9）。**附带发现**：QMT 回测面板「卖出印花税」为**单一比例字段无期间维度** ⇒ 若平台不支持分期间，跨切换日期**无法与本地对齐**，需分段拼接或显式声明已知差异。**schema 缺口如实登记**：`fix_layer` 五枚举缺 `engine` 层（本条目权宜用 probe，不擅自扩容）。证据件 `docs/evidence/dat-11-dims-enumeration-20261022.md`。**DAT 三件一次清 ＋ 统推闭环簿记（2026-10-22 09:2x）**：**①统推闭环**：`1a040a1b` **三方逐位一致**（local／plus 私有／quantstudio 公开）；推送范围 `50bc9cc2..1a040a1b` 共 **9 笔**；prepush-gate **首轮拦下**（仅文字声明未设 `QS_PUSH_BUNDLE_ALLOWALSO` ⇒ exit 9，**闸门有效性再实证**），补声明后放行，留痕 `docs/handoff/push-bundle-rulings.log`（本批 2 行，含 `ruling=RULING=2026-10-22 统推批`）。**过程披露 2 项**：①首轮被门拦＝闸门有效；②`PUSH_EXIT=-1` 系 `Select-Object -First 30` **截断管道**致 git 提前终止的读数假象，**以 `ls-remote` 双库逐位为判据** ⇒ 教训入库「**推送类命令不得截断输出**」。全程未用 `--no-verify`。**②DAT 三件一次清**：**DAT-05 → 已锚定**（`get_history` 双签名／`count=None`→20／`security=None`→空 DF／**`tail(count)` 不足如实返回不凑数**）；**DAT-07 → 已锚定（链路实名）**（`providers/base.py:245-248` **单源四件装配、无多源降级链**；数据根 fallback `backtest_engine.py:40`＋**禁止静默单端 fallback** 守卫 `fidelity_config.py:11`）；**DAT-13 → 已排查**（公开面三态：集合→空容器／单值→零视图或 None／字符串→空串；🔴 **`get_position` 零视图 vs `get_order` None 的不对称属有意设计但未成文**⇒ 待落成文契约）。**DAT 面未验证 2→0**（总览：已锚定 8／已排查 8／未验证 0／不适用 1）。**③副本同步门**已由总调度转线调度（本批触共享层 3 笔：`quantstudio/backtest`×2、`scripts/kb_*`×4、`config/`×1；P1 后门清单须扩展覆盖 `knowledge/`）。证据件 `docs/evidence/dat-05-07-13-closure-20261022.md`。**文档一致性修补轮（2026-10-22 21:1x，计划已批）**：**D1 README 自相矛盾**——正文行52 已被 KB 会话 `2aaedd0` 改为「已同步（2026-10-22）」，而变更记录行118 仍写「PD 侧同步状态＝待办」；处置＝**历史原文保留 + 行尾追加更正**（`2aaedd0` 已完成 PD 侧同步，「待办」解除），**不改历史行**以存审计。**D2 QD-0010 schema 段陈旧**——原「## schema 缺口（如实登记）」仍称「`fix_layer: probe` 为权宜／五枚举缺 engine／不擅自扩容候裁」，实际已扩六枚举并改判 `engine`；处置＝整段替换为「## schema 落位（2026-10-22 已决）」，并写明 **PD 侧已由 `2aaedd0` 同步、本条目无 schema 遗留**。**D2 根因（教训入册）**：上一轮改 `fix_layer` 的脚本对该段用 `.replace()` **未加断言**，字符串未命中即**静默空操作**，且当时只核验 frontmatter 未核验正文 ⇒ 漏改。**教训：同类批处理脚本一律带 `assert`/计数校验（静默空操作家族）**；本轮起全部替换操作均带 `must_rep()` 断言。**D3 DEAL-07 排期已写入骨架 §4.1**：五条 WARN（POS 7 字段／佣金三项／买入印花税／过户费／DAT-13 判空）**同批落**，排 **P2-c CI linter 接入之后、API 枚举之前**；理由＝避免「CI 一接入即红」的时序错位（先 linter→再规则→后枚举，每道门建立在上一道已绿之上）。**本批 3 文件修补 + 簿记 2 件，不提交**（候下批统推）。**QMCH 回测探针 v2 首跑（2026-10-22 22:18）——A1 已修，暴露两条新硬事实**：**修好证据**：`PL2 sector(沪深A股) n=5226` ⇒ 选到 000001.SZ 并成功 passorder；`CTX attrs_total=115 matched=22`（回测 ContextInfo 真实字段面首录：barpos/current_bar/period=1d/benchmark=000300.SH/capital=1000000.0/do_back_test=True/stockcode=000300/is_last_bar/is_new_bar/get_bar_timetag/get_universe/get_stock_list_in_sector/set_account/set_universe/is_stock/is_suspended_stock；**无 accountid 属性**）。**🔴新事实1：回测模式下 get_trade_detail_data 不可用**（bar2-6 全 `DEAL n=-1`）⇒ **QMCH-01/03 不能靠成交明细判定**。**🔴新事实2：回测 ACCOUNT 快照半初始化**：`m_dAvailable=1000000.0` 正确，但 **`m_dAssetBalance=0.0`**、**`m_dCommission=1.7976931348623157e+308`（DBL_MAX 哨兵）**、`m_strStatus=''`（空串）⇒ QD-0008 哨兵族新成员；**结论：回测资金面只能信 `m_dAvailable` 单字段**。**🔴新事实3：委托价被引擎改写**——系统警告「指定价11.3不在当前周期最低最高价间[11.3989,11.7022],使用最新价11.6631, 代码:000001.SZ」⇒ 我按 `get_market_data_ex(count=2)` 末行取的 close=11.30 **与引擎当日可成交区间不匹配** ⇒ 限价单被替换；根因两假设待验：①`count=2` 末行非回测 bar 当日 ②引擎按分钟级 min/max 校验。**QMCH-01 的 fill_px 判定因此被污染**。**🔴v2 缺陷（我的）**：QMCH-02 段仍只认 `get_Ashares`（未走 L2）⇒ `NO_POOL` 是**假阴性**（同 A1 病灶未清）。**另记**：`get_Ashares` 不在回测全局（POOL L1 skip）⇒ 回测取池须走 `C.get_stock_list_in_sector('沪深A股')`（5226 只，与交易侧 universe 同源）。**QMCH-01/02/03 当前状态：仍不可判**（阻断＝DEAL 不可用 ＋ 取价错配 ＋ QMCH02 假阴性）；v3 方案待批。**QMCH 回测探针 v3 首跑（2026-10-22 22:24）——三项实质突破 + 一处我的缺陷**：**①J1 取价错配已确认修复**：`BAR_DATE timetag=1788192000000 => 20260901 (barpos=1482)`；`DAYQUOTE 000001.SZ date=20260901 {o:11.42828, c:11.663108, h:11.702246, lo:11.398927, pc:11.467419}`——其中 h/lo = **11.702246/11.398927** 与 v2 系统警告区间 **[11.3989, 11.7022] 完全吻合** ⇒ **反证 v2 根因＝`count=2` 末行不是回测当日**（v2 取到 11.41/11.30 是另一日期价）。**②QMCH-01 部分可判＋FEE-01 首个正面证据**：bar1 限价 11.66 买 100 股 → bar2 `avail 1000000=>998829`，`CASH_DELTA=-1171.00`；**1171.00 = 100x11.66 + 5.00（最低佣金）精确相等（0 误差）**，而按 close 11.663108 应为 1171.31（差 0.31）⇒ **（a）限价单成交价＝委托价 11.66；（b）FEE-01「最低佣金 5 元」在回测中确实按面板收取（首个计费行为正面证据）**。**③QMCH-03 定谳：QMT 回测引擎不实施 T+1**——bar2 买入后同 bar 卖出，bar3 `avail 999988.167`（回升 1159.167）⇒ `T0_ALLOWED` ⇒ **回测允许当日买卖**，与 A 股 T+1 及本地引擎（实施 T+1）**不一致 ⇒ 已知分歧候选**。**④我的 v3 缺陷（J4-2）**：QMCH-02 段用**缓存 `GS['bar_date']`（20260901）**取价，而实际下单已在 bar3（系统警告日期 **20260903**）⇒ 取价与下单日错配 ⇒ 委托价 7.92 落在 0903 区间 [6.81,7.23] 外被改写为 6.99 ⇒ **本轮涨跌停样本无效**（同 A1 类病灶：**日期未随 bar 刷新**）。**⑤未证/待 v4**：(i) 撮合口径「按委托价 vs 按 close」**本样本无法区分**（委托价 11.66 ≡ round(close,2)，两说同解）⇒ 须用**刻意偏离 close**的限价区分；(ii) `set_account` 返回 None 但**未复测 DEAL**（v3 走资金差路径）⇒ DEAL 是否可救活仍未验；(iii) 卖出侧费率（印花税/过户费/佣金率）需**极端费率控制变量**反推（本轮采信 5 元最低佣金，其余未定）。**QMCH/FEE 回测探针 v4 A+B 双变体（2026-10-22 22:47/22:49）——六项全额精确到分**：**①QMCH-01 定谳：限价单按「委托价」撮合**（非 open/close/high/low）。证据＝双变体 x 双方向 4 个数据点，`implied_fee` 中 **ORDER_PX 项全部精确等于理论费用**、其余假设均偏离：A 变体买 000001@11.70x100=1170.00 → 现金 -1175.00 → 隐含费用 ORDER_PX=**5.0000**（CLOSE 8.6892/OPEN 32.1719/HIGH 4.7754/LOW 35.1073 皆不吻合）；A 变体买 000002@3.13x100=313.00 → -318.00 → ORDER_PX=**5.0000**；B 变体同两笔 → ORDER_PX=**11.7000**（=1170x1%）与 **3.1300**（=313x1%）。**②FEE-01 最低佣金 5 元→按面板生效**（A 变体两笔皆为 5.00，因 1170x0.035%=0.41<5）。**③FEE-02 佣金费率→按面板生效**（B 变体 1% 精确：11.70/3.13/11.40 三处）。**④FEE-03 卖出印花税→按面板生效**（A 0.1%→1.14；B 1%→11.40，均精确）。**⑤FEE-04 过户费→回测中恒 0/未收**（A 卖出费 6.14 = 印花1.14+佣金5.00 精确，理论过户费 0.0114 缺失；B 卖出费 22.80 = 11.40+11.40 精确，理论过户费 0.0114 同样缺失）⇒ 双变体交叉印证，与『QMT 面板无过户费专名字段』一致。**⑥QMCH-02 初判：回测引擎不实施涨停封板约束**——A 变体涨停样 000011.SZ（chg=9.97%）挂 9.49 买入，现金 -954.00 = 949+5 精确成交；B 变体 -958.49 = 949+9.49 精确成交 ⇒ **涨停可买入成交**，与本地引擎 `limit_up_blocked` 拒单不一致 ⇒ **已知分歧候选**（跌停卖出未测）。**🔴⑦我的判据缺陷（自查）**：`K4 T1` 的 `T0_ALLOWED` **是误判**——我总在**下一 bar 开头**读现金，且卖出发生在买入后第 4 个 bar（bar1 买→bar5 卖）⇒ **早已过 T+1**，该判据只能证明『卖单最终成交』，**不能证明当日可卖** ⇒ **QMCH-03 从未被真正测过**，须 v5 同 bar 买后立即卖。**⑧其余**：`SET_ACCOUNT rc=None` 且 bar1-8 全程 `DEAL n=0` ⇒ **set_account 未能救活成交明细**，资金差反推为回测下唯一路径（已跑通）；bar 日期逐日推进且跳过周末（0904→0907/0908）⇒ 交易日历正常。**QMCH 探针 v5 收尾（2026-10-22 23:34）——QMCH-03 定谳（引擎原文证据）＋市价单口径定谳＋两项新事实**：**①QMCH-03 定谳：QMT 回测引擎实施 T+1**。引擎系统警告原文：`当前股票000001.SZ可卖0股,小于需卖量100股,跳过,日期时间:20260901`——bar1 买入后同 bar 卖出被引擎以『可卖0股』跳过；bar2 `delta=-1175.0` 精确等于『只有买成交』（参照值 -1175.0）⇒ 判定 `T1_ENFORCED_SELL_REJECTED`。**此项推翻 v3/v4 的 `T0_ALLOWED` 误判**（v3/v4 卖出发生在买入后第 4 个 bar，早已过 T+1，判据无效——已在簿记标注作废）。**②QMCH-01 市价单口径定谳：市价单(prType=5)按当日 close 成交**。`L2B_MARKET BUY px=0 prType=5` → `delta=-319.0` → `implied_fee{CLOSE:5.0000, OPEN:12.0000, HIGH:-1.0000, LOW:12.0000}` ⇒ **CLOSE 假设隐含费用恰等于最低佣金 5.0000（精确）**，其余假设均偏离（000002.SZ close=3.14，100股=314 + 5 = 319）。**QMCH-01 至此完整：限价单→委托价（v4 四数据点）／市价单→当日 close（v5）**。**③新事实：回测撮合时点＝下单即时（非同 bar 收盘后统一撮合）**。`L4 同bar_买入后 avail=998825.0 => sameday=VISIBLE` ⇒ 下单后立即读 `m_dAvailable` 即见现金变化。这也解释了 v3/v4 『下一 bar 读现金』总能观测到变化的机理。**④QMCH-02 跌停侧未验（如实登记，不推断）**：`L3 state=NO_LIMIT_DOWN_SAMPLE(前60只当日无 <=-9.8%)` ⇒ 前 60 只无跌停样本，**跌停买入未取得证据**（≠无封板约束）；**跌停卖出 NOT_TESTABLE**（需提前持仓且当日恰好跌停，不可控）。涨停侧已完成（v4 A/B 两变体精确成交）。**⑤其余**：`SET_ACCOUNT rc=None` 且 v5 全程 DEAL 无输出（`_deal_probe` 仅在 n>0 打印）⇒ 回测下 `get_trade_detail_data` 仍不可用；bar5-8 avail 恒 998506 ⇒ 市价买单后无其它成交。**至此 QMCH 三格中 01/03 已锚定、02 涨停侧已锚定（跌停侧记缺口）、FEE 四格已全定谳。****QMCH／FEE 端测收官落地（2026-10-22 23:5x，计划已批·建议版记法）**：证据件 `docs/evidence/qmch-fee-probe-conclusion-20261022.md` 新建（七节：六项定谳＋逐项读数摘录＋五轮探针演进＋三次判据缺陷＋面板两变体＋回测 API 面总账）。**registry 落地 9 处**：QMCH-01／02／03 未验证→已锚定（02 涨停侧锚定＋跌停侧缺口照记）；FEE-01／02／03 →已锚定（端测计费实测）；**FEE-04 由「已锚定」改判「已知分歧」**（本地有公式、QMT 回测恒 0，双变体费用无余项交叉印证）；总览 QMCH 4/4 锚定、FEE 4 锚定＋1 分歧，**两域未验证归零**；变更日志追加 1 行。**结论要点**：QMCH-01 限价=委托价／市价=close；QMCH-03 T+1 生效（引擎原文拒单）；FEE-04 过户费 QMT 侧缺失（与面板无专名字段一致）。**未闭合**：FEE-03 分期间切换点（QD-2026-0010 待段落）；QMCH-02 跌停买入（无样本，需扩池重扫）；跌停卖出 NOT_TESTABLE。**QMCH 探针 v6 首跑（2026-10-23 15:45）——切换前侧印花率已定谳，LATE/跌停侧日志截断**：**①EARLY 侧（20230823，切换前）印花率 = 0.1%（已定谳）**：买入 bar2(20230822) 000001.SZ @9.34 ⇒ 934.00+佣金5.00=939.00 ⇒ bar3 avail=999061.00 精确；卖出 bar3 委托 9.23 **被引擎改写**（系统 WARNING：指定价9.23不在当前周期区间[9.23385,9.38172]，使用最新价 **9.24207**）⇒ 实际 gross=924.207；net=999979.282389547-999061.0=**918.2824**；佣金=max(924.207x0.00035,5)=**5.00**；印花=924.207-918.2824-5.00=**0.9246** ⇒ **印花率=0.9246/924.207=0.0010004 ≈ 0.1%**（与面板卖印花 0.1000 一致 ⇒ 切换前按面板值收，未打折）。**②我的探针 bug（自曝）**：`gross` 用了**委托价**（9.23）而非**实际成交价**（9.24207）⇒ 打印出 `stamp=-0.2824 / rate=-0.000306`（负值）；负值是**公式用错 gross** 所致，**不是平台行为**（用实际成交价重算得 0.1%）。根因＝卖出价取 `round(low,2)` 可能**低于真实 low** ⇒ 越界 ⇒ 引擎改价。**③LATE 侧（切换后）未取到**：买入 bar7(20230829) 000002.SZ @14.2 ⇒ 1420+5=1425 ⇒ bar8 avail=998554.2824 精确；卖出 bar8(20230830) 委托 14.05 ⇒ **缺 bar9 读数** ⇒ 切换后印花率未知（分期间存在性**尚未定谳**）。**④跌停侧（A 段）未出现**：日志中既无 `A candidate` 也无 `A_VERDICT` ⇒ 日志在 bar8 截断，bar9+ 未贴。**⑤待办**：请补贴 bar9 起全部日志（含系统 WARNING 行——若 LATE 卖出价也被改写，WARNING 的『使用最新价 X』可还原实际成交价）；探针 v7 修正三点：sell_px 改 `floor(high*100)/100`（保证落在引擎当日区间内、不被改写）、A 段移到 bar>=9（避开 B 的资金干扰）、打印当日区间便于核对。**统推 #3 前落地（2026-10-23 17:5x，两批已批）**：**① v6 定谳**：FEE-03 改判「**已知分歧**」——跨 2023-08-28 双笔端测，切换前印花率 **0.10004%**（20230823）、切换后 **0.10000%**（20230830）⇒ **QMT 回测单一费率、不实施分期间**；切换后侧由「A 跌停买与 B_LATE 卖同 bar」的**联立方程**解出（`delta=-87.405 = 净得 − 1486`）；**QMCH-02 跌停侧补齐**（扩池 300 命中 000712.SZ chg=−0.0997，买入成交）⇒ 涨跌停两侧均无封板约束。**② 本方缺陷两项如实登记**：v6 A/B 同 bar 污染（计划 bar≥9、代码 bars>=8）＋ 卖出价 `round(low,2)` 越界致 gross 失真；教训第 4 条＝**探针自动判定必须先假设"会与被测量互相污染"**（同 bar 共用一次 avail 快照必耦合 ⇒ 分 bar 或联立解）。**③ API 枚举落地**：API 域 `~40（待枚举）`→**88**（PTrade 69 待核／QS-only 9／内部 10／私有 16），「~38」真相＝docstring 单轨；合计行同步；QMT 锚点列维持待枚举（镜像三法抽取得 0）。证据 `docs/evidence/api-surface-enumeration-20261023.md`（16,953B 全量表＋缺口登记）。**④ 边界声明**：实盘柜台印花税必然分期间且**未验** ⇒ 不得以回测结论否定本地分期间逻辑。
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

## 快照 2026-10-22 01:5x（P0 知识库可机读化 A0–A8：实施＋八条验收完成 · 候⑤）

- **当前阶段**：③实施＋④验收完成，**候⑤用户确认** → ⑥并入下批推送（本轮**零提交零推送**）。
- **编排模式**：**单机模式**（预估编码 ≈700 行 < 2000 门槛；A3/A4/A5 仍按纪律 E7 委派）。
- **基线**：呈批基线 e4dd5f2（10-20）→ 出图 pin ec8323d7 → 执行期 HEAD 漂移 e7f366b（他线，无交集）；写前快照 **54728b8270266a8e6238c0afbb23d3f82cecfff4**（stash store）＋文件级 12 件备份 `docs/evidence/_bak/kb-p0-20261021-163630/`。
- **已完成（八条验收全过）**：A0 图（四门＋visual-check）｜A1 域编码表｜A2 三字段（SCHEMA_OK=9/9）｜A3 读取器泛化＋薄壳（四键等价）｜A4 22 passed｜A5 草稿器｜A6 三库注册卡｜A7 R/B/H｜A8 草案（未落位）。
- **门禁读数**：check_dimension_alignment **0**｜check_fund_matrix --check **OK**｜run_contract_gate --strategies **PASS**｜py_compile **0**｜ruff 1（基线 3）。
- **未闭合**：①A8 落位**等窗未满足**（AGENTS.md／.gitattributes／.gitignore 继续挂起）；②⑤用户确认；③⑥推送（并入下批）；④H 指标分母须调用方台账（脚本不落盘调用日志，A7 已声明）。
- **下一步待确认**：八条验收复核 → ⑤确认 → 下批推送批（含本轮 17 项改动面）→ 推送后 trading 同步门。
- **当前有效迭代范围**：P0 批（A0–A8）已收口；P1–P3 未启动。


## P0 ⑤ 确认落档（2026-10-22）

- ⑤＝用户「A. 确认」：八条验收采信；推送批 22 项照准（清单见 `docs/ISSUES.md` 同名节）。
- 待推送：**随下批统推**（窗口由用户另发）；本会话零 push。
- A8 落位：等窗未开，三文件零改动。
- 下一节点：**P1 迁移批 Plan-Mode 计划轮**（已启动，呈审）。


## P1-1 迁移批实施快照（2026-10-22）

- 阶段：③实施＋④验收完成，候⑤；**未提交未推送**（主仓与 trading 侧均悬在工作树）。
- 迁移：`knowledge/platform/**` 185 件（3.11MB，排除 .obsidian 5 件）＋ `knowledge/ptrade-deploy/**` 10 件；sha256 逐件 0 mismatch。
- 红线：硬红线零命中；恒生文档再分发按裁定一=B 已裁接受。
- eol：新建 `knowledge/.gitattributes`（根文件零改动）；额外换行 diff 0。
- 图：`.archify/architecture-p1-migration-20261022-0320/`（四门全过）＋副本 `docs/evidence/archify-p1-migration-20261022-0320/`。
- trading：`vault/` 已删（10 项 D 悬于工作树），提交归 trading 批次。
- 未闭合：①⑤确认；②PD schema 差异候裁（ISSUES P2）；③trading 侧提交；④P0 22 项推送仍候窗口。


## P1-1 ⑤ 确认 · 提交落档（2026-10-22）

- commit1 `2615cee`：P0 知识库可机读化（60 文件）
- commit2：P1-1 迁移批（platform 185 ＋ PD 10 ＋ .gitattributes ＋ 证据/图 ＋ 簿记）
- 两 commit **入统推批**（窗口另发，未 push）
- 下一节点：**PD 修订批**（已启动，本会话主导）→ 其后 P2 Plan 轮（先核 review-kb 4 在途件）


## PD 修订批 · 提交快照（2026-10-22）

- PD 族 schema 定稿（3 字段＋八枚举＋多落点 `+`＋边界段纠偏）；9 条 PD 各 +3/−0。
- `engine` 跨库同步四处（validate_entry／kb_draft 判据／kb_draft choices／PD README）＋QMT README 状态行改「已同步」。
- **未闭合**：QD-2026-0010 schema 不合规（他线，已登记候修）⇒ 套件 21 passed / 1 failed，红因不属本批。
- 提交：随统推批（第 3 笔）。


## P2-a/b/f 实施快照（2026-10-22）

- 三件落地：review-kb 核对（1 真缺陷＋3 约定确认）；prepush KB 体检 WARN（QD/PD 分族）；check-drift -Direction push/pull。
- 阻断修复：kb_preflight 行级 frontmatter 解析（split 截断缺陷，PD-0009 实证）。
- 门禁 0/OK/PASS；影响面图四门过；**未提交**（主仓 3 文件＋trading 1 文件悬工作树，候五审）。
- 待办：QD-0010（他线·裁定四）、CI linter（P2-c 后续）、trading 线补 index 10-16 行。


## KMAP-M0 完成快照（2026-10-22）

- 模板 v1 两件（schema 约定／题集模板）；19 条 trigger 补问法；R0 38 题基线（M1 100%／M1.5 100%／M2 73.7%）。
- **待裁**：M2<85% 触发原 W10 条件——建议以 M1.5 为生产口径、W10 不启动实现。
- **未提交**：P2 批（4 件＋图两目录）＋ M0 批（19 条目＋4 证据/模板件＋簿记）同候下批统推。


## KMAP-M1 完成快照（2026-10-22）

- 工具层泛化：kb_hook（族路由体检）+ kb_draft（--kb 泛化）+ kb-info v2（families 两轴）+ 接入包 4 件。
- 验收 13 项全过：pytest 22 passed、QD 10/PD 9 零不合规、prepush 读数同形、第二现场 1+3 项。
- **未提交**：M1 五件代码 + 模板 4 件 + 证据/簿记；第二现场 2 件（跨仓，未提交）。
- 下一步：M2（等窗）/ M3（QD-0010 合龙后 CI）/ M4（打包试点）。


## 三裁定快照（2026-10-23）

- 四笔采信待推：主仓 e2b634f / 97dd542 / ee526a3 ＋ trading a069206d（逐笔⑤已备）。
- 第二现场（easy-vibe）保留为新项目接入首例样板（裁定二 A）。
- 闸门状态：M0 ✅ / M1 ✅ / M3 待 QD-0010 合龙（统推 #2）/ M2 等窗 / M4 待 M3。


## KMAP-M3 完成快照（2026-10-23）

- 新增 .github/workflows/kb-schema.yml（WARN 起步，delete-only 回退）。
- 验收：YAML 11 项断言 + 两场景端到端（18 件全合规 exit0 / 0 件跳过 exit0）；既有 workflow 零改动。
- 待推：本件随下一笔；M2 等窗，M4 待 M3 ⑤ 后启动。


## KMAP-M4 完成快照（2026-10-23）

- kb-kit v1.0.0 打包（10 件）；实测机器 1.1s／人工 15-20min；8 条验收全过。
- 试点 1 easy-vibe（husky 保护跳过钩子）、试点 2 vibe-coding-cn（钩子生效，种子条目留本线）。
- 修复 3 项实测缺陷（PSScriptRoot 空压／清单顺序／husky 破坏风险）。
- 下一步：⑤ 呈批 → 第 6 笔提交；首月 R/B/H 属观察窗（待试点沉种子条目）；M2 等窗。


## KMAP 主线收官快照（2026-10-23）

- M0/M1/M3/M4 已落地并提交（第 5/6/7 笔）；M2（AGENTS 三文件＋全局层）待用户另批·等窗评估拆分落位。
- 两试点首月 R/B/H 观察窗自 2026-10-23 起算，到期 2026-11-23 出读数。
- 待推批（我方 5 笔主仓 + 1 笔 trading）：e2b634f(P2) / 97dd542(M0) / ee526a3(M1) / 1bba2ad(M3) / ceef237(M4，本簿记随本笔提交) ＋ trading a069206d(P2-f)。


## M2-① 计划件出件快照（2026-10-23）

- docs/handoff/kb-m2-seg1-plan-20261023.md（PLAN）：落位文本 v2 ＋ 既有条文收敛修订 ＋ 四步落位 ＋ 8 条验收 ＋ 段②边界。
- 状态：待④⑤＋等窗；等窗三文件零改动。
- 段①（主仓 AGENTS）→ 段②（副本跟随＋全局预设，须另批）。


## M2-① 计划件 v1.1 快照（2026-10-23）

- 计划件 docs/handoff/kb-m2-seg1-plan-20261023.md（v1.1）：拆两笔落位 ＋ 8 条验收按笔分列 ＋ 段②六步提纲。
- 待④⑤＋等窗；等窗三文件零改动（git diff 空）。
- 执行序：笔①（口径统一）→ 笔②（细则新增）→ 副本跟随（同步门）→ 退回演练 → 段②（另批）。


## M2-① 主仓落位完成快照（2026-10-23）

- 笔① 75d86c4（检索面统一）/ 笔② ee53c24（R1/R4 细则）已提交，AGENTS.md clean；等窗条件已满足且写前 diff=0 已验。
- 落位后 AGENTS.md：622 行 / 59,357 B / sha256 前缀 110B049DDB58；grep R1=1 R4=1 本轮沉淀=2 kb_hook=1 kb-kit=1。
- 退回演练：缺留痕样例判退回、含留痕样例通过、三格式可机检（证据件 §4）。
- **未闭合**：①副本跟随（待同步门）②段②全局层＋全局预设（须另批·完整六步）③统推 #5 未执行。
- 已锁定基线：AGENTS.md R1/R4 条文（2026-10-23）；计划件 docs/handoff/kb-m2-seg1-plan-20261023.md v1.1。


## M2-① 段闭环快照（2026-10-24）

- 三笔已出仓（75d86c4/ee53c24/8d9c6be，origin/main=606e688）。
- 副本跟随完成：R1/R4 节段 sha256 fb107378d529997e 主仓=副本（30 行）⇒ diff=0。
- M3 首触：df2d90e run success（qd 族、registry.md 跳过）；统推#5 606e688 run success（pd 族）——M3/M4 收尾证据齐。
- **未闭合**：段②（全局层 ~/.dsh/AGENTS.md ＋ 全局预设模板）待用户另批·完整六步；M2-① 自此主仓＋副本两面完成。


## M2-① 闭环快照（2026-10-24 总调度确认）

- M2-① 全链闭环：笔① 75d86c4 / 笔② ee53c24 / ④件 8d9c6be 已出仓；判据 8 副本 diff=0；M3 首触+二次读数采信。
- 唯一剩余前置：**段②（全局层 ~/.dsh/AGENTS.md ＋ 全局预设模板）**，候用户另批（完整六步）。
- a4fb871（后续闭环证据+簿记）列入 #6 增补披露，默认周日晚出仓。
- 观察窗：2026-11-23 到期（提醒 schedule-905902f5 在位）。


## 出仓排期更新（2026-10-24 总调度节点⑩裁定）

- `a4fb871`：**#6 锚**（已列增补披露，默认周日晚出仓）。
- `ede8ab0`：**随 #7**（#6 已裁锚=a4fb871，ede8ab0 在锚上；10-26 晚与 B1-B7 尾批同窗出仓）。
- 段②（全局层）候用户另批维持；11-23 两试点观察窗维持；本轮零实施＋簿记即写即提被确认合规。


- **`c06d364`（排期簿记笔）**：总调度 2026-10-25 裁定——**随 #7**（#6 已裁锚=a4fb871，`c06d364` 在锚上，**锚上笔自动归属、无需单列**），10-26 晚与 B1-B7 尾批同窗；本轮簿记（单文件、即写即提、写前 clean）被采信合规。


## 加速包完成 + 段②待审快照（2026-10-25）

- A1/A2/A3/B2 全部落地（2 脚本 + 2 文档 + 1 证据件），读数：M1/M1.5 100%、M2 76.3%、B2 25/25 PASS、A3 PASS。
- 段② ①方案件已出（docs/handoff/kb-m2-seg2-plan-20261025.md），**待②审**；未动 ~/.dsh 任何文件。
- 事故：PS 编码读坏 06 号归档 → git 单文件恢复（974B 一致），已登记。
- 待推：a4fb871(#6)、ede8ab0/c06d364/56ee8f4(#7)、4bb6aac(他线)、本批（脚本+文档+证据+簿记）。


## 段②待实施 + v1.1 口径快照（2026-10-25）

- 段②：②审通过，**待 10-26 联调日收工后低峰实施**（W1–W4）；~/.dsh 仍零改动。
- R0 口径：**M1.5 为 v1.1 生产判据**（38/38 100%）；M2 观察档 76.3%；W10 已触达未启动。
- 判据 8：已提前闭环（副本瘦身版，A3 PASS）；#7 后仅例常跟随核验。
- 待推：a4fb871(#6)、ede8ab0/c06d364/56ee8f4(#7)、3f00dd5(#7)、4bb6aac(他线)、本批(口径文档+簿记)。


## 段② 落位完成快照（2026-10-26）

- 全局层 R1/R4 已落：~/.dsh/AGENTS.md 17,794B（R1=1/R4=1/本轮沉淀=2）；preset ⑧·本轮沉淀子项已落（compose OK）；备份双件在位（.bak-20261026-seg2）。
- 行为面实证：fresh subagent 逐字复述＝分发生效；本会话 system-reminder 即时回显＝文件变更即重载。
- M2 全段仅余：⑥生效确认（候总调度）+ B-1 落点裁定。
- 待推批新增：本笔（证据件+ops候选+簿记）。
## 快照 2026-10-27 15:1x（QMT 转换管线执行会话 · D-24 修复闭环 ＋ 统推 #8 授权）

- **D-24 修复**（③实施 2026-10-26 → ④验收 V1-V7 全绿 → ⑤授权 2026-10-27）：
  - 根因定谳 = **测试缺口·拼接顺序契约**（非产品缺陷）：`def _qs_instr_detail` 定义于 `_QS_QMT_MODULE_HEADER`
    （`_assemble` 偏移 442）、`def _qs_progress_start` 定义于 `_QS_QMT_LOG_EXT`（497）；产品 `_assemble`
    （`source_import_qmt.py:2791`）保证二者恒早于后段模板（730/754/897）⇒ 真实产物自足；缺陷在测试 `_ns()`
    仅 exec `STOCK_INFO_EXT` ＋ `WRAPPER_ZONE`，未模拟该拼接顺序。
  - 修法（②审 A1=(a) 只改测试）：`_ns()` 按 `_assemble` 顺序补 exec `MODULE_HEADER` ＋ `LOG_EXT`，
    docstring 写明顺序契约来源；**产品零改动**（`quantstudio/**` 未触）。
  - 验收：目标 `24 passed`（基线 5 failed / 19 passed）；回归 `236 passed, 3735 deselected`；
    `qmt_wrapper_hash 0c842fddbba9` 未变；`kb_hook.validate_record(qd)` PASS。
  - 提交：`2bfea62`（3 文件 +67/−3）；沉淀 `QD-2026-0014`（qd 族·wrapper·D4·**已关单** status=closed）。
  - 回退点：`stash@{1} baseline-d24-20261026`。
- **统推 #8**：待推 6 笔（`4a31618`／`8b27d2f`／`a105866`／`f515263`／`7d76bcf`／`2bfea62`），执行窗 21:00，
  RULING=「2026-10-27 用户『两项全批』（统推批 #8）」；共享层 **1 文件**
  （`quantstudio/pipeline/qfq_reanchor_engine.py`＝B-1 行为面）⇒ 推送后同步门**不可豁免**，
  须含**黄金结果逐位对比**（复权口径面差异不接受「很小」放行）。
- **本线剩余**：T3 #8 出仓（21:00）／T5 API 69 条件逐条行为验证 Plan（待②审）／
  T6 QMT mirror 锚列枚举只读探针（待方法裁定）。
- **边界声明**：以上为本节点（会话节点①）可见范围，非全项目穷尽。


## M2 闭环快照（2026-10-27）

- **当前阶段**：M2 全段闭环入档（知识库制度层 M0–M2 收官）→ 下一节点＝**11-23 首月观察窗读数**（提醒 `schedule-905902f5` 在位，2026-11-23 10:00 CST）。
- **M2-①** 主仓 AGENTS R1/R4 落位 ✅（副本 diff=0）｜**M2-②** 全局层 R1/R4 落位 ✅（`~/.dsh/AGENTS.md` 17,794 B ＋ preset ⑧·本轮沉淀子项；V1–V8 全过；⑥生效确认）｜**遗留阻塞项＝无**。
- **已锁基线**：全局 AGENTS sha `205917a18a58f837`／preset sha `e8b417b6d78c5326`（回退＝`*.bak-20261026-seg2` 覆盖，全局层无 git 仓）。
- **未闭合 Issue**：无 M2 阻塞项；ops 族立族＝待触发（≥3 条同域）；A2 试点 2 首批落条＝待回执。
- **待推批**：`a105866`（段②证据件＋ops 候选）＋`f515263`（R4 落件 QD-2026-0012/0013）随 #8 出仓（我方不自行推送）。
