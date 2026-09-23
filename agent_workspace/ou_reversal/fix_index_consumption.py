
import json, os
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
for rel in [r"output\generated_strategies\ou_reversal_csi300_10\agent_strategy_design.json",
            r"agent_workspace\ou_reversal_csi300_10\agent_strategy_design.json"]:
    p = os.path.join(ROOT, rel)
    j = json.load(open(p, encoding="utf-8"))
    mt = j["factor_definitions"]["market_timing"]
    mt["consumption_rule"] = ("【E1-4 消费规则】daily-bar-v1 契约下该 API 末行为 D 日（daily_incl_T）；"
        "返回帧以 trade_date 为 **index**（ptrade_api.py:1979 df.set_index('trade_date')），"
        "fields 白名单中的 'trade_date' 不是列、会被丢弃，故必须经 df.index 读取日期。"
        "本策略成交于 D 日开盘，故必须丢弃 index >= D 的行，只消费 <= S 的行："
        "取 count=need+1 → 过滤 [t for t in df.index if t < D] → 断言剩余 >= need → 取末 need 行。R4 断言 A4-1 锁定。")
    mt["price_basis"] = "get_index_day_bar('000300', count=need+1, fields=['high','low','close'])（指数不复权；trade_date 为 index）"
    open(p, "w", encoding="utf-8").write(json.dumps(j, ensure_ascii=False, indent=2) + "\n")
    print(rel, "consumption_rule updated")
