#!/usr/bin/env bash
# One-shot: download images, convert markdown -> LaTeX, compile twice with XeLaTeX.
#
#   build.sh INPUT.md [extra md_to_latex.py args...]
#
# Output PDF is written next to INPUT.md as book.pdf. Set BUILD=/some/dir to use a
# separate build directory. Extra args are forwarded to md_to_latex.py, e.g.
#   build.sh foo.md --fontset fandol --title "My Book"
#
# Persistent AI-correction files are picked up automatically when present in $BUILD:
#   overrides.json / symbols.json / corrections.json   (see SKILL.md, "AI review loop")
# The run always writes $BUILD/review.json and finishes with a content-fidelity
# audit (audit.py) plus a source-vs-output page-count hint.
set -euo pipefail

MD="${1:?usage: build.sh INPUT.md [md_to_latex.py args...]}"; shift || true
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$(dirname "$MD")" && pwd)"
BUILD="${BUILD:-$SRC_DIR}"
mkdir -p "$BUILD"

echo "== [1/4] downloading images =="
python3 "$SKILL_DIR/download_images.py" "$MD" --out-dir "$BUILD/imgs" --map "$BUILD/imgmap.json"

EXTRA=""
[ -f "$BUILD/overrides.json" ]   && EXTRA="$EXTRA --overrides $BUILD/overrides.json"
[ -f "$BUILD/symbols.json" ]     && EXTRA="$EXTRA --symbols $BUILD/symbols.json"
[ -f "$BUILD/corrections.json" ] && EXTRA="$EXTRA --corrections $BUILD/corrections.json"

echo "== [2/4] markdown -> LaTeX =="
# shellcheck disable=SC2086
python3 "$SKILL_DIR/md_to_latex.py" "$MD" --imgmap "$BUILD/imgmap.json" --imgdir imgs \
        --out "$BUILD/book.tex" --report "$BUILD/review.json" $EXTRA "$@"

echo "== [3/4] xelatex pass 1 =="
( cd "$BUILD" && xelatex -interaction=nonstopmode book.tex >xelatex1.log 2>&1 || true )
echo "== [4/4] xelatex pass 2 (TOC + headers) =="
( cd "$BUILD" && xelatex -interaction=nonstopmode book.tex >xelatex2.log 2>&1 || true )

ERR=$(grep -cE '^! ' "$BUILD/xelatex2.log" || true)
MISS=$(grep -c 'Missing character' "$BUILD/xelatex2.log" || true)
PAGES=$(grep -oE 'Output written on .* \([0-9]+ page' "$BUILD/xelatex2.log" | grep -oE '[0-9]+ page' || echo "?")
echo "done: $BUILD/book.pdf  (errors=$ERR, missing_glyphs=$MISS, $PAGES)"

echo "== content-fidelity audit =="
python3 "$SKILL_DIR/audit.py" "$MD" "$BUILD/book.tex" || echo "NOTE: audit found missing content — see above"

# hint: compare with the source PDF page count when the source PDF is discoverable
SRC_PDF="$(printf '%s' "$MD" | sed -E 's/_by_PaddleOCR-VL[^/]*$//').pdf"
if [ -f "$SRC_PDF" ]; then
  SPAGES=""
  if command -v pdfinfo >/dev/null 2>&1; then
    SPAGES=$(pdfinfo "$SRC_PDF" | awk '/^Pages:/{print $2}')
  elif command -v mdls >/dev/null 2>&1; then
    SPAGES=$(mdls -name kMDItemNumberOfPages "$SRC_PDF" 2>/dev/null | awk -F'= ' '/kMDItemNumberOfPages/{print $2}')
  fi
  [ -n "${SPAGES:-}" ] && echo "note: source PDF has $SPAGES pages; re-typeset output is $PAGES (page count naturally differs after reflow)"
fi

[ "$ERR" = "0" ] || echo "NOTE: $ERR LaTeX error(s) remain — see $BUILD/xelatex2.log (grep '^! ')"
[ "$MISS" = "0" ] || echo "NOTE: $MISS missing-glyph warning(s) — see $BUILD/xelatex2.log (grep 'Missing character'). Hint: 'U+0005 in lmromandemi' usually means \\mathbf applied to a Greek letter."
