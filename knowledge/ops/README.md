# OPS 族知识条目库（运维探针与守护链域）

> **立族**：2026-11-12 总调度批六十六裁定（A-4＝甲，我方起草 / ⑥ 复核族 schema 后落定）
> **登记**：`knowledge/kb-info.yaml` families 段 `id: ops`
> **立族动因**：守护链复活能力实施轮与 observe 观察期产出 **E1–E4** 四条经验，现有三族均无适配面：
> qd＝QMT 转换管线；pd＝PTrade 转换/部署；db＝数据库引擎与数据完整性（适用面明确限定「写入路径/约束/巡检守卫/制品判据/版本升级」，**不含进程运维**）。
> `db` 族 README §1 早已预告「**ops 族（候选）＝运维探针（daemon 可观测/告警通道）**；数据库引擎≠进程运维，不重叠」——本族即该候选转正。

## 1. 适用范围（与既有族边界）

**进程/任务侧的运维探针、守护链、启停生命周期与可观测教训**。判据：`fix_target` 落在

- 外科巡检/探针脚本（`scripts/check_*.ps1`、`check_*.py`）
- 计划任务定义与触发器（`schtasks`、`QS_Daemon*`）
- daemon 生命周期与占用声明（`daemon_occupancy.json`、启停/接管/锁）
- 自动恢复机制（拉起/重试/自愈）
- 心跳/告警/留痕通道（`daemon_heartbeat_alerts.log`、`daemon_revival.log`）

### 族边界（互不重叠，机检口径）

| 对比族 | 边界一句话 |
|---|---|
| **vs db** | db＝数据库引擎行为与数据完整性（写入路径/约束/巡检守卫/制品/版本升级）；**ops＝进程与任务运维**。同一份「巡检脚本」若其判据是 DB 约束（如 PK 计数）⇒ db；若是进程存活/心跳/拉起 ⇒ **ops** |
| **vs qd** | qd＝QMT 转换管线域（wrapper/matrix/portability）；与进程运维无关 |
| **vs pd** | pd＝PTrade 转换与部署；与进程运维无关 |
| **vs iron-rules 库** | 铁律完整版归档不入 families / 不入 R4 口径；**本族收录的是可复用的运维教训条目，不含铁律条文本身** |

> **判定口诀**：**改的是「库里的数据/约束」⇒ db；改的是「跑着的进程/任务」⇒ ops。**

## 2. Schema（六必填字段，同 qd/pd/db）

`symptom / root_cause / fix_target / evidence / fixture / trigger`

- `trigger` 两段式：`<复现条件>；问法：<口头问法>`
- 六字段任一缺失 ⇒ `kb_hook.validate_record` 不过 ⇒ **不落盘**（机检门）

### 2.1 fix_layer 五枚举（独立于 QD 六 / PD 九 / DB 五；多落点 `+` 连接）

| 枚举 | 语义 | 典型落点 |
|---|---|---|
| **probe** | 只读探测/巡检：读数采集方法、测点选择、时间窗过滤 | `check_daemon_alive.ps1` 的 H1/H2/H3 判据、`check_passthrough_pk.py` |
| **guard** | 闸门/拦截：阈值、冷却、占用声明、旁证、fail-closed | F2 阈值闸 / F3 占用闸 / F4 冷却闸 / F11 四探 |
| **lifecycle** | 生命周期状态机：启停、触发器、锁、接管、占用窗 | `QS_DaemonForever` 三触发、`daemon_occupancy.json`、`.daemon.lock` |
| **recovery** | 自动恢复：拉起、重试、自愈、自证 | `schtasks /Run` 拉起路径、F6 拉起后自证、`REVIVED` 判定 |
| **observability** | 可观测：日志/心跳/告警/留痕/退出码语义 | `daemon_revival.log` 结构化行、`[REVIVED]` 入告警流 |

- `fix_layer_multi: true`——多落点以 `+` 连接（如 `guard+recovery`、`probe+observability`），组件须在枚举内。

### 2.2 判层关键词（`kb_draft.py` 缺省自动判层；顺序命中即定）

```yaml
probe:         ["探针", "巡检", "check_", "探测", "只读", "读数", "测点"]
guard:         ["闸", "守卫", "拦截", "SKIP", "防误", "阈值", "旁证", "occupancy", "占用声明"]
lifecycle:     ["daemon", "生命周期", "启停", "schtasks", "计划任务", "触发器", "锁", ".lock"]
recovery:      ["复活", "拉起", "自愈", "REVIVED", "重试", "重启", "恢复"]
observability: ["日志", "心跳", "告警", "留痕", "可观测", "退出码", "heartbeat"]
```

> 共 38 个关键词。关键词只是**辅助判层**（缺省用），**不作判域主依据**；最终以 `fix_target` 实际落点为准。

## 3. 条目模板（`OPS-YYYY-NNNN.md`）

```markdown
# OPS-YYYY-NNNN <现象一句话>

- **symptom**：
- **root_cause**：
- **fix_target**：<文件:行 或 描述>
- **evidence**：<主仓路径#锚点>
- **fixture**：<复现固件/测试路径>
- **trigger**：<复现条件>；问法：<口头问法>
- **fix_layer**：<枚举>（多落点 `+`）
- **domain**：D1|D2|D3|D4|D5

## 详情
（现象展开 / 根因机制 / 修复实现 / 验证读数 / 边界与未闭合）
```

## 4. 落位与机检

- **落位**：`knowledge/ops/OPS-*.md`（`entry_glob` 机检）
- **编号**：`OPS-YYYY-NNNN`（`id_pattern`）
- **起草**：
  ```bash
  python scripts/kb_draft.py --kb knowledge/ops \
    --symptom "<现象一句话>" --root-cause "<根因一句话>" \
    --fix-target "<文件:行>" --evidence "<证据路径#锚点>" \
    --fixture "<固件路径>" --domain D3 [--layer <枚举>] [--json]
  ```
  ⇒ 经 `kb_hook.validate_record` **按 ops 族 schema** 校验；**不过不落盘**。
- **不重复登记**：同一条经验已在 Obsidian 主库（`12-客户运维/案例卷宗` / `已知错误`）登记的，本族条目作**机读版**并互相指针，不复制全文。

## 5. 域编码（D1–D5）

复用 `knowledge/qmt/README.md §2.1` 语义；运维类条目通常落 **D3（运维与缺陷）**，判据/探针口径类可落 **D1**，性能相关落 **D4**。

## 6. 首批待落条目（E1–E4，本族立族动因）

| ID | 条目 | 建议固定层 | 域 |
|---|---|---|---|
| E1 | R3b「自停未声明」误拉形态 + F11 旁证四探 | `guard+probe` | D3 |
| E2 | 复活三段式闸（阈值/占用/冷却）+ observe 渐进上线 | `guard+recovery` | D3 |
| E3 | 「全表行数 vs 当日分区」测点混用陷阱 | `probe` | D3 |
| E4 | BIGINT epoch 毫秒时间列不可 `CAST AS DATE`（按 YYYYMMDD 过滤得假 0） | `probe` | D3 |

> 上述四条待 ⑥ 复核族 schema 后，逐条走 `kb_draft.py` 机检门落盘。

## 7. 变更记录

- **2026-11-12 立族（草案）**：总调度批六十六 A-4＝甲；`kb-info.yaml` families 增 `id: ops`（dir/glob/id_pattern/五枚举/multi=true/38 判层关键词）；本 README 为族 schema 正本。**状态：待 ⑥ 复核族 schema 后落定**。
