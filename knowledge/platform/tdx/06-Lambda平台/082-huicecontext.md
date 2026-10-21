---
title: "context上下文变量context.stock、context.universe的核心区别 | 通达信量化平台"
source_url: "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60/huicecontext.html"
crawled_at: 2026-10-12T02:42:35+08:00
platform: TDX
category: 06-Lambda平台
depth: 4
tags: [平台/TDX, 状态/已抓取]
---

### [#](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60/huicecontext.html#context上下文变量context-stock、context-universe的核心区别) context上下文变量context.stock、context.universe的核心区别

`context.stock`
仅允许在 init(context) 内部做声明赋值，作为股票池输入入口。
支持单字符串 context.stock = "000001.SZ"，也支持列表 context.stock = ["000001.SZ","600000.SH"]
⚠️后续回调（handle_bar / before_trading_start）修改它无效，不会改变回测股票池。
⚠️不能写 context.stock = ["all"]，没有全市场魔法值；不赋值会直接抛 ValueError，回测直接报错终止。
`context.universe`
Lambda框架内部做：代码标准化、去重、合法性校验之后，输出的最终生效股票池。
init 执行完成之后才定稿，init内部访问context.universe不一定完整；
在handle_bar、before_trading_start、after_trading_end回调里面，业务代码、冒烟测试统一遍历 context.universe，不要用 context.stock。

```
def init(context):
    # 仅在这里声明股票池
    context.stock = ["000001.SZ", "600000.SH"]

def handle_bar(context, bar_dict):
    # ❌不要 for sym in context.stock
    # ✅建议统一使用 universe
    for sym in context.universe:
        bar = bar_dict[sym]
```

1
2
3
4
5
6
7
8
9

← [成交回调on_trade](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9d546pbhc/mindoc-1hib60s1q4hbg.html) [表单参数对象context.params](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hho7blr2j340/mindoc-1hho9u0cbfe60/mindoc-1hhoahrgeati0.html) →
