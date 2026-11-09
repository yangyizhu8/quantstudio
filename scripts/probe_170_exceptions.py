# -*- coding: utf-8 -*-
"""裁 (a) 执行：170 对「晚于源端首日」的例外——逐对探测 QuestDB 是否真有数据。

判定：
  有 ⇒ 可逐对回填（进 backfill 清单）
  无 ⇒ 入丙白名单 known_violations 登记
"""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
PROBE = ROOT / "docs" / "evidence" / "qfq-gap-probe-20261110.json"
CST = timezone(timedelta(hours=8))


def qdb_all_first_days(table: str, tcol: str) -> dict:
    sql = f"SELECT ts_code, min({tcol}) FROM {table} GROUP BY ts_code"
    url = "http://127.0.0.1:9000/exec?" + urllib.parse.urlencode({"query": sql})
    with urllib.request.urlopen(url, timeout=300) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    out = {}
    for ts, mn in d.get("dataset", []):
        code = str(ts).split(".")[0]
        try:
            s = str(mn).replace("Z", "")
            dt = datetime.fromisoformat(s).replace(tzinfo=CST)
            out[code] = int(dt.timestamp() * 1000)
        except Exception:
            pass
    return out


def qdb_count(ts_code: str, day: str) -> int:
    """查 QuestDB stock_daily 某 code 某日行数（列名 trade_date）。"""
    sql = (f"SELECT count() FROM stock_daily WHERE ts_code = '{ts_code}' "
           f"AND trade_date >= '{day}' AND trade_date < dateadd('d',1,'{day}')")
    url = "http://127.0.0.1:9000/exec?" + urllib.parse.urlencode({"query": sql})
    with urllib.request.urlopen(url, timeout=120) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return int(d["dataset"][0][0])


print("=" * 78)
print("① 筛出 170 对「晚于源端首日」的例外")
print("=" * 78)
probe = json.loads(PROBE.read_text(encoding="utf-8"))
no_src = probe["STOCK_daily"]["no_source"]

first_days = qdb_all_first_days("stock_daily", "trade_date")
print(f"  stock_daily 拿到 {len(first_days):,} 个 code 的首日")

exceptions = []
for g in no_src:
    day = g["day"]
    day_ms = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)
    for code in g["codes"]:
        fd = first_days.get(str(code))
        if fd is not None and day_ms >= fd:
            exceptions.append({"code": str(code), "day": day,
                               "first_day": datetime.fromtimestamp(fd / 1000, CST).strftime("%Y-%m-%d")})
print(f"  例外对数 = {len(exceptions)}")

print("\n" + "=" * 78)
print("② 逐对探测 QuestDB 是否真有数据（裁 a 核心）")
print("=" * 78)
fillable = []
no_source = []
for i, e in enumerate(exceptions, 1):
    code, day = e["code"], e["day"]
    sfx = ".SH" if code.startswith(("6", "9")) else ".SZ"
    try:
        n = qdb_count(f"{code}{sfx}", day)
    except Exception as ex:
        n = -1
    if n > 0:
        fillable.append({**e, "qdb_rows": n})
    else:
        no_source.append({**e, "qdb_rows": n})
    if i % 30 == 0 or i == len(exceptions):
        print(f"  [{i}/{len(exceptions)}] 可补={len(fillable)} 无源={len(no_source)}")

print(f"\n  ✅ 可逐对回填: {len(fillable)} 对")
print(f"  ❌ 无源（入白名单）: {len(no_source)} 对")

print("\n  --- 可补样本（前 5）---")
for e in fillable[:5]:
    print(f"    {e['code']} {e['day']}  QuestDB={e['qdb_rows']} 行（源端首日 {e['first_day']}）")
print("  --- 无源样本（前 5）---")
for e in no_source[:5]:
    print(f"    {e['code']} {e['day']}  QuestDB=0（源端首日 {e['first_day']}）")

out = ROOT / "docs" / "evidence" / "exception-170-probe-20261110.json"
out.write_text(json.dumps({
    "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
    "scope": "STOCK_daily 中「晚于源端首日」的 170 对例外",
    "total": len(exceptions),
    "fillable": fillable,
    "no_source": no_source,
    "summary": {"fillable": len(fillable), "no_source": len(no_source)},
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n  证据: {out.name}")
print(f"\n  裁 (a) 判定：{'有 → 逐对回填 ' + str(len(fillable)) + ' 对' if fillable else '全部无源 → 入丙白名单'}")
