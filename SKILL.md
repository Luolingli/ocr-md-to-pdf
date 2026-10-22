---
name: ocr-md-to-pdf
description: Compile a PaddleOCR-VL markdown export (plus its sibling .json) into a fully vector, selectable-text PDF book with XeLaTeX. Use when the user has an OCR'd `*.pdf_by_PaddleOCR-VL*.md` file (typically a Chinese / math textbook with $...$ math, HTML <table>s, and <img> figures) and wants it turned into a clean typeset PDF, or asks to "compile/rebuild/重排 the OCR markdown into a PDF book". Handles CJK-in-math, HTML tables, remote images, footnote recovery from the JSON, and a generated navigable TOC.
---

# OCR markdown → vector PDF book

Turns a PaddleOCR-VL markdown export into a single LaTeX file and compiles it with
XeLaTeX. Output is real vector text (selectable/searchable), not scanned images.

## When this applies
- Input is a `<name>.pdf_by_PaddleOCR-VL-*.md` (math in `$ ... $` / `$$ ... $$`, tables
  as raw `<table>`, figures as `<img src="https://...">`), usually with a sibling
  `<name>.pdf_by_PaddleOCR-VL-*.json` (the structured OCR result — keep it, footnotes
  are recovered from it).

## Prerequisites (check first, with `Bash`)
- `xelatex` (TeX Live / MacTeX) with the **ctex** package and CJK fonts. The default
  `--fontset mac` needs macOS system fonts (Songti/Heiti). On Linux/Windows pass
  `--fontset fandol` (self-contained, `tlmgr install fandol`) or `ubuntu`/`windows`.
- `python3` (standard library only — no pip installs).
- Optional for previews: `gs` (Ghostscript) or `pdftoppm` to rasterize pages.

## Quick start
```bash
SKILL=~/.claude/skills/ocr-md-to-pdf/scripts
bash "$SKILL/build.sh" "/path/to/NAME.pdf_by_PaddleOCR-VL-1.6.md"
# → writes NAME's folder/book.pdf  (override out dir with BUILD=/tmp/build)
# extra args go to the converter, e.g.  ... --fontset fandol --title "书名"
```
`build.sh` does all stages and reports page count + remaining LaTeX errors. It also
auto-loads `overrides.json` / `symbols.json` / `corrections.json` from the build dir
when present (the AI-review artifacts from a previous run), always writes
`review.json`, and finishes with a content-fidelity audit (`audit.py`) and a
source-vs-output page-count hint.

## Manual stages (use these to debug or customize)
```bash
SKILL=~/.claude/skills/ocr-md-to-pdf/scripts
cd <build-dir>
# 1) download the remote figures (signed URLs expire — do this promptly)
python3 "$SKILL/download_images.py" "<INPUT.md>" --out-dir imgs --map imgmap.json
# 2) markdown -> LaTeX
python3 "$SKILL/md_to_latex.py" "<INPUT.md>" --imgmap imgmap.json --imgdir imgs --out book.tex
# 3) compile twice (2nd pass fills the TOC and running headers)
xelatex -interaction=nonstopmode book.tex
xelatex -interaction=nonstopmode book.tex
```

## Converter options (`md_to_latex.py`)
- `--json PATH`      sibling JSON (default: input with `.md`→`.json`); enables footnotes.
- `--fontset NAME`   `mac` (default) | `windows` | `ubuntu` | `fandol`.
- `--title` / `--author`   title-page text (title auto-detected from JSON `doc_title`).
- `--imgmap` / `--imgdir`  image map json and the folder name for `\graphicspath`.
- `--keep-ocr-toc`   keep the book's own (messy, run-on) 目录 instead of dropping it.
- `--no-footnotes`   skip JSON footnote recovery.

## What the converter handles automatically
- `$ ... $` / `$$ ... $$` math with stray spaces; **CJK runs inside math are wrapped in
  `\text{}`** so the math font never shows tofu.
- HTML `<table>` → bordered `longtable` (colspan via `\multicolumn`); `<img>` → centered
  `\includegraphics` sized from the `width="NN%"` hint; center `<div>` → `center`.
- Headings → `\chapter*` / `\section*` / … with PDF bookmarks; OCR-misdetected body text
  promoted to a fake top-level heading is demoted back to text (only `第N章` / known
  front-/back-matter titles become chapters); running header set per chapter via `\markboth`.
  **The book's own printed TOC is ground truth**: any body heading whose title matches a
  numbered TOC entry is re-leveled to the TOC's level (`第N章`→chapter, `N.M`→section,
  `N.M.K`→subsection), which fixes PaddleOCR's frequent heading-level misassignment
  (e.g. sections marked `##` and unnumbered chapter titles marked `###`). TOC entries
  that never appear as a body heading are reported in `review.json: missing_headings`
  (the OCR lost the heading — restore it via `--corrections`).
- Footnotes (dropped by the .md export) recovered from the JSON: placed inline at their
  `$^{①}$` marker by page-context; any that can't be located go to a "补充脚注" appendix.
- Robustness for OCR garble: HTML entities, literal `\n`, `\textcircled{n}`, stray text
  `<`/`>`, double sub/superscripts, under-counted `array` columns, bare `\left`/`\right`,
  unbalanced braces — all repaired so the document compiles.
- PaddleOCR idiosyncrasies in math: CJK wrapped as `\mathrm{~ 中文 ~}` is rewritten to
  `\text{中文}`; `\mathbf{Greek}` (undefined in OT1 math — it makes XeTeX request a
  bogus U+0005 glyph from the bold Latin font) is rewritten to `\boldsymbol{Greek}`;
  `\xlongequal{def}` ("defined as" equals) is provided in the preamble; display formulas
  that start with a bracket get a `\relax` prefix to silence the amsmath warning.
- Loud structural checks: an **odd number of inline `$`** (a stray unpaired `$` shifts
  every inline pairing after it) and any inline span that crosses lines or swallows
  markup are reported loudly — do not trust the output until they are fixed via
  `--corrections`.

## Verify the result
After compiling, **check the log and the pages**:
```bash
grep -cE '^! ' xelatex2.log              # LaTeX errors (aim for 0)
grep -c 'Missing character' xelatex2.log # missing glyphs (aim for 0)
gs -q -dSAFER -dBATCH -dNOPAUSE -sDEVICE=png16m -r110 \
   -dFirstPage=N -dLastPage=N -sOutputFile=p.png book.pdf   # eyeball a page
```
Confirm fidelity (no content dropped): every long CJK run in the md should appear in the
tex — `build.sh` runs this automatically via `scripts/audit.py`; manually:
```bash
python3 "$SKILL/audit.py" "<INPUT.md>" book.tex   # exit 1 lists missing runs
```

## Improving accuracy: the AI review loop (hybrid)
The deterministic core only guarantees the document **compiles** — it cannot fix what the
OCR mis-*recognised* (a garbled formula, a wrong symbol). That residual is where the AI
adds value over a bare script. Keep the script for the 99% that is fine, and spend AI
effort only on the handful of flagged spots:

1. Convert with a report:
   ```bash
   python3 scripts/md_to_latex.py "INPUT.md" --imgmap imgmap.json --imgdir imgs \
           --out book.tex --report review.json
   ```
   `review.json` lists ONLY the high-risk spots (typically a few dozen, not thousands):
   - `math`: formulas a repair heuristic had to touch (`brace_imbalance`, `array_columns`,
     `left_right_delim`, double sub/superscript). Each has a stable `id`, the `raw` OCR, the
     `rendered` LaTeX, and — when matchable — the original `{page, bbox}`.
   - `broken_inline_spans`: inline spans that cross lines or contain markup (a stray `$`
     earlier in the document). Each has the `line` in the md where the broken span starts —
     the culprit `$` is at or just before that line.
   - `missing_headings`: printed-TOC entries with no matching body heading (OCR dropped the
     heading). Restore them with `--corrections` (the TOC is the title evidence).
     **Not every entry here is a real problem** — triage before touching the md:
     * *TOC sub-entries* (`几个例子`, `子列`, `曲线的拐点`, `平面束`, …) are the **second- and
       third-level lines of the book's own printed 目录**: they sit indented under a `§N`
       line and a matching body heading usually *does* exist, just without a `#` mark
       (grep the title in `book.tex` — if it is there as plain text, the PDF is fine and
       this is a false alarm). Silently editing them only corrupts the TOC parse.
     * *Scan artefacts* are the real junk: captions/headers of the scanning service
       baked into the page (e.g. nine repeated `N. 2000年，中国人口…达到14亿人。` lines
       printed across a 目录 page by the scanner overlay). These are **not book content**
       and should be deleted from the md outright — see the junk-hunt recipe below.
     * A literal `<!--bookpage:N-->` appearing as an entry means a page marker leaked into
       the printed-TOC text; delete that line.
   - `unmapped_chars`, `demoted_headings`, `footnotes_unplaced`, `missing_images`.
   - `inline_dollar_count`: total inline `$` in the md (odd = broken, see above).
2. For each flagged `math` item, **repair from evidence only**:
   - render the source region to look at it:
     `gs -q -dSAFER -dBATCH -dNOPAUSE -sDEVICE=png16m -r150 -dFirstPage=<page+1> -dLastPage=<page+1> -sOutputFile=chk.png book.pdf`
     (the original page image; `bbox` is `[x0,y0,x1,y1]` on that page), and/or look up the
     same formula in the sibling `.json`;
   - decide the correct LaTeX from what is actually visible. **Do not invent content**: if a
     piece is genuinely unreadable, leave the heuristic result and note it — never fabricate
     math or text that the source does not support.
3. Write the accepted fixes to `overrides.json` keyed by the `id`, e.g.
   ```json
   { "7ef8563c": "s = \\sqrt{\\mathbb{V}(\\widehat{Y}_*)}" }
   ```
   For unmapped glyphs, write `symbols.json`, e.g. `{ "≜": "$\\triangleq$" }`.
   For a *semantic* OCR error in plain text the core can't flag — a misread character in
   a heading or sentence (e.g. a section titled "第三部分" that the book's own TOC and
   context show must be "第二部分") — use `--corrections corrections.json`, a `{wrong:
   right}` map of literal replacements applied to the raw md. Make each key long enough to
   be unique (`"MCMC 第三部分": "MCMC 第二部分"`), and only correct what the evidence
   (the book's printed TOC, surrounding text, the source image) clearly supports.
4. Re-run with the overrides and recompile — fixes are applied reproducibly, no hand-editing:
   ```bash
   python3 scripts/md_to_latex.py "INPUT.md" --imgmap imgmap.json --imgdir imgs \
           --out book.tex --overrides overrides.json --symbols symbols.json --report review.json
   xelatex -interaction=nonstopmode book.tex && xelatex -interaction=nonstopmode book.tex
   ```
   An overridden formula stops being flagged; iterate until `review.json`'s `math` is empty
   or only contains items you've confirmed are already correct. `overrides.json` /
   `symbols.json` are the durable, auditable record of the AI's corrections.

## Hunting scan artefacts (non-book text baked into the page)

Scanned-page services sometimes leave their own text on the page (order numbers,
"Scanned with CamScanner", promotional lines, a客服 hotline). OCR reads them faithfully,
so they are **not** OCR errors — but they are not book content either and must be removed
from the md. They cluster on front matter (目录/版权页), never inside the mathematical body.

Recipe — look before you cut, and cut with assertions:

```bash
# 1) find the suspicious text and count it
grep -n 'Scanned with CamScanner\|2000年\|camscanner' "<book>.md"
# 2) confirm which source page it came from — if it is in the JSON too, it is really
#    printed on the page (not an md corruption)
python3 - <<'PY'
import json
J=json.load(open("<book>.json",encoding="utf-8"))
for i,p in enumerate(J,1):
    for b in p.get("prunedResult",{}).get("parsing_res_list",[]):
        if "Scanned with CamScanner" in b.get("block_content",""):
            print(i, b["block_label"], b["block_content"][:80])
PY
# 3) delete the whole run of lines in one shot, asserting the expected count,
#    then rebuild and prove it is gone from the RENDERED pdf (not just the md):
gs -q -dSAFER -dBATCH -dNOPAUSE -sDEVICE=txtwrite -sOutputFile=/tmp/all.txt book.pdf
grep -c '2000年' /tmp/all.txt     # must be 0
```

Rules: back up the md first; assert the exact number of hit lines before deleting (a
blank/regex-too-wide replace once ate a whole corpus); after rebuilding, verify with
`gs -sDEVICE=txtwrite` on the **PDF**, because a clean `book.tex` alone does not prove the
page is clean.

## Fixing leftover errors
The converter clears the known PaddleOCR failure classes, but a brand-new document may
surface a new one. If `grep '^! ' xelatex2.log` is non-zero:
1. Read the first error and its `l.NNN`; open that line in `book.tex`.
2. If the block alone compiles in isolation, the cause is an earlier unclosed construct —
   find it with `xelatex -halt-on-error`.
3. Add the fix to `md_to_latex.py` (usually a new symbol in `SYM`, a guard in
   `normalize_math`, or a tighter regex), regenerate, recompile. Do **not** hand-edit
   `book.tex` — it is regenerated every run.

Known misleading diagnostics (learned the hard way):
- `Missing character: There is no ^^E (U+0005) in font [lmromandemi10-regular]` — the
  U+0005 is a red herring: it is XeTeX asking the *bold Latin* font for a glyph slot that
  doesn't exist, caused by `\mathbf` applied to a Greek letter (e.g. `\mathbf{\Pi}`, an
  OCR misread of a bold math symbol). The character is NOT in your `.tex`; grep for
  `\mathbf{` + Greek. The converter now rewrites these to `\boldsymbol{...}`, but if the
  *intended* symbol is something else entirely (e.g. a pre-annihilator `⊥N` misread as
  `\mathbf{\Pi}^\perp N`), use `--corrections` with the book's own usage elsewhere as
  evidence.
- A wall of `Improper \prevdepth` / `Missing \cr` / `Missing }` errors at the *next*
  heading after a formula: the formula above it has an unclosed construct (e.g. a
  `\begin{array}` with no `\end{array}` — OCR sometimes mangles a big symbol like `⋂`
  into a broken array). Fix the formula, and the cascade disappears.

## Notes
- Re-run `download_images.py` before converting if the signed image URLs have expired
  (HTTP 403 / "too small"); missing images render as a small `[图]` placeholder.
- The PDF references only what the OCR captured. Genuinely un-OCR'd pages/glyphs can't be
  invented — report such gaps rather than fabricating textbook content.
