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