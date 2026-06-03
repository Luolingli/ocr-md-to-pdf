**English** | [简体中文](README.zh-CN.md)

# ocr-md-to-pdf

Compile a [**PaddleOCR-VL**](https://github.com/PaddlePaddle/PaddleOCR) markdown export into a fully **vector, selectable-text PDF book** with XeLaTeX.

The OCR of a scanned Chinese / math textbook gives you a `*.pdf_by_PaddleOCR-VL-*.md`
(math in `$ ... $` / `$$ ... $$`, tables as raw `<table>`, figures as remote `<img>`),
plus a sibling `*.json`. This tool turns that into a clean, typeset PDF where every
character is real text — searchable and copyable, not a scanned image.

It is packaged as a [Claude Code](https://claude.com/claude-code) **skill**, but the
scripts are plain Python 3 (standard library only) and run fine on their own.

## What it handles

- Inline / display math with stray spaces, and **CJK characters inside math** (each CJK
  run is wrapped in `\text{}` so the math font never shows tofu).
- HTML `<table>` → bordered `longtable` (colspan aware); `<img>` → centered
  `\includegraphics`; center `<div>` → `center`.
- Headings → `\chapter*` / `\section*` / … with PDF bookmarks and per-chapter running
  headers; a clean navigable table of contents is generated (the OCR's own messy 目录 is
  dropped by default).
- **Footnote recovery**: the `.md` export silently drops footnotes — they are recovered
  from the `.json` and placed inline at their `①②③` markers (unlocatable ones go to an
  appendix).
- Robust repair of common OCR garble so the document always compiles: HTML entities,
  literal `\n`, `\textcircled{n}`, stray text `<`/`>`, double sub/superscripts,
  under-counted `array` columns, bare `\left`/`\right`, unbalanced braces, …

## Requirements

- **XeLaTeX** (TeX Live / MacTeX) with the **ctex** package and CJK fonts.
  The default `--fontset mac` uses macOS system fonts; on Linux/Windows use
  `--fontset fandol` (`tlmgr install fandol`, self-contained) or `ubuntu` / `windows`.
- **Python 3** (standard library only).
- Optional: Ghostscript (`gs`) or `pdftoppm` to rasterize pages for previewing.

## Quick start

```bash
bash scripts/build.sh "/path/to/NAME.pdf_by_PaddleOCR-VL-1.6.md"
# → writes NAME's folder/book.pdf  (set BUILD=/tmp/out to use another dir)
# extra args pass through to the converter, e.g.:
bash scripts/build.sh foo.md --fontset fandol --title "书名" --author "作者"
```

`build.sh` runs all three stages and reports the page count and any remaining LaTeX errors.

## Stages (run manually to debug / customize)

```bash
# 1) download the remote figures (signed URLs expire — do this promptly)
python3 scripts/download_images.py "INPUT.md" --out-dir imgs --map imgmap.json
# 2) markdown -> LaTeX
python3 scripts/md_to_latex.py "INPUT.md" --imgmap imgmap.json --imgdir imgs --out book.tex
# 3) compile twice (the 2nd pass fills the TOC and running headers)
xelatex -interaction=nonstopmode book.tex
xelatex -interaction=nonstopmode book.tex
```

### Converter options

| option | meaning |
|---|---|
| `--json PATH` | sibling JSON (default: input with `.md`→`.json`); enables footnote recovery |
| `--fontset NAME` | `mac` (default) · `windows` · `ubuntu` · `fandol` |
| `--title` / `--author` | title-page text (title auto-detected from JSON `doc_title`) |
| `--imgmap` / `--imgdir` | image url→file map and the folder for `\graphicspath` |
| `--keep-ocr-toc` | keep the book's own (messy) 目录 instead of dropping it |
| `--no-footnotes` | skip JSON footnote recovery |
| `--report FILE` | write a `review.json` of only the high-risk spots (repaired formulas with `{page,bbox}`, unmapped glyphs, demoted headings, unplaced footnotes, missing images) |
| `--overrides FILE` | apply `{math_id: corrected_latex}` (an AI's evidence-based fixes) reproducibly |
| `--symbols FILE` | merge `{char: latex}` into the symbol map (for unmapped glyphs) |
| `--corrections FILE` | `{wrong: right}` literal text fixes for semantic OCR errors the core can't flag (e.g. a misread character in a heading); make each key unique |

### Accuracy: the AI review loop

The deterministic core only guarantees the document **compiles** — it can't fix what the OCR
*mis-recognised*. Run with `--report review.json` to get the handful of flagged spots (a few
dozen, not thousands), have an AI/human check each against the original page image
(`{page,bbox}`) and the sibling JSON, write corrections to `overrides.json` / `symbols.json`,
then re-run with `--overrides`/`--symbols`. Fixes apply reproducibly and the spot stops being
flagged — no hand-editing of generated output, no fabrication of un-OCR'd content. See
[`SKILL.md`](SKILL.md) for the step-by-step loop and guardrails.

## Why not just `pandoc input.md`?

Markdown does compile to PDF directly — but feeding **PaddleOCR-VL output straight to
pandoc essentially never produces a usable result**. This tool's value is that it fixes
the OCR-specific landmines a generic md→PDF pipeline won't:

- **Inline math `$ x $` with spaces** — pandoc requires the `$` to touch a non-space, so
  every inline formula silently stops being math.
- **CJK characters inside math** — XeLaTeX math mode shows tofu / errors unless each CJK
  run is wrapped in `\text{}`.
- **Tables are raw HTML** — pandoc's markdown→LaTeX drops bare `<table>`.
- **Figures are signed remote URLs** — they expire; they must be fetched locally and mapped.
- **Footnotes aren't in the .md at all** — the OCR export drops them; they can only be
  recovered by matching the sibling `.json`, which no generic tool will do.
- **Pervasive OCR garble** — unbalanced braces, `\left0.8`, double subscripts, nested
  `align*`, package conflicts — any one of these halts compilation.

So: **clean markdown → just use pandoc, this tool is overkill**; **PaddleOCR-VL output of a
Chinese / math textbook → this tool turns "compiles to garbage (or not at all)" into
"works out of the box".**

## Use as a Claude Code skill

Clone (or symlink) this repo into your skills folder so the directory name is the skill name:

```bash
git clone https://github.com/<you>/ocr-md-to-pdf ~/.claude/skills/ocr-md-to-pdf
```

Then in Claude Code: `/ocr-md-to-pdf`, or just ask it to "compile this OCR markdown into a
PDF book". See [`SKILL.md`](SKILL.md) for the full workflow and debugging notes.

## Notes & limitations

- Re-run `download_images.py` before converting if the signed image URLs expired
  (HTTP 403); missing images render as a small `[图]` placeholder.
- The PDF reflects **only what the OCR captured** — genuinely un-OCR'd pages or glyphs
  can't be invented.
- Tuned for PaddleOCR-VL output (HTML tables/images, `$...$` math). Other OCR markdown
  flavors may need small tweaks to the regexes in `scripts/md_to_latex.py`.

## Acknowledgements

Built around the output of [PaddleOCR / PaddleOCR-VL](https://github.com/PaddlePaddle/PaddleOCR)
by [@PaddlePaddle](https://github.com/PaddlePaddle). This tool only converts that output into a
PDF — all OCR credit goes to them.

## License

MIT — see [LICENSE](LICENSE).
