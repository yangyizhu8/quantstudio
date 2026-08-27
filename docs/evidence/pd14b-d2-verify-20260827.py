"""D2 修正数值验证：确认审计方数值推导 + 修正公式。"""
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DAY = 86400000

normal = 1782835200000   # 正常行（07-01 00:00 CST 存储为 UTC ms）
abnormal = 1782864000000 # 异常行（07-01 08:00 CST 存储为 UTC ms）

def show(label, ms):
    utc = datetime.fromtimestamp(ms / 1000, timezone.utc)
    cst = datetime.fromtimestamp(ms / 1000, CST)
    print(f"{label}: {ms}  UTC={utc}  CST={cst}  %86400000={ms % DAY}")

show("正常行", normal)
show("异常行", abnormal)

print("\n审计方推导验证：")
print(f"  正常行 %86400000 = {normal % DAY}（应为 57600000 非零）")
print(f"  异常行 %86400000 = {abnormal % DAY}（应为 0）")

print("\n修正公式验证（异常行 - 8h 到 CST 零点）：")
fixed = abnormal - 28800000
show("修正后", fixed)
assert fixed == 1782835200000, f"修正后应与正常行一致，实际 {fixed}"
print(f"  ✅ 修正后 == 正常行 1782835200000（CST 00:00）")

print("\n反向验证（地板公式的灾难性）：")
floored = normal // DAY * DAY
show("正常行地板（旧公式）", floored)
assert floored != normal, f"旧公式会改动正常行！{floored} != {normal}"
print(f"  ❌ 旧公式地板使正常行变为 {floored}（=CST 08:00 错位）——审计正确")