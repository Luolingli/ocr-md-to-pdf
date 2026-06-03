#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convert a PaddleOCR-VL markdown export (Chinese / math textbook) into a single
self-contained LaTeX file that XeLaTeX compiles into a fully vector PDF book.

Handles the quirks of PaddleOCR markdown:
  * inline `$ ... $` / display `$$ ... $$` math with spaces and embedded CJK
    (CJK runs inside math are wrapped in \\text{} so the math font never misses glyphs)
  * HTML <table> -> bordered longtable (colspan aware)
  * <img ...> -> \\includegraphics from a locally downloaded image map
  * <div style="text-align:center"> -> center environment
  * circled footnote markers ①..⑩, raw \\textcircled{n}, HTML entities, stray
    math `<`/`>` written in plain text, malformed arrays / \\left..\\right, etc.
  * recovers footnotes from the sibling *.json (the .md export silently drops them)
  * drops the OCR's own messy table of contents (a clean navigable TOC is generated)

Pure standard library. Usage:  python3 md_to_latex.py INPUT.md [options]
"""
import re, json, os, argparse

CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩'
GL = CIRCLED

# module-level stores populated during conversion
DISPLAY, INLINE, TABLE, IMG = [], [], [], []
FOOTNOTES, FOOTNOTE_LEFTOVERS = [], []
IMGMAP = {}

# ---------------------------------------------------------------- math helpers
WRAP_RE = re.compile(r'[　-〿一-鿿＀-￯‘’“”…·—–]+')
ARR_RE = re.compile(r'(\\begin\{array\}\{)([^{}]*)(\})(.*?)(\\end\{array\})', re.S)


def fix_arrays(c):
    """make the array column spec at least as wide as the widest row (OCR under-counts)"""
    def rep(m):
        body = m.group(4)
        maxc = 1
        for r in re.split(r'\\\\', body):
            if r.strip():
                maxc = max(maxc, len(r.split('&')))
        ncols = len(re.findall(r'[lcr]', m.group(2)))
        spec = m.group(2) if ncols >= maxc else 'l' * maxc
        return m.group(1) + spec + m.group(3) + body + m.group(5)
    return ARR_RE.sub(rep, c)


def balance_braces(c):
    """drop unmatched } and close unmatched { so a garbled OCR formula still compiles"""
    out, depth, i, n = [], 0, 0, len(c)
    while i < n:
        ch = c[i]
        if ch == '\\' and i + 1 < n:
            out.append(ch); out.append(c[i + 1]); i += 2; continue
        if ch == '{':
            depth += 1; out.append(ch)
        elif ch == '}':
            if depth > 0:
                depth -= 1; out.append(ch)
        else:
            out.append(ch)
        i += 1
    if depth > 0:
        out.append('}' * depth)
    return ''.join(out)


def decode_entities(c):
    return (c.replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', '~')
             .replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', r'\&'))


def normalize_math(c):
    c = decode_entities(c).strip()
    if not c:
        return ""
    c = re.sub(r'(?<!\\)%', r'\\%', c)                # escape only bare percent
    for k, ch in enumerate(CIRCLED, 1):
        if ch in c:
            c = c.replace(ch, r'{\text{\cnum{%d}}}' % k)
    c = re.sub(r'_([A-Za-z0-9])_([A-Za-z0-9])', r'_{\1\2}', c)   # a_b_c -> a_{bc}
    c = re.sub(r'\^([A-Za-z0-9])\^([A-Za-z0-9])', r'^{\1\2}', c)
    c = WRAP_RE.sub(lambda m: r'\text{' + m.group(0) + '}', c)   # CJK runs -> \text{}
    c = fix_arrays(c)
    # insert a "." after a bare \left / \right whose delimiter the OCR dropped
    # (but never touch \leftarrow, \rightarrow, \rightsquigarrow, ...)
    c = re.sub(r'\\left(?![A-Za-z\s(\[\]{}|./<>)\\])', r'\\left.', c)
    c = re.sub(r'\\right(?![A-Za-z\s(\[\]{}|./<>)\\])', r'\\right.', c)
    return balance_braces(c)


# ---------------------------------------------------------------- inline text
SYM = {
    '–': '--', '－': '-', '．': '.', '～': r'$\sim$',
    '⊥': r'$\perp$', '∥': r'$\parallel$', '→': r'$\to$', '↦': r'$\mapsto$',
    '↔': r'$\leftrightarrow$', '⇒': r'$\Rightarrow$', '⇔': r'$\Leftrightarrow$',
    '∝': r'$\propto$', '≈': r'$\approx$', '≡': r'$\equiv$', '∼': r'$\sim$',
    '∀': r'$\forall$', '∃': r'$\exists$', '√': r'$\surd$', '∑': r'$\sum$',
    '∏': r'$\prod$', '∫': r'$\int$', '∂': r'$\partial$', '∇': r'$\nabla$',
    '✓': r'$\checkmark$', '✗': r'$\times$', '−': '-',
    'χ': r'$\chi$', 'λ': r'$\lambda$', 'δ': r'$\delta$', 'μ': r'$\mu$',
    'σ': r'$\sigma$', 'θ': r'$\theta$', 'α': r'$\alpha$', 'β': r'$\beta$',
    '²': r'\textsuperscript{2}', '³': r'\textsuperscript{3}',
    '×': r'$\times$', '÷': r'$\div$', '±': r'$\pm$',
    '⊆': r'$\subseteq$', '⊂': r'$\subset$', '⊇': r'$\supseteq$',
    '∈': r'$\in$', '∉': r'$\notin$', '∪': r'$\cup$', '∩': r'$\cap$',
    '∅': r'$\varnothing$', '≤': r'$\leq$', '≥': r'$\geq$', '≠': r'$\neq$',
    '∞': r'$\infty$', '△': r'$\triangle$', '□': r'$\square$',
    '§': r'\S{}', '©': r'\textcopyright{}', '®': r'\textregistered{}',
    '<': r'$<$', '>': r'$>$',
    'Ⅰ': 'I', 'Ⅱ': 'II', 'Ⅲ': 'III', 'Ⅳ': 'IV', 'Ⅴ': 'V', 'Ⅵ': 'VI',
    '①': r'\cnum{1}', '②': r'\cnum{2}', '③': r'\cnum{3}', '④': r'\cnum{4}',
    '⑤': r'\cnum{5}', '⑥': r'\cnum{6}', '⑦': r'\cnum{7}', '⑧': r'\cnum{8}',
    '⑨': r'\cnum{9}', '⑩': r'\cnum{10}',
}
SYM_RE = re.compile('|'.join(re.escape(k) for k in SYM))
BARE_CMD = re.compile(r'\\[A-Za-z]+\*?(?:\{[^{}]*\}|\[[^\]]*\]|\^\{[^{}]*\}|_\{[^{}]*\})*')
ESC = {'&': r'\&', '%': r'\%', '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}',
       '~': r'\textasciitilde{}', '^': r'\textasciicircum{}', '\\': r'\textbackslash{}'}
ESC_RE = re.compile(r'[&%#_{}~^\\]')


def render_inline(s):
    if s is None:
        return ""
    s = (s.replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
          .replace('&quot;', '"').replace('&#39;', "'"))
    local = []

    def stash(latex):
        local.append(latex)
        return '\x01%d\x01' % (len(local) - 1)

    s = BARE_CMD.sub(lambda m: stash('$' + m.group(0) + '$'), s)   # bare \Omega, \cup ...
    s = SYM_RE.sub(lambda m: stash(SYM[m.group(0)]), s)
    s = ESC_RE.sub(lambda m: ESC[m.group(0)], s)
    s = re.sub(r'\*\*([^*\n]+?)\*\*', r'\\textbf{\1}', s)          # **bold**
    s = re.sub('\x01(\\d+)\x01', lambda m: local[int(m.group(1))], s)
    s = re.sub('\x00I(\\d+)\x00',
               lambda m: ('$' + normalize_math(INLINE[int(m.group(1))]) + '$')
               if normalize_math(INLINE[int(m.group(1))]) else '', s)
    s = re.sub('\x00F(\\d+)\x00',
               lambda m: r'\footnote{' + render_footnote(FOOTNOTES[int(m.group(1))]) + '}', s)
    return s


def render_footnote(raw):
    raw = re.sub(r'\\n(?![A-Za-z])', ' ', raw).strip()
    out, last = [], 0
    for m in re.finditer(r'\$([^$]+?)\$', raw):
        out.append(render_inline(raw[last:m.start()]))
        n = normalize_math(m.group(1))
        out.append(('$' + n + '$') if n else '')
        last = m.end()
    out.append(render_inline(raw[last:]))
    return ''.join(out).strip()


def plain_text(s):
    """plain text for PDF bookmarks (no math, no commands)"""
    s = re.sub('\x00I(\\d+)\x00', '', s)
    s = re.sub('\x00[DTGF](\\d+)\x00', '', s)
    s = BARE_CMD.sub('', s)
    s = re.sub(r'[\\${}*#_^~]', '', s)
    s = SYM_RE.sub('', s)
    return s.strip()


# ---------------------------------------------------------------- tables / images
def convert_table(html):
    rows = re.findall(r'<tr\b[^>]*>(.*?)</tr>', html, re.S)
    parsed, maxcols = [], 0
    for r in rows:
        cells = re.findall(r'<t[dh]\b([^>]*)>(.*?)</t[dh]>', r, re.S)
        row, cols = [], 0
        for attr, content in cells:
            m = re.search(r'colspan="?(\d+)"?', attr)
            span = int(m.group(1)) if m else 1
            content = re.sub(r'</?[a-zA-Z][^<>]*>', '', content).strip()
            row.append((span, render_inline(content)))
            cols += span
        maxcols = max(maxcols, cols)
        parsed.append(row)
    ncol = maxcols if maxcols > 0 else 1
    w = round(0.90 / ncol, 3)
    colspec = '|' + ''.join(r'>{\centering\arraybackslash}p{%s\linewidth}|' % w for _ in range(ncol))
    out = [r'\begingroup\small\renewcommand{\arraystretch}{1.35}',
           r'\begin{longtable}{' + colspec + '}', r'\hline']
    for row in parsed:
        cs = []
        for span, content in row:
            if span > 1:
                cs.append(r'\multicolumn{%d}{|>{\centering\arraybackslash}p{%s\linewidth}|}{%s}'
                          % (span, round(w * span, 3), content))
            else:
                cs.append(content)
        out.append(' & '.join(cs) + r' \\ \hline')
    out += [r'\end{longtable}', r'\endgroup']
    return '\n'.join(out)


def convert_img(tag):
    msrc = re.search(r'src="([^"]+)"', tag)
    if not msrc:
        return ''
    fn = IMGMAP.get(msrc.group(1))
    if not fn:
        return r'\par{\centering[\,\textit{图}\,]\par}'
    mw = re.search(r'width="?(\d+)%', tag)
    if mw:
        pct = max(0.05, min(0.92, int(mw.group(1)) / 100.0))
        opt = r'width=%s\linewidth,height=0.85\textheight,keepaspectratio' % round(pct, 3)
    else:
        opt = r'width=0.78\linewidth,height=0.85\textheight,keepaspectratio'
    return r'\par\medskip{\centering\includegraphics[%s]{%s}\par}\medskip' % (opt, fn)


# ---------------------------------------------------------------- footnote recovery
def recover_footnotes(md, json_path):
    """turn ①..⑩ markers into real \\footnote{} using the sibling JSON; returns new md"""
    try:
        J = json.load(open(json_path, encoding='utf-8'))
    except Exception:
        return md

    def marker_re(g):
        return r'\$\s*\^\{?\s*' + re.escape(g) + r'\s*\}?\s*\$'

    for page in J:
        bl = page.get("prunedResult", {}).get("parsing_res_list", [])
        fns = [b for b in bl if b["block_label"] in ("footnote", "vision_footnote")
               and re.match(r'\s*[' + GL + ']', b["block_content"])]
        if not fns:
            continue
        ref = re.sub(r'\\n(?![A-Za-z])', ' ',
                     "".join(b["block_content"] for b in bl
                             if b["block_label"] not in ("footnote", "vision_footnote")))
        for b in fns:
            g = re.match(r'\s*([' + GL + '])', b["block_content"]).group(1)
            text = re.sub(r'^\s*[' + GL + r']\s*', '', b["block_content"]).strip()
            m = re.search(marker_re(g), ref)
            placed = False
            if m:
                ctx = re.sub(r'\s+', '', ref[max(0, m.start() - 22):m.start()])[-12:]
                if ctx:
                    patt = '(' + r'\s*'.join(re.escape(c) for c in ctx) + r')\s*' + marker_re(g)
                    md, n = re.subn(patt, '\\g<1>\x00F' + str(len(FOOTNOTES)) + '\x00', md, count=1)
                    if n == 1:
                        FOOTNOTES.append(text)
                        placed = True
            if not placed:
                FOOTNOTE_LEFTOVERS.append(text)
    return md


# ---------------------------------------------------------------- preamble
PREAMBLE_TMPL = r"""\documentclass[UTF8,fontset=%(fontset)s,11pt,openany]{ctexbook}
\usepackage{amsmath,amssymb,amsthm,mathrsfs}
\usepackage{graphicx}
\usepackage{longtable}
\usepackage{array}
\usepackage{multirow}
\usepackage[a4paper,margin=2.4cm]{geometry}
\usepackage{enumitem}
\usepackage{textcomp}
\usepackage{fancyhdr}
\usepackage[hidelinks,bookmarksnumbered=false]{hyperref}
\newcommand{\cnum}[1]{\textcircled{\scriptsize #1}}
\graphicspath{{%(imgdir)s/}{./}}
\setcounter{tocdepth}{1}
\setcounter{secnumdepth}{-1}
\renewcommand{\arraystretch}{1.2}
\allowdisplaybreaks
\setlength{\parindent}{2em}
\setlength{\emergencystretch}{3em}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small\leftmark}
\fancyhead[R]{\small\thepage}
\renewcommand{\headrulewidth}{0.4pt}
\title{%(title)s}
\author{%(author)s}
\date{}
\begin{document}
\frontmatter
%(maketitle)s
\tableofcontents
\mainmatter
"""


def detect_title(md, json_path):
    try:
        J = json.load(open(json_path, encoding='utf-8'))
        for page in J:
            for b in page.get("prunedResult", {}).get("parsing_res_list", []):
                if b["block_label"] == "doc_title":
                    return re.sub(r'^#+\s*', '', b["block_content"]).strip()
    except Exception:
        pass
    m = re.search(r'^#\s+(.*\S)\s*$', md, re.M)
    return m.group(1).strip() if m else "Document"


# ---------------------------------------------------------------- main pipeline
def convert(md, json_path, imgdir, fontset, title, author, drop_toc, do_footnotes):
    md = re.sub(r'\\n(?![A-Za-z])', ' ', md)                       # literal "\n" artifacts
    md = re.sub(r'\\textcircled\{\s*(\d+)\s*\}',
                lambda m: CIRCLED[int(m.group(1)) - 1] if 1 <= int(m.group(1)) <= 10 else m.group(0), md)
    if drop_toc:
        md = re.sub(r'\n##\s*目录\s*\n.*?(?=\n##\s)', '\n', md, flags=re.S)
    if do_footnotes:
        md = recover_footnotes(md, json_path)

    # global protection passes (order matters)
    md = re.sub(r'\$\$(.+?)\$\$',
                lambda m: '\n\x00D%d\x00\n' % (DISPLAY.append(m.group(1)) or len(DISPLAY) - 1),
                md, flags=re.S)
    md = re.sub(r'\$([^$]+?)\$',
                lambda m: '\x00I%d\x00' % (INLINE.append(m.group(1)) or len(INLINE) - 1), md)
    md = re.sub(r'<table\b.*?</table>',
                lambda m: '\n\x00T%d\x00\n' % (TABLE.append(convert_table(m.group(0))) or len(TABLE) - 1),
                md, flags=re.S)
    md = re.sub(r'<img\b[^>]*?>',
                lambda m: '\n\x00G%d\x00\n' % (IMG.append(convert_img(m.group(0))) or len(IMG) - 1),
                md, flags=re.S)
    md = re.sub(r'<div[^>]*text-align:\s*center[^>]*>', '\n\x00CB\x00\n', md)
    md = re.sub(r'</div>', '\n\x00CE\x00\n', md)
    md = re.sub(r'</?[a-zA-Z][^<>]*>', '', md)        # strip only REAL leftover tags

    HEAD = re.compile(r'^(#{1,6})\s+(.*\S)\s*$')
    BLOCK_TOK = re.compile(r'^\x00(D|T|G)(\d+)\x00$')
    CHAPTER_PAT = re.compile(r'第\s*\d+\s*章|chapter\s+\d+', re.I)
    FRONT_BACK = {'原书序', '原书前言', '目录', '参考文献', '符号列表', '名词索引', '译者前言',
                  '内容简介', 'preface', 'references', 'bibliography', 'index'}

    out, buf = [], []

    def flush():
        if buf:
            text = ''.join(buf).strip()
            if text:
                out.append(render_inline(text)); out.append('')
            buf.clear()

    def heading(level, t):
        flush()
        t = re.sub('\x00F(\\d+)\x00',
                   lambda m: FOOTNOTE_LEFTOVERS.append(FOOTNOTES[int(m.group(1))]) or '', t)
        printed = render_inline(t)
        plain = plain_text(t) or 'sec'
        ts = t.strip()
        if level <= 2:
            is_chapter = (level == 1 or bool(CHAPTER_PAT.search(ts))
                          or ts in FRONT_BACK or '沃塞曼' in ts)
            if is_chapter:
                out.append(r'\chapter*{%s}' % printed)
                out.append(r'\markboth{%s}{%s}' % (plain, plain))
                out.append(r'\phantomsection\addcontentsline{toc}{chapter}{\texorpdfstring{%s}{%s}}'
                           % (printed, plain))
            else:                                  # OCR mis-detected body text as heading
                out.append(printed); out.append('')
        elif level == 3:
            out.append(r'\section*{%s}' % printed)
            out.append(r'\phantomsection\addcontentsline{toc}{section}{\texorpdfstring{%s}{%s}}'
                       % (printed, plain))
        elif level == 4:
            out.append(r'\subsection*{%s}' % printed)
        else:
            out.append(r'\subsubsection*{%s}' % printed)
        out.append('')

    for line in md.split('\n'):
        st = line.strip()
        if st == '':
            flush(); continue
        if st == '\x00CB\x00':
            flush(); out.append(r'\begin{center}'); continue
        if st == '\x00CE\x00':
            flush(); out.append(r'\end{center}'); continue
        mb = BLOCK_TOK.match(st)
        if mb:
            flush()
            kind, idx = mb.group(1), int(mb.group(2))
            if kind == 'D':
                norm = normalize_math(DISPLAY[idx])
                if not norm:
                    out.append('')
                elif re.match(r'\\begin\{(align|gather|equation|multline|eqnarray|flalign)\*?\}', norm):
                    out.append(norm)
                else:
                    out.append(r'\[' + norm + r'\]')
            elif kind == 'T':
                out.append(TABLE[idx])
            elif kind == 'G':
                out.append(IMG[idx])
            out.append('')
            continue
        mh = HEAD.match(line)
        if mh:
            heading(len(mh.group(1)), mh.group(2)); continue
        buf.append(st)
    flush()

    body = re.sub(r'\n{3,}', '\n\n', '\n'.join(out))

    if FOOTNOTE_LEFTOVERS:
        extra = ['', r'\chapter*{补充脚注}',
                 r'\phantomsection\addcontentsline{toc}{chapter}{补充脚注}', '',
                 '以下脚注在 OCR 识别的正文中未保留对应的标注位置，按原文出现顺序补录于此。', '',
                 r'\begin{itemize}']
        extra += [r'\item ' + render_footnote(t) for t in FOOTNOTE_LEFTOVERS]
        extra += [r'\end{itemize}', '']
        body += '\n' + '\n'.join(extra)

    preamble = PREAMBLE_TMPL % {
        'fontset': fontset, 'imgdir': imgdir,
        'title': r'\bfseries ' + title, 'author': author,
        'maketitle': r'\maketitle' if title else '',
    }
    return preamble + body + "\n\\end{document}\n"


def main():
    ap = argparse.ArgumentParser(description="PaddleOCR markdown -> XeLaTeX book")
    ap.add_argument("input", help="the *.md produced by PaddleOCR-VL")
    ap.add_argument("--json", help="sibling *.json (default: input with .md->.json); used for footnotes")
    ap.add_argument("--imgmap", help="image url->file map json (from download_images.py)")
    ap.add_argument("--imgdir", default="imgs", help="image folder name for \\graphicspath (default: imgs)")
    ap.add_argument("--out", help="output .tex path (default: book.tex next to input)")
    ap.add_argument("--fontset", default="mac",
                    help="ctex fontset: mac | windows | ubuntu | fandol (default: mac)")
    ap.add_argument("--title", help="title for the generated title page (default: auto from JSON/#)")
    ap.add_argument("--author", default="", help="author line for the title page")
    ap.add_argument("--keep-ocr-toc", action="store_true", help="keep the OCR's own messy 目录")
    ap.add_argument("--no-footnotes", action="store_true", help="do not recover footnotes from JSON")
    a = ap.parse_args()

    md_text = open(a.input, encoding="utf-8").read()
    json_path = a.json or (os.path.splitext(a.input)[0] + ".json")
    out_path = a.out or os.path.join(os.path.dirname(os.path.abspath(a.input)), "book.tex")
    if a.imgmap and os.path.exists(a.imgmap):
        IMGMAP.update(json.load(open(a.imgmap, encoding="utf-8")))
    title = a.title or detect_title(md_text, json_path)

    tex = convert(md_text, json_path, a.imgdir, a.fontset, title, a.author,
                  drop_toc=not a.keep_ocr_toc, do_footnotes=not a.no_footnotes)
    open(out_path, "w", encoding="utf-8").write(tex)
    print("wrote %s  (display=%d inline=%d table=%d img=%d footnotes=%d, +%d in addendum)"
          % (out_path, len(DISPLAY), len(INLINE), len(TABLE), len(IMG),
             len(FOOTNOTES), len(FOOTNOTE_LEFTOVERS)))


if __name__ == "__main__":
    main()
