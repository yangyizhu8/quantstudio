# Guard pattern 精确匹配缺陷修复设计（防线包 #8，技术债 → 方案起草）

- 版本：v1.1（2026-08-29 实施完成待 Step 4 复核；总调度预审通过两条件已并入）
- 状态：草案——待总调度审计（六步第 1→2 步）
- 关联：issue_registry 技术债 #16 族（2026-08-24 双档立项）；AGENTS.md 写前快照纪律
- 证据档案：`docs/evidence/final-snapshot-20260822-briefing.md`（事故一/二）、
  `docs/handoff/baseline_20260821_0040_dispatch.md`（事故三：终极解决会话进程清理误杀）

## 1. 问题定义（三起事故实证）

| # | 事故 | 日期 | 机制 | 损失 |
|---|---|---|---|---|
| 1 | SNAP_003 周一窗 create REFUSED | 08-24 23:29 | **bash 裸提及误匹配**：两个 zcode 持久 shell 的 cmdline 内嵌历史命令文本（含 `run_cloud_sync` 字面量），bash.exe 不在 `SHELL_PROC_NAMES`（仅 powershell/pwsh/cmd）→ 走原子串匹配命中 | 一次窗口报废（总调度直跑补救） |
| 2 | 总调度守护 wait_until.sh 误触 | 同刻 | 守护脚本描述文本内嵌 pattern 字面量（`恢复两个wrapper…run_cloud_syn…`）→ 同机制 | 同上（同案双受害者） |
| 3 | 终极解决会话进程清理误杀 | 08-25 11:04 | **pattern 过宽清理**：按 `run_ptrade_strategy` 关键词全杀 python——误杀稳定化 verify r1（rc=-1 无 traceback 硬杀），产物误配连锁 | ~3h verify 重跑 + 归因成本 |
| 附 | pid 3 幻影 | 08-24 23:01/23:26 | `psutil` 枚举到 pid=3 的"python.exe"（不可读 cmdline）→ fail_closed 误计 | 两次 verify REFUSED 干扰 |

**共同根因**：guard 与周边清理工具均以 **cmdline 子串匹配** 为判据——文本自指（监控/守护/历史命令内嵌字面量）与宽关键词（清理范围失控）两类误伤同源。

## 2. 设计目标与红线

- 消除文本自指误匹配（事故 1/2），杜绝宽关键词误杀重演（事故 3 的工具侧约束），清除幻影进程（附）；
- **红线不变**：真实数据侧任务检测零降级（ps1/py/sh 包装器、python 本体、SYSTEM fail-closed、marker 归因、abort 语义、A-豁免边界）。

## 3. 修复设计（三层）

### 3.1 层一：shell 宿主族补全 + 扩展名锚定扩展（治事故 1/2）

```python
SHELL_PROC_NAMES = {"powershell.exe", "pwsh.exe", "cmd.exe",
                    "bash.exe", "sh.exe", "wsl.exe", "zsh.exe", "fish.exe"}
# 锚定检查扩为：pat+".ps1" / pat+".py" / pat+".sh" / pat+".bash"
```

- bash 家族命令文本裸提及不再命中；真实任务三形态（ps1/py/**sh** 包装器）全覆盖；
- zcode 持久 shell / 守护 wait_until.sh 描述文本 = 不含 `pat+".sh"` 锚（`wait_until.sh` 非数据侧名）→ 不命中。

### 3.2 层二：非 shell 进程词边界匹配（纵深）

python 等本体的匹配由子串改为**词边界正则**（`\b<pat>\b`）：
- `run_cloud_sync` 作为整词命中 `python ...run_cloud_sync.ps1` ✅；
- 内嵌于更长标识符（如 `my_run_cloud_sync_notes.txt`）不命中——降低文本自指面。

### 3.3 层三：幻影过滤 + marker 专责强化（治"附"+ 收窄面）

- **幻影过滤**：`pid < 10`（Windows 保留进程区：0=Idle/4=System）或 `create_time()` 不可得者不计入 fail_closed；
- **marker 专责**：QDB 域判定逐步从"pattern 白名单"向"marker 自声明"迁移（现有 v1.1 机制已有；本层仅将 `QDB_READ_ONLY_PATTERNS` 新增条目的准入标准改为"必须有 wrapper marker 或只读证据"——不再扩裸 pattern）；
- **工具侧约束（治事故 3）**：产出 `scripts/data_side_process_query.py` 统一查询接口（输出带 `matched_pattern`+pid+锚类型的 JSON），终极解决/守护工具**禁止自写进程扫描**、按接口结果+精确 pid 定点清理——宽度失控从源头消除。

## 4. 改动范围

| 文件 | 改动 |
|---|---|
| `scripts/governance_snapshot.py` | SHELL_PROC_NAMES 扩族 + 锚定扩 .sh/.bash + 词边界匹配 + 幻影过滤（~30 行） |
| `scripts/data_side_process_query.py` | 新增统一查询接口（只读） |
| `tests/test_guard_extension_anchor.py` | 测试矩阵扩充（§5） |
| 使用方文档（dispatch 纪律节） | 守护/清理工具改用统一接口的规约 |

引擎/快照 hash/锁/白名单语义零改动。

## 5. 测试矩阵（含三案例回归）

| # | 场景 | 期望 |
|---|---|---|
| U1 | `bash -c "...文本含 run_cloud_sync 字面量..."`（事故 1 复刻） | 不命中 |
| U2 | `bash /tmp/wait_until.sh 08-25 恢复 wrapper run_cloud_syn...`（事故 2 复刻） | 不命中 |
| U3 | `bash xxx/repair_minutes_wrapper.sh`（真实 sh 包装器） | 命中（.sh 锚） |
| U4 | `powershell -File run_cloud_sync.ps1` / `python ...run_sync_now.py` | 命中（回归不变） |
| U5 | python cmdline 含 `xxxrun_cloud_sync_notepy`（内嵌长词） | 不命中（词边界） |
| U6 | pid=3 幻影（mock psutil 枚举） | 不计入 fail_closed |
| U7 | `data_side_process_query.py` 输出 | 含 matched_pattern/pid/anchor_type，只读零副作用 |
| U8 | 既有 guard 全量（marker 10 + 锚定 12 + 豁免 2 + 3A 锁） | 全绿（零降级证明） |
| U9 | **词边界数值级断言**（总调度条件①，P-D14b 教训）：对每个 DATA_SIDE_PATTERNS 条目逐一构造 正例（含 pat 整词）/负例（`x{pat}x` 前后缀拼接/`{pat}_notes.txt`/`my_{pat}`）样例，断言 re 匹配命中数逐条等于预期（正=1/负=0），禁止仅跑通不核数 | 逐 pattern 数值断言全等 |
| U10 | **接口-guard 一致性断言**（总调度条件②）：同一进程快照下 `data_side_process_query` 输出 hits 与 `_data_side_tasks_running()` 结果（pid/matched_pattern/anchor 维度）逐一相等，防双实现漂移；实现约束=query 接口**复用** guard 枚举函数而非独立实现 | 逐字段相等 |

## 6. 验收标准

1. U1-U10 全绿（三事故回归 U1/U2/U6 + 数值级断言 U9 + 一致性断言 U10 必含）；
2. 生产活证：一次真实数据侧窗口（sync/repair）期间 query 接口正确识别（与 guard 拦截记录交叉核对）；
3. 等价性：空闲时段 `data_side_guard` 行为与修复前一致（无任务时放行不变）。

## 7. 回退条件

任一真实任务检测用例（U3/U4/生产活证）失败 → 回退本包（guard 恢复当前版本），事故 1/2 类误报退回"关键词纪律+拆分写法"人工防线。

## 8. 排期与实施状态

启动令后实施完成（2026-08-29）。U 矩阵 8/8 通过（U1-U7+U9/U10；U8 并入全量回归 34 passed）。
**残留面登记（U1 修正发现）**：bash -c 文本若含完整 `pat.ps1/.py` 字样，锚定无法与真实包装器
调用区分 → 保守命中（误报方向=延迟快照零损失，可接受）；事故实况为裸提及，已修复。
活证项：留待下次真实数据侧窗口交叉核对（与生产排期联动，不阻塞推送）。
