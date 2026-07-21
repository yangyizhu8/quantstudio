"""Generate the PR1 security-code contract from the runtime authority."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantstudio.backtest.libs.security_code_rules import render_security_code_rules_markdown


def main() -> None:
    output = ROOT / "docs" / "strategy-compiler" / "security-code-rules.md"
    output.write_text(render_security_code_rules_markdown(), encoding="utf-8", newline="\n")
    print(output)


if __name__ == "__main__":
    main()
