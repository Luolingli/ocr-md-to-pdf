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
`build.sh` does all three stages and reports page count + remaining LaTeX errors.

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
- Footnotes (dropped by the .md export) recovered from the JSON: placed inline at their
  `$^{①}$` marker by page-context; any that can't be located go to a "补充脚注" appendix.
- Robustness for OCR garble: HTML entities, literal `\n`, `\textcircled{n}`, stray text
  `<`/`>`, double sub/superscripts, under-counted `array` columns, bare `\left`/`\right`,
  unbalanced braces — all repaired so the document compiles.

## Verify the result
After compiling, **check the log and the pages**:
```bash
grep -cE '^! ' xelatex2.log              # LaTeX errors (aim for 0)
grep -c 'Missing character' xelatex2.log # missing glyphs (aim for 0)
gs -q -dSAFER -dBATCH -dNOPAUSE -sDEVICE=png16m -r110 \
   -dFirstPage=N -dLastPage=N -sOutputFile=p.png book.pdf   # eyeball a page
```
Confirm fidelity (no content dropped): every long CJK run in the md should appear in the
tex — see the audit one-liner below.
```bash
python3 - "$INPUT_MD" book.tex <<'PY'
import re,sys
md=open(sys.argv[1],encoding='utf-8').read(); tex=re.sub(r'\s+','',open(sys.argv[2],encoding='utf-8').read())
md=re.sub(r'<table.*?</table>',' ',md,flags=re.S); md=re.sub(r'\$\$.+?\$\$',' ',md,flags=re.S)
md=re.sub(r'\$[^$]+?\$',' ',md); md=re.sub(r'<[^>]+>',' ',md)
miss=[r for r in set(re.findall(r'[一-鿿]{6,}',md)) if r not in tex]
print('CJK runs missing from tex:',len(miss)); [print(' ',x) for x in miss[:10]]
PY
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
   - `unmapped_chars`, `demoted_headings`, `footnotes_unplaced`, `missing_images`.
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

## Fixing leftover errors
The converter clears the known PaddleOCR failure classes, but a brand-new document may
surface a new one. If `grep '^! ' xelatex2.log` is non-zero:
1. Read the first error and its `l.NNN`; open that line in `book.tex`.
2. If the block alone compiles in isolation, the cause is an earlier unclosed construct —
   find it with `xelatex -halt-on-error`.
3. Add the fix to `md_to_latex.py` (usually a new symbol in `SYM`, a guard in
   `normalize_math`, or a tighter regex), regenerate, recompile. Do **not** hand-edit
   `book.tex` — it is regenerated every run.

## Notes
- Re-run `download_images.py` before converting if the signed image URLs have expired
  (HTTP 403 / "too small"); missing images render as a small `[图]` placeholder.
- The PDF references only what the OCR captured. Genuinely un-OCR'd pages/glyphs can't be
  invented — report such gaps rather than fabricating textbook content.
