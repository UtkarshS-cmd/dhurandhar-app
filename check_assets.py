"""Verify every assets/ reference in index.html + js/*.js resolves to a real file.

Used by run.bat step 7b. Exits 1 and prints missing paths on failure.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "index.html"
JS_DIR = ROOT / "js"

REF_RE = re.compile(r"""['\"](assets/[^'\"\s>]+)['\"]""")


def collect_refs() -> list[str]:
    refs: set[str] = set()
    html = HTML.read_text(encoding="utf-8")
    refs.update(m.split("?")[0].split("#")[0] for m in REF_RE.findall(html))
    for js in sorted(JS_DIR.glob("*.js")):
        refs.update(
            m.split("?")[0].split("#")[0]
            for m in REF_RE.findall(js.read_text(encoding="utf-8", errors="ignore"))
        )
    # CSS url(...) references, if any are added later.
    for css in sorted((ROOT / "css").glob("*.css")):
        refs.update(
            m.split("?")[0].split("#")[0]
            for m in re.findall(r"url\(\s*['\"]?(assets/[^)'\"\s]+)", css.read_text(encoding="utf-8", errors="ignore"))
        )
    return sorted(refs)


def main() -> int:
    refs = collect_refs()
    print(f"    asset refs: {len(refs)}")
    missing = [r for r in refs if not (ROOT / r).is_file() or (ROOT / r).stat().st_size == 0]
    for m in missing:
        print(f"    [missing] {m}")
    if missing:
        return 1
    print("    all referenced assets exist and are non-empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
