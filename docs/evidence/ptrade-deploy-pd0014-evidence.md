# PD-0014 验收证据：qs-deploy GUI「打开输出目录」按钮（2026-10-30）

> 方案：`docs/ptrade-deploy-opendir-design.md`（②审通过：判型=新增能力型、D3 双端对等不适用照准）
> ③实施：`quantstudio/gui/tabs/deploy_tab.py` 单文件 + `tests/test_deploy_opendir_button.py`（5 用例）
> 本轮沉淀：PD-2026-0014 · 判型：**新增能力型**（纯 GUI 壳，内核七段零 diff）

## 1. 实施内容

| 位置 | 改动 |
|---|---|
| `deploy_tab.py` 导入 | 增 `import os`；qfluentwidgets 增 `InfoBar, InfoBarPosition` |
| `__init__` | 增 `self._last_out_dir = None` |
| 快捷页 row3 | 增 `PushButton("打开输出目录")` → `_open_out_dir` |
| 高级页 row2 | 增 `PushButton("打开输出目录")` → `_open_out_dir`（两页共享缓存） |
| `_open_out_dir` | 逐条对齐 `ptrade_export_tab._open_out_dir` 三态：无目录 `InfoBar.info` / 目录不存在 `InfoBar.error` / `os.startfile` + `OSError` 反馈 |
| `_out_dir_from_artifacts` | 静态纯函数：由内核 `artifacts`（product→report→runbook）派生产物目录 |
| `_on_finished` | 转换成功后缓存 `_last_out_dir`（P6 SKIPPED 时无 artifacts ⇒ 不更新 ⇒ 走提示分支） |

## 2. 验收五项（方案 §4）

| # | 标准 | 结果 |
|---|---|---|
| 1 | 快捷页与高级页各有一枚「打开输出目录」按钮 | ✅ `test_both_pages_have_open_dir_button`（findChild 实测 = 2 枚）；真实 GUI 冒烟 `quick_btn/adv_btn` 均打印「打开输出目录」 |
| 2 | 未转换时点击 → 提示，不抛异常不静默 | ✅ `test_click_without_conversion_shows_hint`；真实冒烟 `no_conversion_startfile_calls = []`（未调 startfile） |
| 3 | 转换 PASS 后点击 → `startfile` 实参 = 产物目录 | ✅ `test_click_after_conversion_opens_artifact_dir`（monkeypatch 断言）；**真实冒烟**：`startfile_calls = ['…\\Demo_account_Real_account\\全球资产轮动']`，且 `derived == artifact parent: True` |
| 4 | 零行为变更 | ✅ `quantstudio/ptrade_deploy/` **零 diff**（`git status --porcelain` = `[]`）；deploy 套件 **121/121**；契约矩阵 `OK` |
| 5 | 族机检 | ✅ PD 族 **14 件 / 不合规 0 件**（含新增 PD-2026-0014） |

## 3. 真实 GUI 冒烟读数（offscreen 实例化 + 真控件 + 真回调）

```
quick_btn: 打开输出目录 | adv_btn: 打开输出目录
no_conversion_startfile_calls = []
derived_out_dir = D:\miniQMT策略实盘\QuantStudio\Demo_account_Real_account\全球资产轮动
startfile_calls = ['D:\\miniQMT策略实盘\\QuantStudio\\Demo_account_Real_account\\全球资产轮动']
derived == artifact parent: True
```

> 路径来源：真实 `run_deploy(全球资产轮动)` 的 `artifacts["product"]`（P0–P6 全 PASS），
> 经 `_on_finished` 回调派生——**走的是生产链路，非人工构造**。

## 4. 实施中踩坑（已沉淀进条目）

| 坑 | 现象 | 修正 |
|---|---|---|
| QApplication 未模块级持有 | `RuntimeError: wrapped C/C++ object of type QConfig has been deleted`（连续 3 个用例红） | qfluentwidgets 的 QConfig 是 QObject 单例，app 被回收后 C++ 侧悬空 → QApplication 提到**模块级**持有（与 `tests/test_deploy_gui.py` 既有权威写法对齐） |

## 5. 零回归与判型

- **判型：新增能力型**——只增 UI 入口，不改任何 verdict、产物或内核行为（`ptrade_deploy/` 零 diff 为证）；
- 既有 GUI 冒烟用例（`test_deploy_gui.py`）不变、全绿；
- 产物 SHA 对等门禁不受影响（双端 parity 用例随套件通过）。

## 6. 六步进度

①方案 ✅ → ②审计 ✅ → ③实施 ✅ → ④验收 ✅（本件）→ **⑤用户确认（待）** → ⑥随批统推。
