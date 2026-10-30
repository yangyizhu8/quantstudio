# PD-0014 方案：qs-deploy GUI「打开输出目录」按钮（参考导出 PTrade 页同名实现）

> 立项依据：2026-10-30 用户实盘使用反馈（截图）——转换 PASS 后需手工在资源管理器定位
> `Demo_account_Real_account/<策略名>/`，不便；要求参考「导出 PTrade」页同名按钮补齐。
> 状态：①方案（待②审计）——**未实施、未提交**。

## 1. 问题定义

| 项 | 现状 |
|---|---|
| 转换成功后产物落 | `Demo_account_Real_account/<策略目录名>/`（三件：`*_trade.py` / `report.md` / `runbook.md`） |
| GUI 定位手段 | **无**——用户须手工开资源管理器逐级找目录（用户截图实证） |
| 同类页先例 | 「导出 PTrade」页已有「打开输出目录」按钮（`ptrade_export_tab.py:201` + `_open_out_dir` :383-398） |

## 2. 设计（对等移植 + 最小适配）

### D1 按钮与交互（逐条对齐参考实现）

| 参考行为（`ptrade_export_tab._open_out_dir`） | 本页移植 |
|---|---|
| 无目录 → `InfoBar.info("无输出目录", "请先执行一次转换")` | 相同 |
| 目录不存在 → `InfoBar.error("输出目录不存在", str(d))` | 相同 |
| `os.startfile(str(d))`（Windows only，noqa S606） | 相同 |
| 打开失败 → `InfoBar.error("打开失败", f"{d}: {e}")` | 相同 |

**目录来源**：不新增状态变量——`_on_finished` 已收到 `d["artifacts"]["product"]`（绝对路径），
取其 `Path(...).parent` 缓存到 `self._last_out_dir`；失败（`_on_error`）不清空（保留上次成功目录，可再开）。
P6 SKIPPED（FAIL 链）时 artifacts 为空 → 不更新缓存 → 按钮走「无输出目录」提示分支。

### D2 放置位置

快捷页 `row3`（一键转换行）与高级页 `row2`（开始转换行）**各加一枚**同名 `PushButton`（非主按钮），
紧邻主按钮、`addStretch(1)` 之前——两页各自完成转换后都能一键定位（共享同一 `self._last_out_dir`）。

### D3 双端对等判定

本功能为 **GUI 壳便利动作**（打开资源管理器），不产生任何工件、不影响产物 SHA——
按铁律「新功能模块添加：双端对等」第 3 条反例（无终端等价动作可对等：CLI 侧 `start <dir>` 属
shell 行为非产品功能），**判定双端对等条款不适用**；内核（`run_deploy`）**零改动**。

## 3. 改动范围

| 文件 | 改动 |
|---|---|
| `quantstudio/gui/tabs/deploy_tab.py` | ①快捷页 row3 + 高级页 row2 各加 `PushButton("打开输出目录")` → `_open_out_dir`；②`__init__` 加 `self._last_out_dir: Optional[Path] = None`；③`_on_finished` 从 `artifacts["product"]` 派生并缓存 |
| `tests/test_deploy_quickmode.py`（GUI 冒烟节） | 加 1 用例：按钮存在 + 未转换时点击状态栏提示「请先执行一次转换」（不真开资源管理器——mock `os.startfile` 或仅验证提示分支） |
| 条目 | `PD-2026-0014`（kb_draft 起草 + kb_hook 校验） |

**不动**：`preflight/transformer/equiv/gates/runner/deliver/quickmode`（内核七段零 diff）、
参考页 `ptrade_export_tab.py`（只读参照）。

## 4. 验收标准（5 项）

1. 快捷页与高级页**各有一枚**「打开输出目录」按钮（`tab.findChild` 实测）；
2. 未转换时点击 → 提示「无输出目录：请先执行一次转换」（不抛异常不静默）；
3. 转换 PASS 后点击 → `os.startfile` 以**产物所在目录**被调（测试以 monkeypatch 断言实参
   = `Demo_account_Real_account/<策略名>` 的绝对路径）；
4. **零行为变更**：deploy 套件全绿（110+新增）、`ptrade_deploy/` 零 diff、GUI 冒烟既有用例不变；
5. 族机检全绿（含新增条目）。

## 5. 影响面 / 风险 / 回退

| 项 | 评估 |
|---|---|
| 转换行为 | **零变更**（纯 UI 壳，不触内核） |
| 跨平台 | `os.startfile` 仅 Windows——与参考实现一致（项目当前 Windows-only 部署面；Linux/macOS 分支留待有需求另立） |
| 风险 | 极低：目录不存在/打开失败均有用户反馈（对齐参考实现三态） |
| 回退 | 单文件 revert（deploy_tab.py + 测试各一处） |

## 6. 三型判定

**新增能力型**（GUI 新增便利能力；不改任何既有 verdict 与产物——纯增益）。

## 7. 知识反哺检索留痕（R1）

```
知识反哺：[无命中] PD 族检索（打开输出目录/open_dir/os.startfile/GUI 按钮），2026-10-30
知识反哺：[无命中] QD 族检索（打开输出目录/open_dir/os.startfile），2026-10-30
知识反哺：[豁免] 通用库 knowleage_base——GUI 便利功能无对应域 MOC（只读确认，不回写），2026-10-30
```

> 采纳参考：**本仓代码先例** `ptrade_export_tab.py:383-398`（三态反馈：无目录/不存在/打开失败均
> 有 InfoBar 提示，禁静默）——这是比条目更直接的活参考，逐条对齐移植。

## 8. 排期

①方案（本件）→ ②审计 → ③实施（单文件 + 1 测试，≈半小时）→ ④验收（5 项）→ ⑤确认 → ⑥随批统推。
