"""Build docs/readme.html from its template, inlining the fonts.

The page has to be self-contained -- openable straight from disk, and valid
under a strict content-security policy -- so the Anthropic Serif cuts are
subsetted to Latin, converted to woff2 and embedded as data URIs rather than
fetched. Subsetting takes the five cuts from ~330 KB to ~76 KB.

Run with:  uv run --with 'fonttools[woff]' python scripts/build_readme.py
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_SRC = ROOT / "Anthropic Serif-fontiko"
TEMPLATE = ROOT / "docs" / "readme.template.html"
OUTPUT = ROOT / "docs" / "readme.html"

# (source cut, css family, weight, style)
CUTS = [
    ("AnthropicSerif-Display-Light-Static", "Anthropic Serif Display", 300, "normal"),
    ("AnthropicSerif-Display-Medium-Static", "Anthropic Serif Display", 500, "normal"),
    ("AnthropicSerif-Text-Regular-Static", "Anthropic Serif Text", 400, "normal"),
    ("AnthropicSerif-Text-Semibold-Static", "Anthropic Serif Text", 600, "normal"),
    ("AnthropicSerif-Text-RegularItalic-Static", "Anthropic Serif Text", 400, "italic"),
]

# Latin, the punctuation the copy actually uses, arrows and a check mark.
UNICODES = (
    "U+0020-007E,U+00A0,U+00B7,U+00D7,U+2013,U+2014,U+2018,U+2019,U+201C,U+201D,"
    "U+2022,U+2026,U+2190-2193,U+2713,U+00A9,U+00E9,U+00B0"
)


def subset(source: Path, target: Path) -> None:
    subprocess.run(
        [
            sys.executable, "-m", "fontTools.subset", str(source),
            f"--output-file={target}",
            "--flavor=woff2",
            f"--unicodes={UNICODES}",
            "--layout-features=kern,liga,calt,onum,lnum,tnum",
        ],
        check=True,
        capture_output=True,
    )


def font_face_css(work: Path) -> str:
    blocks = []
    total = 0
    for stem, family, weight, style in CUTS:
        source = FONT_SRC / f"{stem}.otf"
        if not source.exists():
            raise SystemExit(f"missing font: {source}")
        target = work / f"{stem}.woff2"
        subset(source, target)
        payload = target.read_bytes()
        total += len(payload)
        encoded = base64.b64encode(payload).decode("ascii")
        blocks.append(
            "@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};"
            "font-display:swap;"
            "src:url(data:font/woff2;base64,{data}) format('woff2')}}".format(
                family=family, style=style, weight=weight, data=encoded
            )
        )
    print(f"  embedded {len(CUTS)} cuts, {total / 1024:.0f} KB of woff2")
    return "\n".join(blocks)


def main() -> None:
    if shutil.which("ttx") is None:
        try:
            import fontTools  # noqa: F401
        except ImportError:
            raise SystemExit(
                "fonttools is required. Run:\n"
                "  uv run --with 'fonttools[woff]' python scripts/build_readme.py"
            )

    template = TEMPLATE.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        css = font_face_css(Path(tmp))

    if "/*__FONTS__*/" not in template:
        raise SystemExit("template is missing the /*__FONTS__*/ marker")

    OUTPUT.write_text(template.replace("/*__FONTS__*/", css))
    print(f"  wrote {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
