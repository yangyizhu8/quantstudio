
import os, sys
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, os.path.join(ROOT, "skills", "quantstudio-strategy-compiler", "scripts"))
import inspect
import prepare_user_backtest_candidate as p
print(inspect.getsource(p)[:2500])
