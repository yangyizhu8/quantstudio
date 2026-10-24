## 【铁律】策略生成默认通过 skill 开发，仅本地 QuantStudio

1. **默认行为**：用户给出策略开发提示词时，默认通过 `quantstudio-strategy-compiler` skill 开发，
   生成 **QuantStudio 本地零依赖策略**并落盘 `quantstudio/backtest/strategies/`。
2. **skill 默认 QuantStudio-only**（不生成 PTrade）。PTrade 转换由 PyQt 的"转 PTrade"tab
   或终端命令（`qs-compile import`）承接，不在策略开发期承担。
3. **ETF 策略默认用 `get_etf_list_local` 做 PIT 动态池**。转 PTrade 时由转换功能
   根据用户确认的回测起始日固化静态池（`ETF_POOL_STATIC`，见 07-ETF动态池固化补充规格）。
4. **用户可明确要求"跳过 skill 直接手写"**来绕过完整流程（应对简单策略）。
5. **历史教训**（2026-07-26）：旧版 skill 产出的 PTrade 代码在真实 IQEngine 报
   `NameError: name 'set_backtest' is not defined`——本地自创 API 混入 PTrade 目标。
   新版分工（skill 只产出本地代码 + 转换管线承接 PTrade）从源头消除该问题；
   旧产出物 `ptrade/tech_etf_mvo_rotation_ptrade.py` 已被真实平台证伪，视为失效。

