# A4 框架层变更 · 实施后报告（停点版）

> **状态**：**框架层修复已完成，停在同步门**。**未 stage／commit／push／同步**。
> **对账采样**：**2026-11-18T07:26:15Z**（size／sha256／mtime 三件套**同刻齐打**）
> **依据**：审核结论「A4 本地实施和验证已完成，停在框架同步门」＋ 本轮工作指示。
> **边界遵守**：未改 `local_only_symbols` / `signatures` / Validator；未跑平台 D5／订单；未解除 A5／T2；未 Git 任何操作。

---

## 一、五件改动文件（含前后指纹）

| # | 文件 | 性质 | 改前 | **改后（实测）** |
|---|---|---|---|---|
| 1 | `skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json` | **改** | 33,586 B／`1b4b11ff4ac6e033432ef8e1508076030a8b7bac88c6c08b872f1497edff3b37` | **35,155 B／`2f9ee610dc57db31ac15a30b357119f23fa4d2b4fc823edee1fd721d074437a0`**（mtime 11-18 09:55） |
| 2 | `…/a4-j4-fixed-sample/design.json` | **新增** | — | 500 B／`342d4fd507904c79daf9…`（11-18 09:53） |
| 3 | `…/a4-j4-fixed-sample/source.py` | **新增** | — | 72 B／`a84638ae84ddfb6eb2fd…`（11-18 09:53） |
| 4 | `…/a4-j4-results/before.json` | **新增** | — | 4,852 B／`4aa08a62bbcf12037806…`（11-18 09:53） |
| 5 | `…/a4-j4-results/after.json` | **新增** | — | 4,852 B／`4aa08a62bbcf12037806…`（11-18 10:01） |

**git 实测状态（零 staged 确证）**：

```
 M  skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json
??  docs/evidence/…/a4-j4-fixed-sample/design.json
??  docs/evidence/…/a4-j4-fixed-sample/source.py
??  docs/evidence/…/a4-j4-results/after.json
??  docs/evidence/…/a4-j4-results/before.json
```

⇒ `M` 位于**第二列**（worktree modified，**未进 index**）；`??` 为未跟踪 ⇒ **index 为空，无任何 staged 内容**。

## 二、S-1 保全链（**已实证齐备**）

| 文件 | 读数 | 说明 |
|---|---|---|
| `.git/a4-pre-write/20261117/ptrade-api-signatures.pre.json` | **33,586 B／`1b4b11ff4ac6e033…`**／mtime **09-08 15:50** | **完整原字节**；mtime 保留原值 ⇒ 系**字节级复制**而非重写 |
| `.git/a4-pre-write/20261117/pre-fingerprint.json` | 223 B／`bbc221a3…`／11-18 09:54 | **S-2 立验记录** |

⇒ 回退链（S-1 保全／S-2 立验／S-6 前置闸／仅反向差量恢复）**具备可执行条件**；`.git/` 不被 git 跟踪 ⇒ 不入提交范围。
⚠️ 上轮我方曾申报「S-1 保全链未独立核验」，**本轮已核，结论为齐备**。

## 三、行为契约（判定零翻转）

| 判据 | 要求 | **实测** | 结论 |
|---|---|---|---|
| **J-1** | `len(local_only_symbols) == 27` | **27** | ✓ 不变 |
| **J-2** | `"is_trade" in local_only_symbols == True` | **True** | ✓ 不变 |
| **J-3** | `"is_trade" in signatures == False` | **False** | ✓ 不变 |
| **J-4** | 三规则均 BLOCK ＋ before/after 完整报告 SHA 相等 | 见 §四 | ✓ 达成 |
| **J-5** | 键集合 = 原 8 键 ∪ {`is_trade_doc_basis`}，无其他增删 | **9 键**，新增键类型 **`str`** | ✓ |
| **旧 8 键逐值** | 逐键值 sha256 不变 | **8/8 不变**（含 `set_backtest_is_trade` 原 T3 逐字保留） | ✓ |

**契约定性**：新增键为**纯 provenance 登记**——`runtime_corrections` 在本仓**无任何 Python 消费者**（五面 grep 实证），故：
- **无判定路径变化**（Validator 输出逐字节一致，由 §四 双 SHA 相等证明）；
- **无性能影响**（加载期解析一次，不在回测热路径）；
- **无精度影响**（不参与任何计算）；
- ⇒ **纯增益·元数据增量型**（铁律 18 三审计全否）。

## 四、J-4 实测取证

| 项 | 实测 |
|---|---|
| 固定样本 | `design.json` 500 B ＋ `source.py` 72 B（含 `engine_profile`＋`timing` 及 `is_trade()` 调用） |
| before 报告 | 4,852 B／`4aa08a62bbcf12037806…` |
| after 报告 | 4,852 B／`4aa08a62bbcf12037806…` |
| **双 SHA 相等** | **True**（逐字节相同） |
| `PTRADE-LOCAL-SYMBOL` | **出现 2 次**（`is_trade`＋`set_backtest` 各一） |
| `PTRADE-DESIGN-LOCAL-API` | **出现 1 次** |
| `TARGET-LOCAL-EXTENSION-BAN` | **出现 2 次**（`is_trade`＋`set_backtest`） |

⇒ **三规则全部实测 BLOCK**，与第 5 轮独审「set_backtest 再触发 L762/L1005 各 1 次」的静态推演**完全吻合**。
⇒ **J-4 状态由「未验证」转为「实测达成」**。

## 五、测试证据（**来源须如实标注**）

| 项 | 内容 |
|---|---|
| 读数 | **82 passed** |
| **证据来源** | **审核方本轮回执**（非我方实测） |
| 我方是否重跑 | **否**——本轮为「停点整理报告」，非重跑指令；且「不重跑已通过的 J4」 |
| 一手证据缺口 | **我方无本地 junit／stdout 留档**。若需一手证据，须授权跑一次并落 `docs/evidence/`（**我方未擅自跑**） |
| 影响评估 | 不影响 A4 结论——A4 的判定面由 J-1～J-5 与双 SHA 相等独立封存，不依赖该测试读数 |

## 六、文档状态

| 对象 | 现状 |
|---|---|
| **A4 方案正文** | `a4-is-trade-doc-basis-light-plan-20251117.md` **18,676 B／`563a3354827d48849b94f254eefe974a4e13f0de8c5a73ada4a6c6fdde97494a`**（mtime 11-18 09:25）—— **本轮未改动** |
| 第 5 轮独审回执 | `a4-zcode-review-receipt-r5-20251118.md` 9,411 B／`fecc097d…` |
| 本报告 | 本件 |

### 6.1 四条非阻塞补强 → **登记为文档一致性待办**（本轮**未**合入方案正文）

| # | 待办项 | 目标位置 | 评审方定性 |
|---|---|---|---|
| **D-1** | C-3 补时序句「before run 于 **S-2 立验通过后**、A4 写入前执行；紧前实测目标 JSON sha256 == `1b4b11ff…b37`，不符即停」 | 方案 §六 C-3 | 非阻断 |
| **D-2** | E-2 判定改**四点小指纹**（目标 JSON sha＋两条 alignment 候选路径不存在性＋两样本 sha） | 方案 L130 | 非阻断 |
| **D-3** | S-6① status 检查限定 `-- 目标JSON`；`a4-j4-*` 列入**本批白名单** | 方案 §7.1 S-6 | 非阻断 |
| **D-4** | 显式加一步「**先按 A-3 路径落盘两个固定样本**」 | 方案 §六 | 非阻断 |

**合入条件（据本轮指示）**：若纳入方案正文 ⇒ **更新后重新登记方案 SHA**，**不开新审轮**，**不重跑已通过的 J-4**。
**当前状态**：四项**均未合入**（关键词「S-2 立验通过后」「小指纹」「本批白名单」「先落盘」实测全 MISSING）⇒ 方案 SHA 不变，**本次报告与 18,676 B 版对账一致**。

## 七、仓外消费者未覆盖边界（**必须随报告声明**）

| 项 | 结论 |
|---|---|
| 已核范围 | `quantstudio/`（0）、`skills/`（1，仅 JSON 自身）、`scripts/`（0）、`tests/`（0）；`validate_agent_strategy.py` **不读** `runtime_corrections`；`validate_ptrade_portability.py` **0** |
| **可声称** | **「已读消费者段未见额外键拒绝」** |
| ⚠️ **不可声称** | ❌「本仓绝无手写校验」——关键词零命中不能排除**非关键词形式**（硬编码键白名单／`len()` 断言／显式 `keys()` 比对） |
| ⚠️ **更不可声称** | ❌「必然不会拒绝」——**无拒绝处理 ≠ 已被证明不会拒绝** |
| **未覆盖面（明确列举）** | ①**仓外消费者**（其他仓库／客户部署／外部脚本）②**未来新增消费者**③**运行时动态读取**（反射／配置驱动）④**非已读段的其余引用文件** |
| 落地后义务 | 若后续发现仓外消费者，须按 `str` 读取；如需严格 schema，须另行核 `additionalProperties` 类约束 |

## 八、四项责任人（A4 框架层变更 owner 应答，**拟认领·待用户确认**）

业务 owner 询问四项角色归属。我方作为 A4 框架层变更 owner 应答如下（**认领不等于任命；最终以用户确认为准**）：

| 角色 | 拟认领方 | 职责边界 | 说明 |
|---|---|---|---|
| **实施报告责任人** | **本会话（节点⑦）** | 本报告及后续事实陈述、指纹对账、如实申报 | A4 实施与取证由本线完成，责任随产出 |
| **文档维护责任人** | **本会话（节点⑦）** | A4 方案正文、证据件、四条补强 D-1～D-4 的合入与 sha 重登记 | ⚠️ 方案正文历史版本曾由他线修订（U168/U169/U170），**后续如他线并行修订须先约定唯一写者**，避免双写冲突 |
| **测试验收责任人** | **审核席（ZCode 独审通道）＋ 用户终审** | 82 passed 的验收归属、J-4 判据复验、独审回执 | 四次独审均由该通道出具；**82 passed 的我方一手证据缺口需其补全或授权我方补跑** |
| **GitHub 同步批准人** | **用户本人** | ⑥ 双仓推送＋trading 同步门的放行 | 依铁律 01／14，⑤用户确认不可委派；**本轮不发生任何同步** |

## 九、停点声明

| 项 | 状态 |
|---|---|
| 框架层修复 | **完成**（本地） |
| stage／commit／push | **零** |
| GitHub 同步 | **未发生**，且**须等用户 post-repair confirmation** |
| `local_only_symbols`／`signatures`／Validator | **未改** |
| 平台 D5／订单 | **未跑** |
| A5／T2 | **未解除**（T2 未证，BLOCK 仍在） |
| 平台最终④ | **未通过** |
| 同步前置 | **用户明确 post-repair synchronization confirmation ＋ 相关文档齐备** |
