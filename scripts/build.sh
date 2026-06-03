#!/usr/bin/env bash
# One-shot: download images, convert markdown -> LaTeX, compile twice with XeLaTeX.
#
#   build.sh INPUT.md [extra md_to_latex.py args...]
#
# Output PDF is written next to INPUT.md as book.pdf. Set BUILD=/some/dir to use a
# separate build directory. Extra args are forwarded to md_to_latex.py, e.g.
#   build.sh foo.md --fontset fandol --title "My Book"
set -euo pipefail

MD="${1:?usage: build.sh INPUT.md [md_to_latex.py args...]}"; shift || true
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$(dirname "$MD")" && pwd)"
BUILD="${BUILD:-$SRC_DIR}"
mkdir -p "$BUILD"

echo "== [1/4] downloading images =="
python3 "$SKILL_DIR/download_images.py" "$MD" --out-dir "$BUILD/imgs" --map "$BUILD/imgmap.json"

echo "== [2/4] markdown -> LaTeX =="
python3 "$SKILL_DIR/md_to_latex.py" "$MD" --imgmap "$BUILD/imgmap.json" --imgdir imgs \
        --out "$BUILD/book.tex" "$@"

echo "== [3/4] xelatex pass 1 =="
( cd "$BUILD" && xelatex -interaction=nonstopmode book.tex >xelatex1.log 2>&1 || true )
echo "== [4/4] xelatex pass 2 (TOC + headers) =="
( cd "$BUILD" && xelatex -interaction=nonstopmode book.tex >xelatex2.log 2>&1 || true )

ERR=$(grep -cE '^! ' "$BUILD/xelatex2.log" || true)
PAGES=$(grep -oE 'Output written on .* \([0-9]+ page' "$BUILD/xelatex2.log" | grep -oE '[0-9]+ page' || echo "?")
echo "done: $BUILD/book.pdf  (errors=$ERR, $PAGES)"
[ "$ERR" = "0" ] || echo "NOTE: $ERR LaTeX error(s) remain — see $BUILD/xelatex2.log (grep '^! ')"
