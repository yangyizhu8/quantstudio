# PD-0006 验收证据：qs-deploy run_daily 源形态支持（六步④验收）

> 对应方案：`docs/ptrade-deploy-rundaily-design.md`（①②已过：方案 2026-10-11 落盘、总调度②审通过「方案（5 文件纯增量/D3 单挂载 fail-closed/D2 时点重挂有文档依据/知识反哺留痕）②审通过」）。
> 实施快照：`30f0caf585e8a2d5b1713a46261aff3d008ddd8c`（stash stored，写前快照纪律）。
> 状态：④验收 PASS（本文件）→ ⑤用户确认 ✅（2026-10-15 批准，与 M8 同批）→ ⑥推送（dev 统推候令）。

## 1. 实施明细（方案 §4 五文件 + 固件 + 测试）

| 落点 | 内容 | 形态 |
|---|---|---|
| `profiler.py` | `_run_daily_mounts()`：initialize 直接语句级挂载识别（D1 保守：Name 引用/str 时点/缺省 9:31 文档默认）；profile 新键 `run_daily_mounts`；多挂载 warning | 纯新增 |
| `config.py` | P2 放宽（恰 1 挂载合法）+多挂载拒绝（D3）+>4 预算断言（平台 ≤5 上限）+**混合形态显式拒绝**（实施中新增，见 §3） | 条件放宽+新分支 |
| `transformer.py` | `_apply_mount(profile)` 第二输入：挂载目标函数体原样内联+**源注册语句定点移除**（0 处即硬失败）+部署注册注入（源时点 provenance 记录） | 新分支；handle_data 路径零改动 |
| `equiv.py` | 第 5 类白名单：`_source_run_daily_targets` 源侧识别+`_strip_source_registrations` 源侧对称剔除+挂载目标→handle_data 名字映射+签名白名单+extra 方向豁免 | 白名单扩展 |
| 固件 | `fixture_rundaily_strategy.py`（周频挂载形态）+`deploy_rundaily.yaml` | 新增 |
| 测试 | `test_deploy_rundaily.py` **10 用例**（识别 3/P2 边界 3/P3 挂载 1/端到端 2/混合形态 1） | 新增 |

runner 无需改动（profile dict 全程透传，P2/P3/P4 直接消费新键）。

## 2. 验收标准逐项（方案 §7）

| # | 标准 | 结果 |
|---|---|---|
| 1 | 新固件+6-8 用例 | ✅ 10 用例（超出：混合形态边界用例追加） |
| 2 | hv_tech_etf_mvo 真实产物 7 阶段全 PASS（P4 等价零回退） | ✅ P0→P6 全 PASS（原 P2 blocked 复现件转正） |
| 3 | 既有 25 用例全绿 | ✅ 25/25（含 1 处断言演进放宽：`test_main_window_registration_ast` `==10`→`>=10`，归因他线 M5.2 件6 追加 row==10 tab 的合法叠加，锚定改为 deploy 项存在性+位置，断言语义不弱化） |
| 4 | V6 池补齐 4/4 | ⚠️ **口径修正**：tech_etf_mvo ✅ PASS + fall_reversal ✅ PASS；canslim/vol_regime **非本方案范围 FAIL**——见 §3 归因（他线源形态演进） |
| 5 | CONTRACT GATE 零干扰 | ✅ `check_fund_matrix.py --check` 哈希一致（本地快验；CI 全量随推送批） |

**deploy 套件终态：35/35 全绿**（新 10+既有 25，1.65s）。

## 3. V6 池 canslim/vol_regime 归因（他线演进，非本方案回归）

**现象**：两策略 P4 FAIL（26 处 diff：initialize 错位+daily_logic 函数体 mismatch）。

**取证**：源文件（`output/ptrade_export/`）已被他线在途 convert-pipeline 重写为**混合形态**——`handle_data` 空操作 stub（docstring「引擎每 bar 调用…此处空操作」）+ `run_daily(context, daily_logic, time=…)` 真逻辑挂载并存。M7 验收时两策略为纯 handle_data 形态（PASS 基线），源演进在 M8 后发生。

**处置（实施中增强）**：P3 会对混合形态选错挂载对象（包装 stub 而非真逻辑）→ 原 P4 隐晦拦截（26 处错位 diff，可诊断性差）。本实施在 P2 增加**混合形态显式拒绝**（「混合形态暂不支持——挂载对象歧义；请用纯 handle_data 源或纯 run_daily 源」）+ 负向测试 `test_p2_hybrid_form_explicitly_rejected`。fail-closed 行为正确保留（防双驱动坏产物：_deploy_daily_exec 空操作+源挂载照跑）。

**边界声明**：混合形态支持待上游 convert-pipeline 形态稳定后**单独立项**（vault 登记观察项）；本方案 D3 边界（纯单挂载源）不变。

## 4. 平台文档依据（先查文档铁律）

依据 Context7 `/kay-ou/ptradeapi`（Ptrade山西.md「run_daily-按日周期处理」，2026-10-11 查询）：
- `run_daily(context, func, time='9:31')`，func 须 (context) 单参——包装签名 (ctx, data=None) 与挂载调用兼容；
- 仅 initialize 可调；run_daily+run_interval 累计上限 5（>4 预算断言依据）；
- 日线回测周期无论 time 值恒 15:00 执行、交易中 00:00~23:59 可控——D2 时点重挂（源 15:00 回测语义→档位 14:50 尾盘）与 handle_data 源同近似的推论依据。

## 5. 知识反哺检索留痕（grep-able）

```
知识反哺：[命中] 小市值ETF动量大白马策略（01-策略/小市值ETF动量大白马策略.md，2026-10-11）
```

采纳：错峰多挂载实践 → D3 单挂载限制+≤5 预算断言设计。（方案阶段已留痕，本节回执确认。）

## 6. 六步进度

①方案 ✅（2026-10-11）→ ②审计 ✅（总调度通过，无修订项）→ ③实施 ✅（本文件 §1）→ ④验收 ✅（本文件）→ ⑤用户确认 ✅（2026-10-15 批准，与 M8 同批）→ ⑥推送（dev 统推候令，勿单独 push）。
