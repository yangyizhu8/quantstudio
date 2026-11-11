# D5 交付包 QFQ 出厂质量门 · 方案（2026-11-08 · 节点21）

> 依据：用户令（2026-11-08 15:06）裁-3 甲 —— E3–E6 与 R-A/R-B 归节点21；⑱ 侧 E3–E6 已撤回，其仅保留 D1/D4。
> 前置：⑪ 探查三缺口 R-A（空库雷）/R-B（写死开发机绝对路径）/R-C（无出厂检验）＋ 裁-1 乙（保持 `released=false` 走 legacy ＋硬门止血）。
> 六步声明：本件是**方案件（①）**，呈②审。**接入打包链的代码改动一律不在本批落地**（须走 ③→④→⑤→⑥）。

## 一、问题（为什么客户侧会静默错价）

客户复权还原依赖**两个独立源**：

```
qfq_price = raw_price × (当日 adj_factor  ← 包内 aux 库（分子）
                       / latest adj_factor) ← 主库/云端（分母）
```

两源不同锚 ⇒ 价格静默错配，**不报错**。三条现实缺口：

| 编号 | 缺口 | 实测（2026-11-08） |
|---|---|---|
| R-A | 世代库（gen1）为**空库**却可能被路由命中 | 包内 `released=false` 时走 legacy 合规；一旦置 `true`，路由切到 gen1，而其 **6 表全 0 行** ⇒ 因子空 |
| R-B | 记录里写死**开发机绝对路径** | `qfq_source_cutover.aux_db_path` 存我方 Windows 绝对路径；客户侧 D5-a/D5-b 守卫会 fail-closed（宁缺不错），但若不拦则形态畸变 |
| R-C | 无**出厂检验** | 打包后无任何机检证明「客户能正确复权」；靠人工 SOP |

## 二、R-B 核证（本轮完成，回证候裁）

**结论：manifest 侧的 `aux_db_path` 字段在运行时不被读（裁乙方向成立）；但存在第二消费面需一并治理。**

| 证据 | 位置 | 读的是什么 |
|---|---|---|
| 统一运行时路由 | `quantstudio/pipeline/qfq_aux_router.py:231-288` `resolve_runtime_aux_path()` | ①客户 `qfq_aux_paths.json` 的 `released` + `generations`；②主库 `qfq_active_cutover` JOIN `qfq_source_cutover` 取 **`source_generation`**（不取 `aux_db_path` 列） |
| 打包验证 | `quantstudio/delivery/verify.py:340-401` `_assert_generation_consistency()` | 调上述同一函数 ⇒ **不读 manifest 的 `aux_db_path`**；改为校验「客户将读的路径必须落在 DATA_ROOT 且包内有同名件」 |
| 第二消费面 | `qfq_resident_orchestrator.py:216/240-243`、`qfq_aux_router.py:67-76/145-160` | **读**记录列 `aux_db_path`（`explicit_default`），且对异平台绝对路径 **fail-closed 抛 `AuxRouteError`** |

⇒ **建议裁乙**（文档说明 + 口径卡透出），但附加一条硬约束：
**交付包内主库记录的 `aux_db_path` 必须是相对路径或客户可解析路径；否则客户一激活即 fail-closed**。
该约束本轮已落为检查项（`G5`），不依赖客户读文档。

## 三、方案组成（E3–E6）

| 件 | 交付物 | 形态 | 本批状态 |
|---|---|---|---|
| **E3** | `scripts/verify_qfq.py`（随包） | 客户环境独立跑；V1–V6 六判据；PASS/FAIL + JSON；任一 FAIL 阻断启用 | ✅ 骨架落地＋4 形态冒烟通过 |
| **R-A 硬门** | `scripts/qfq_release_gate.py` | 只读检查器：G1–G5；产出 `qfq_quality_certificate.json`；3 负向 + 1 正向演练全通 | ✅ 工具落地（**未接入打包链**） |
| **E4** | 口径卡（一条命令输出 cutover_id/price_source/source_generation/各版本/released/包生成时点/因子最大日期） | 复用 `qfq_cutover.py` 查询 + 包内 `qfq_aux_paths.json` | ⏳ 待 D6 |
| **E5** | 出厂证书流水线（打包链末跑 E1+E2+E3+QualityAudit QFQ 段 → 证书） | 常态硬门；**接入打包链＝六步** | ⏳ 待 D7（本件即其前置方案） |
| **E6** | 降级策略（aux 解析失败/因子缺失/跨源不一致 ⇒ 显式分支） | 甲=拒绝还原并告警（宁缺不错）／乙=写入并标 `qfq_unverified` | ⏳ 待 D8（候裁） |

## 四、R-A 硬门设计（拟接入，须六步）

**接入点**：打包链末端（导出完成、发布前）执行 `qfq_release_gate.run_gate(pkg)`：

```
E1 导出完整性 → E2 跨层一致性 → E3 客户自检（对包跑一次）→ QualityAudit(QFQ 段)
      → qfq_release_gate(G1-G5) → qfq_quality_certificate.json
      → verdict=PASS 才允许发布；FAIL 即拒绝发布（exit 1）
```

**五条判据**

| 码 | 判据 | 级别 |
|---|---|---|
| G1 | 包内主库存在 | FAIL |
| G2 | `released` 可判定（缺文件/坏 JSON ⇒ 按 false + WARN） | WARN |
| **G3** | **released=true ⇒ 世代库存在且 `adj_factor`/`fund_adj` 行数 > 0**；released=false ⇒ legacy `qfq_aux.db` 存在且非空 | **FAIL（R-A 雷）** |
| G4 | released=true 但主库无 active cutover ⇒ 拒发（否则客户以为在用 gen1 实则 legacy） | FAIL |
| G5 | 记录 `aux_db_path` 不得为异平台绝对路径 | FAIL |

**负向用例（本轮实测全通）**

| 用例 | 期望 | 实测 |
|---|---|---|
| released=true + 空 gen1 | FAIL(G3) | ✅ |
| released=false + 空 legacy | FAIL(G3) | ✅ |
| released=true + gen1 文件缺失 | FAIL(G3) | ✅ |
| released=false + legacy 有数（当前交付形态） | PASS | ✅ |

## 五、分批与边界

- **本批（已落，不改生产核心）**：两个只读脚本 + 本方案件 + 演练证据；SOP 检查项可即刻使用。
- **下批（须六步）**：把 gate 挂进打包链（改打包入口 = 生产核心）；`verify_qfq.py` 纳入交付包清单；证书纳入发布门。
- **不碰**：`mcp_adapter L276-288`、`verify.py` 路由逻辑、`qfq_aux_paths.json` 的 `released` 取值（裁-1 乙：保持 false 走 legacy）。
- **回退**：两个脚本均为纯新增只读件，删除即回退；不产生任何数据写入。

## 六、知识反哺

`知识反哺：[无命中]` 检索面＝`QuantStudio/knowledge/**`（按 families/domain 过滤：pipeline/delivery/qfq）+ `knowleage_base` 对应域 MOC（只读）。未取到针对「交付包出厂检验 / 双源锚一致性」的现成条目。
`本轮沉淀：N/A（理由：本批为新增只读工具与方案件，未出验收结论；验收批按 R4 补沉淀）`
