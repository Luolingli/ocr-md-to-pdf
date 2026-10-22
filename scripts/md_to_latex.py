#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convert a PaddleOCR-VL markdown export (Chinese / math textbook) into a single
self-contained LaTeX file that XeLaTeX compiles into a fully vector PDF book.

Deterministic core (handles the PaddleOCR quirks automatically):
  * inline `$ ... $` / display `$$ ... $$` math with spaces and embedded CJK
    (CJK runs inside math are wrapped in \\text{} so the math font never misses glyphs)
  * HTML <table> -> bordered longtable (colspan aware)
  * <img ...> -> \\includegraphics from a locally downloaded image map
  * <div style="text-align:center"> -> center environment
  * circled footnote markers ①..⑩, raw \\textcircled{n}, HTML entities, stray text
    `<`/`>`, double sub/superscripts, malformed arrays / \\left..\\right, unbalanced braces
  * recovers footnotes from the sibling *.json (the .md export silently drops them)
  * drops the OCR's own messy table of contents (a clean navigable TOC is generated)

v2 — AI-in-the-loop hybrid (so the *skill* beats a bare script on accuracy):
  * --report review.json   : emit ONLY the high-risk spots (repaired formulas, unmapped
                             glyphs, demoted headings, unplaced footnotes, missing images),
                             each with a stable id and, where matchable, the original-page
                             {page,bbox} so an AI can inspect the source image and repair.
  * --overrides FILE       : {math_id: corrected_latex_body} — apply the AI's evidence-based
                             fixes reproducibly, no hand-editing of generated output.
  * --symbols FILE         : {char: latex} merged into the symbol map (for unmapped glyphs).

Pure standard library. Usage:  python3 md_to_latex.py INPUT.md [options]
"""
import re, json, os, argparse, hashlib

CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩'
GL = CIRCLED

# stores populated during conversion
DISPLAY, INLINE, TABLE, IMG = [], [], [], []
FOOTNOTES, FOOTNOTE_LEFTOVERS = [], []
IMGMAP, OVERRIDES = {}, {}
FORMULA_INDEX = {}                       # normalized formula -> {page, bbox}
REPORT = {"math": [], "unmapped_chars": {}, "demoted_headings": [],
          "footnotes_unplaced": [], "missing_images": [],
          "broken_inline_spans": [], "missing_headings": [],
          # 20261021 additions — one key per improvement, each with a count (nothing silent):
          "normalize_report": {},        # A: homoglyph/symbol rewrites, counted by rule
          "toc_candidates": [],          # B: untitled printed-TOC blocks (report-only!)
          "dropped_blocks": [],          # B: only what --drop-block removed, after confirm
          "heading_level_fixes": [],     # C: § headings normalized to h3 (were ####/#####…)
          "cn_num_headings": [],         # C: （一）（二）headings mapped to a level
          "missing_heading_candidates": [],  # C: TOC-style lines with no heading mark
          "toc_only_entries": [],        # B: TOC sub-entries with no body heading (not an error)
          "untitled_toc": []}            # B: TOC-style dot-leader rows left in the body (report-only)
# 改进 A: normalize_math rewrite rules. NORM_QUIET = deterministic symbol normalization
# (kept out of the `math` AI-review queue, still counted in normalize_report);
# everything else (structure repairs) stays in `math` with all reasons.
NORM_QUIET = {'unicode_greek', 'cyrillic_homoglyph', 'junk_marks', 'mathbf_greek'}
NORM_COUNTS = {}

_seen_math = set()
TOC_LEVELS = {}                          # normalized printed-TOC title -> heading level (1/2/3, or 0 = report only)
TOC_TITLES = []                          # (original title, normalized) in TOC order
TOC_ENTRIES = []                         # [{title, norm, level, indent}] — TOC order + indentation
HEADINGS_SEEN = set()                    # normalized titles of all body headings
HEAD_ORD_MATCH = []                      # (level, canon) of headings that DID match the TOC
CN_NUM_HEADINGS = []                     # [(level, title, sector)]
SECTION_PREV = [None]                    # enclosing § heading text (for the TOC-candidate check)
CN_NUM_LEVEL = 5                         # default for （一）…= input level 5 (subsubsection*)
MISSING_BASELINE = 0                     # canonical-match baseline for missing_heading_candidates


def hid(raw):
    return hashlib.sha1(raw.strip().encode('utf-8')).hexdigest()[:8]


def locate(raw):
    return FORMULA_INDEX.get(re.sub(r'\s+', '', raw.strip().strip('$')))


# ---------------------------------------------------------------- math helpers
WRAP_RE = re.compile(r'[　-〿一-鿿＀-￯‘’“”…·—–]+')
ARR_RE = re.compile(r'(\\begin\{array\}\{)([^{}]*)(\})(.*?)(\\end\{array\})', re.S)


def fix_arrays(c):
    def rep(m):
        body = m.group(4)
        maxc = 1
        for r in re.split(r'\\\\', body):
            if r.strip():
                maxc = max(maxc, len(r.split('&')))
        spec = m.group(2)
        ncols = len(re.findall(r'[lcr]', spec))
        if ncols < maxc:
            need = maxc - ncols
            if '|' in spec:
                # under-counted augmented matrix: the missing columns belong to the
                # coefficient part, so insert them before the last '|' to keep the
                # A|b separator (naively replacing with 'l'*maxc wipes the bar out)
                i = spec.rfind('|')
                spec = spec[:i] + 'l' * need + spec[i:]
            else:
                spec = 'l' * maxc
        return m.group(1) + spec + m.group(3) + body + m.group(5)
    return ARR_RE.sub(rep, c)


def balance_braces(c):
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


# 改进 A — Unicode homoglyphs inside MATH (a literal 'π' in $...$ is tofu/unmapped).
# Only Greek *italic* forms get translated: a capital Sigma/Omega standing for a number
# (∑ / 样本空间 Ω) is a symbol too, but Γ/Σ/Ω are ambiguous enough that mapping them to
# \Gamma/\Sigma/\Omega is still right inside math. (Outside math they stay a report item.)
UNI_GREEK_MATH = {
    'π': r'\pi', 'φ': r'\varphi', 'ϕ': r'\phi', 'θ': r'\theta', 'α': r'\alpha',
    'β': r'\beta', 'γ': r'\gamma', 'δ': r'\delta', 'ε': r'\varepsilon',
    'ϵ': r'\epsilon', 'ζ': r'\zeta', 'η': r'\eta', 'λ': r'\lambda', 'μ': r'\mu',
    'ν': r'\nu', 'ξ': r'\xi', 'ρ': r'\rho', 'σ': r'\sigma', 'τ': r'\tau',
    'υ': r'\upsilon', 'χ': r'\chi', 'ψ': r'\psi', 'ω': r'\omega',
    'Γ': r'\Gamma', 'Δ': r'\Delta', 'Θ': r'\Theta', 'Λ': r'\Lambda', 'Ξ': r'\Xi',
    'Π': r'\Pi', 'Σ': r'\Sigma', 'Υ': r'\Upsilon', 'Φ': r'\Phi', 'Ψ': r'\Psi',
    'Ω': r'\Omega',
}
UNI_GREEK_RE = re.compile('|'.join(re.escape(k) for k in UNI_GREEK_MATH))

# Cyrillic homoglyphs of Latin letters (OCR confuses fonts). ONLY the unambiguous ones:
#  в/и/т/н/д are left alone (they collide with real Cyrillic words → needs a human).
CYR_MAP = {'а': 'a', 'е': 'e', 'ё': 'e', 'к': 'k', 'м': 'm', 'о': 'o', 'р': 'p',
           'с': 'c', 'у': 'y', 'х': 'x', 'г': 'r', 'Ч': 'H', 'ш': 'w', 'й': 'i',
           'ч': 'h', 'б': 'b', 'ы': '3', 'О': 'O', 'З': '3'}
# 'Xinчин' (Xin Chin 大数定律) → 'Xinc3n' 之外不猜：'ын' → '3n' 只在下标位置有证据
CYR_SIGNATURE = 'Xinчин'
# A Cyrillic run glued to a Latin letter/digit on BOTH sides counts as a homoglyph
# token (Xinчин, Bектор). A stand-alone Cyrillic word (Чебышев) is left alone.
# Only a *mixed* Latin/Cyrillic token is a homoglyph garble (Xinчин, Bектор).
# A pure Cyrillic word (Остроградский, Чебышев) is a real name — leave it to the CJK
# fallback font, never transliterate it.
CYR_CONTEXT_RE = re.compile(r'(?<=[A-Za-z0-9])([\u0400-\u04FF]+)')
# 非 ASCII 杂符（乱码）：Tamil 组合符/零宽字符，以及 OCR 打在 'р' 前面的 U+0BC6
JUNK_MARKS_RE = re.compile('[\u0bc6\u0bcd\u200b\u200c\u200d\ufeff]')
DASHED_RE = re.compile('[\u2010-\u2015\u00ad\u2043\uff0d]+')


def _cyr_join(m):
    return ''.join(CYR_MAP.get(c, c) for c in m.group(1))


def _dash_join(m):
    return '-' if '-' in m.group(0) else '--'


CYR_WORD_RE = re.compile(r"[\u0400-\u04FF]+")


def wrap_cyrillic_words(s):
    """Wrap stand-alone Cyrillic words (real names: Остроградский, Чебышев) in \\text{}
    so they render with the CJK/fallback font instead of vanishing from lmroman
    (XeTeX gives "Missing character U+0435 in font lmroman10-regular")."""
    if not CYR_WORD_RE.search(s):
        return s, 0
    n = 0

    def rep(m):
        nonlocal n
        n += 1
        return r"\text{%s}" % m.group(0)
    return CYR_WORD_RE.sub(rep, s), n


def normalize_math(c, reasons=None):
    """normalize OCR math to compilable LaTeX; if `reasons` is a list, record which
    repairs fired (a non-empty list => the formula is a candidate for AI review)."""
    c = decode_entities(c).strip()
    if not c:
        return ""
    c = re.sub(r'(?<!\\)%', r'\\%', c)
    for k, ch in enumerate(CIRCLED, 1):
        if ch in c:
            c = c.replace(ch, r'{\text{\cnum{%d}}}' % k)

    def step(pattern, repl, tag, s):
        ns = re.sub(pattern, repl, s)
        if reasons is not None and ns != s:
            reasons.append(tag)
        return ns

    # 改进 A1: literal Unicode Greek inside math -> the LaTeX command (π -> \pi, φ -> \varphi).
    # The math font never sees a literal 'π'; outside math it stays an unmapped_chars item.
    c = step(UNI_GREEK_RE, lambda m: UNI_GREEK_MATH[m.group(0)] + ' ', 'unicode_greek', c)
    # 改进 A2: Cyrillic homoglyphs glued to a Latin/number token (Xinчин, Bектор) -> Latin.
    # Only in that context: a *real* Cyrillic word after a space (Чебышев) is a person's name
    # and must stay Cyrillic — it is reported as unmapped for a human, never rewritten.
    c = step(CYR_CONTEXT_RE, _cyr_join, 'cyrillic_homoglyph', c)
    c = step(JUNK_MARKS_RE, '', 'junk_marks', c)
    c = step(r'_([A-Za-z0-9])_([A-Za-z0-9])', r'_{\1\2}', 'double_subscript', c)
    c = step(r'\^([A-Za-z0-9])\^([A-Za-z0-9])', r'^{\1\2}', 'double_superscript', c)
    # PaddleOCR wraps CJK text inside math as \mathrm{~ 中文 ~}; \text{} is the
    # legal home for CJK (deterministic, so not flagged for review).
    c = re.sub(r'\\mathrm\{~\s*([^{}~]*?)[~\s]*\}',
               lambda m: (r'\text{%s}' % re.sub(r'\s+', ' ', m.group(1)).strip())
                         if re.search(r'[一-鿿]', m.group(1)) else m.group(0), c)
    # \mathbf{Greek} is undefined in OT1 math and makes XeTeX request a bogus
    # U+0005/U+0002 glyph from the bold Latin font ("Missing character: ^^E in
    # lmromandemi" — a misleading diagnostic). \boldsymbol is the bold math font
    # (amssymb is already in the preamble). Covers BOTH spellings:
    #   * a literal Greek char        \mathbf{Θ}          -> \boldsymbol{Θ}
    #   * a Greek command             \mathbf{\Theta}     -> \boldsymbol{\Theta}
    #   * a math-alphabet command     \mathbf{\mathbb{R}} -> \boldsymbol{\mathbb{R}}
    #   * nested braces               \mathbf{\hat{\mu}}  -> \boldsymbol{\hat{\mu}}
    c = re.sub(r'\\mathbf\{(?=(?:[^\x00-\x7f]|\\[A-Za-z]+))',
               r'\\boldsymbol{', c)
    c = step(r'\\mathbf\{([\u0391-\u03A9\u03B1-\u03C9]|\\[A-Za-z]+)\}', r'\\boldsymbol{\1}',
             'mathbf_greek', c)
    c = WRAP_RE.sub(lambda m: r'\text{' + m.group(0) + '}', c)
    nc = fix_arrays(c)
    if reasons is not None and nc != c:
        reasons.append('array_columns')
    c = nc
    nc = re.sub(r'\\left(?![A-Za-z\s(\[\]{}|./<>)\\])', r'\\left.', c)
    nc = re.sub(r'\\right(?![A-Za-z\s(\[\]{}|./<>)\\])', r'\\right.', nc)
    if reasons is not None and nc != c:
        reasons.append('left_right_delim')
    c = nc
    nc = balance_braces(c)
    if reasons is not None and nc != c:
        reasons.append('brace_imbalance')
    return nc


def render_math(raw, kind):
    """resolve a math block: apply an AI override if present, else normalize and (if it
    needed repair) record it in the review report. Returns the LaTeX body."""
    mid = hid(raw)
    if mid in OVERRIDES:
        return OVERRIDES[mid]
    reasons = []
    body = normalize_math(raw, reasons)
    # 改进 A: every normalization rewrite is COUNTED (never silent) — normalize_report.json
    for r in reasons:
        if r in NORM_COUNTS:
            NORM_COUNTS[r] += 1
    if reasons and mid not in _seen_math:
        _seen_math.add(mid)
        # the symbols-homoglyph rewrites are deterministic normalizations, not "repairs" that
        # need AI review: keep them out of the `math` queue (reported in normalize_report.json)
        review = [r for r in reasons if r not in NORM_QUIET]
        if review:
            item = {"id": mid, "kind": kind, "reasons": sorted(set(review)),
                    "raw": raw.strip(), "rendered": body}
            loc = locate(raw)
            if loc:
                item.update(loc)
            REPORT["math"].append(item)
    return body


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
    # 改进 A: units / CJK numerals that PaddleOCR emits as odd glyphs
    '℃': r'${}^{\circ}\mathrm{C}$', '°': r'${}^{\circ}$',
    'Ⅶ': 'VII', 'Ⅷ': 'VIII', 'Ⅸ': 'IX', 'Ⅹ': 'X',
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


def rebuild_sym_re():
    global SYM_RE
    SYM_RE = re.compile('|'.join(re.escape(k) for k in sorted(SYM, key=len, reverse=True)))


def render_inline(s):
    if s is None:
        return ""
    s = (s.replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
          .replace('&quot;', '"').replace('&#39;', "'"))
    local = []

    def stash(latex):
        local.append(latex)
        return '\x01%d\x01' % (len(local) - 1)

    # 改进 A (text side): a literal Unicode Greek letter in body text (Γ函数, 区域 Ω, Σ上)
    # -> $\\Gamma$ / $\\Omega$ — otherwise it is tofu in CJK text and an unmapped_chars item.
    # Done before SYM/ESC so the injected '$' is not escaped. '℃' has its own mapping.
    def _grk(m):
        i = m.start()
        left = s[max(0, i - 2):i]
        right = s[m.end():m.end() + 2]
        # 正文里出现在中文左邻的字面希腊字母（"区域Ω上"、"设Ω由..."）多是符号漏了 $，
        # 转成 $\Omega$ 与上下文一致；"Γ函数" 这种 (希腊字母+中文) 惯例写法保持原样
        if re.search(r'[一-鿿]', right[:1]):
            return m.group(0)
        NORM_COUNTS['unicode_greek_text'] = NORM_COUNTS.get('unicode_greek_text', 0) + 1
        return stash('$' + UNI_GREEK_MATH[m.group(0)] + '$')
    s = UNI_GREEK_RE.sub(_grk, s)
    # 正文里的纯西里尔词（真实人名 Остроградский/Чебышев）包进 \text{}，交给 CJK 回退字体；
    # 否则 lmroman 没有这些字形，XeTeX 只报 Missing character，正文里直接缺字。
    if CYR_WORD_RE.search(s):
        n_cyr = len(CYR_WORD_RE.findall(s))
        s = CYR_WORD_RE.sub(lambda m: stash(r'\cjktext{%s}' % m.group(0)), s)
        NORM_COUNTS['cyrillic_text'] = NORM_COUNTS.get('cyrillic_text', 0) + n_cyr
    s = BARE_CMD.sub(lambda m: stash('$' + m.group(0) + '$'), s)
    s = SYM_RE.sub(lambda m: stash(SYM[m.group(0)]), s)
    s = ESC_RE.sub(lambda m: ESC[m.group(0)], s)
    s = re.sub(r'\*\*([^*\n]+?)\*\*', r'\\textbf{\1}', s)
    s = re.sub('\x01(\\d+)\x01', lambda m: local[int(m.group(1))], s)

    def inl(m):
        body = render_math(INLINE[int(m.group(1))], 'inline')
        return ('$' + body + '$') if body else ''
    s = re.sub('\x00I(\\d+)\x00', inl, s)
    s = re.sub('\x00F(\\d+)\x00',
               lambda m: r'\footnote{' + render_footnote(FOOTNOTES[int(m.group(1))]) + '}', s)
    return s


def render_footnote(raw):
    raw = re.sub(r'\\n(?![A-Za-z])', ' ', raw).strip()
    out, last = [], 0
    for m in re.finditer(r'\$([^$]+?)\$', raw):
        out.append(render_inline(raw[last:m.start()]))
        body = render_math(m.group(1), 'inline')
        out.append(('$' + body + '$') if body else '')
        last = m.end()
    out.append(render_inline(raw[last:]))
    return ''.join(out).strip()


def plain_text(s):
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
        REPORT["missing_images"].append(msrc.group(1))
        return r'\par{\centering[\,\textit{图}\,]\par}'
    mw = re.search(r'width="?(\d+)%', tag)
    if mw:
        pct = max(0.05, min(0.92, int(mw.group(1)) / 100.0))
        opt = r'width=%s\linewidth,height=0.85\textheight,keepaspectratio' % round(pct, 3)
    else:
        opt = r'width=0.78\linewidth,height=0.85\textheight,keepaspectratio'
    return r'\par\medskip{\centering\includegraphics[%s]{%s}\par}\medskip' % (opt, fn)


# ---------------------------------------------------------------- JSON helpers
def build_formula_index(J):
    for pi, page in enumerate(J):
        for b in page.get("prunedResult", {}).get("parsing_res_list", []):
            if b["block_label"] in ("display_formula", "inline_formula", "formula"):
                key = re.sub(r'\s+', '', b["block_content"].strip().strip('$'))
                if key and key not in FORMULA_INDEX:
                    FORMULA_INDEX[key] = {"page": pi, "bbox": b.get("block_bbox")}


def recover_footnotes(md, J):
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


# ---------------------------------------------------------------- unmapped glyph scan
_SAFE = set('，。、；：？！“”‘’（）《》【】—…·「」『』〔〕％　')


def scan_unmapped(body):
    for ch in body:
        o = ord(ch)
        if o < 128:
            continue
        if 0x4e00 <= o <= 0x9fff or 0x3400 <= o <= 0x4dbf:   # CJK ideographs
            continue
        if 0x00a0 <= o <= 0x024f:                            # accented Latin (font has it)
            continue
        if 0x2010 <= o <= 0x2015:                            # dashes
            continue
        if ch in _SAFE:
            continue
        REPORT["unmapped_chars"][ch] = REPORT["unmapped_chars"].get(ch, 0) + 1


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
%% Stand-alone Cyrillic words (real names such as Ostrogradsky, Chebyshev) have no
%% glyphs in the Latin text fonts; render them with the CJK font, which carries the
%% full Cyrillic range, instead of letting XeTeX drop them ("Missing character").
\newcommand{\cjktext}[1]{{\CJKfamily{\CJKrmdefault}#1}}
\providecommand{\xlongequal}[1]{\mathrel{\overset{#1}{=}}}
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
%% source-book pagination (bookpage markers): the footer shows the printed
%% book page so assignments like "P29" resolve at a glance
\newcounter{bookpage}
\fancyfoot[C]{\small\ifnum\value{bookpage}>0 P\thebookpage\fi}
\title{%(title)s}
\author{%(author)s}
\date{}
\begin{document}
%% absolute page numbering from the title page: \frontmatter/\mainmatter would
%% reset \thepage at the body, desynchronizing the book-page map (labels store
%% \thepage) from the viewer's physical PDF pages
%(maketitle)s
\thispagestyle{empty}
\tableofcontents
"""


# A real book title never starts like a structural heading: 第N章/第N篇, § N, or
# 习题/目录. PaddleOCR's doc_title detection misfires on partial scans and returns
# exactly these (高数上册: 第四章 / 第六章 / § 3 曲面、曲线和二次曲面).
CHAPTER_HEAD_RE = re.compile(
    r'^第\s*(?:\d+|[一二三四五六七八九十百]+)\s*[章篇]'
    r'|^§\s*\d'
    r'|^(?:目录|习题|附录|参考文献|索引)\s*$'
    r'|^部分习题答案')


def detect_title(md, J):
    """Book title for \title{}. PaddleOCR's `doc_title` block is unreliable — on a
    partial-scan PDF it happily labels the first *chapter* as the document title
    (高数上册: doc_title == "第四章 矩阵和线性方程组"), which then becomes the only
    text on PDF page 1. A chapter/part heading is never a book title: reject it."""
    for page in J:
        for b in page.get("prunedResult", {}).get("parsing_res_list", []):
            if b["block_label"] == "doc_title":
                cand = re.sub(r'^#+\s*', '', b["block_content"]).strip()
                if cand and not CHAPTER_HEAD_RE.match(cand):
                    return cand
    # Fallback: an h1 that is a chapter/part/§ heading is OCR noise, not the book
    # title (高数上册 has exactly three stray h1s: 第四章/第六章/§ 3 …, all noise).
    for m in re.finditer(r'^#\s+(.*\S)\s*$', md, re.M):
        cand = m.group(1).strip()
        if not CHAPTER_HEAD_RE.match(cand) and not re.match(r'^§\s*\d', cand):
            return cand
    return "Document"


# ------------------------------------------------------------ printed-TOC cross-check
def norm_title(s):
    """canonical form for matching a TOC entry against a body heading. Body headings
    are seen AFTER inline math was tokenized, so the tokens are stripped here (the
    TOC side is parsed before tokenization and its $...$ math is stripped)."""
    s = re.sub('\x00I(\\d+)\x00', '', s)
    s = re.sub(r'\$[^$]*\$', '', s)
    s = re.sub(r'\s+', '', s)
    s = re.sub(r'[.。…·\-—]+$', '', s)   # trailing dot leaders (".....", "……")
    return s.lower()


def canon_title(s):
    """looser canonical form for the missing-heading check: OCR may split the math of
    the same title differently in the printed TOC vs the body heading (e.g.
    `$ C^{*} $` vs `C $ ^{*} $`), so keep the math content but drop all math
    punctuation, commands and non-alphanumerics (lowercased)."""
    s = s.lower()
    s = re.sub(r'\\[A-Za-z]+\*?', '', s)
    s = re.sub(r'[$^_{}~\\*]', '', s)
    s = re.sub(r'[^0-9a-z\u4e00-\u9fff]', '', s)
    return s


def parse_toc(md):
    """Read the book's own printed TOC — the ground truth for structure — BEFORE it
    is dropped. Numbered entries fix the heading level of any body heading with the
    same title (OCR often mis-levels headings):  第N章 -> chapter(1), N.M -> section(2),
    N.M.K -> subsection(3). Unnumbered entries (appendices, 参考文献, ...) are recorded
    at level 0: report-only, never re-leveled. TOC entries whose title never appears
    as a body heading end up in REPORT["missing_headings"] (OCR lost the heading)."""
    # 改进 B: a `目录` line with a wrong mark level used to disable the whole TOC (下册 has
    # NO 目录 line at all). Accept any mark level and stop only at a chapter-level heading —
    # a `##### 习题` inside the TOC page used to cut the parse short.
    m = re.search(r'\n(#{1,6})\s*目\s*录[^\n]*\n(.*?)(?=\n#{1,2}\s)', md, flags=re.S)
    if not m:
        return
    toc_body = m.group(2)
    # 改进 B/C: PaddleOCR leaves the dot leaders in place ("§1 函数 ..... 3"); the old
    # `\s+\d+$` strip did not remove them, so every 上册 TOC entry failed to match its body
    # heading (142 false missing_headings). Strip leaders first; also record the printed
    # INDENT (0 / 1 / 2) — it is the only level evidence for （一）… entries, which carry
    # no number of their own.
    for line in toc_body.split('\n'):
        raw = line.rstrip()
        s = raw.strip()
        if not s:
            continue
        indent = 0
        if raw.startswith('    ') or raw.startswith('\t'):
            indent = 2
        elif raw.startswith(' ') or raw.startswith('\u3000'):
            indent = 1
        if DOTLEADER_RE.search(s):
            s = re.sub(r'\s*(?:\.{3,}|…|·{3,})\s*\d{1,4}\s*$', '', s)
        s = re.sub(r'\s+\d+\s*$', '', s)    # trailing page number
        s = s.strip()
        if not s or len(re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]', '', s)) < 2:
            continue                          # rule-out: a single stray glyph (e.g. "2")
        if re.match(r'^第\s*(?:\d+|[一二三四五六七八九十百]+)\s*章', s):
            # chapter titles use CJK numerals in the printed TOC (第一章 …) as well
            # as Arabic ones (第1章 …) — strip either, keep the rest as the title
            title = re.sub(r'^第\s*(?:\d+|[一二三四五六七八九十百]+)\s*章\s*', '', s)
            TOC_LEVELS[norm_title(title)] = 1
            TOC_TITLES.append((title, norm_title(title)))
            lvl = 1
        elif re.match(r'^§\s*\d+', s):
            # "§N …" sections (books that number sections with §) — real sections
            TOC_LEVELS[norm_title(s)] = 2
            TOC_TITLES.append((s, norm_title(s)))
            lvl = 2
        elif re.match(r'^习\s*题', s):
            # per-section exercise pages — the main homework target, keep them
            # section-level so the TOC can navigate straight to them
            TOC_LEVELS[norm_title(s)] = 2
            TOC_TITLES.append((s, norm_title(s)))
            lvl = 2
        elif re.match(r'^\d+\.\d+\.\d+', s):
            TOC_LEVELS[norm_title(s)] = 3
            TOC_TITLES.append((s, norm_title(s)))
            lvl = 3
        elif re.match(r'^\d+\.\d+', s):
            TOC_LEVELS[norm_title(s)] = 2
            TOC_TITLES.append((s, norm_title(s)))
            lvl = 2
        else:
            TOC_LEVELS.setdefault(norm_title(s), 0)
            TOC_TITLES.append((s, norm_title(s)))
            lvl = 0
        TOC_ENTRIES.append({"title": s, "norm": norm_title(s), "level": lvl,
                            "indent": indent, "cn": cn_num(s)})


# ------------------------------------------------------------ heading repair
# OCR mislabels structure around section headings in two recurring ways:
#  * a theorem / definition / example paragraph gets a spurious "### N.M …" prefix
#    (its theorem number looks like a section number) — demote it back to body so
#    it stops polluting the TOC (e.g. "### 10.21 定义 考虑检验", "### 21.4 定理 …").
#  * a real section title is left un-marked — either as a plain "N.1 引言" line, or
#    rendered as a spaced-out display formula — so it never reaches the TOC; promote it.
THM_KW = r'(?:定理|定义|引理|推论|命题|性质|例|证明|假设|算法|公理|结论|定律|注记|注解)'
SECTION_THM_RE = re.compile(r'^\d+\.\d+\s*' + THM_KW)
INTRO_SEC_RE = re.compile(r'^\d+(?:\.\d+)?\s*引\s*言\s*$')
HEADING_FORMULA_BAN = re.compile(
    r'[=<>+]|\\frac|\\sum|\\int|\\prod|\\lim|\\sqrt|\\le|\\ge|\\neq|\\pm|\\cdot|\\times|\\sim')


def formula_is_heading(content):
    """True if a display formula is really a spaced-out section title, e.g.
    '10.3\\quad\\chi^{2}\\quad 分 \\quad 布' (a section, not an equation)."""
    if not re.match(r'^\s*\d+\.\d+\s*\\quad', content):
        return False
    if HEADING_FORMULA_BAN.search(content):
        return False
    return bool(re.search(r'[一-鿿]', content)) and len(content) < 60


def formula_heading_text(content):
    parts = re.split(r'\\quad', content)
    return (parts[0].strip() + ' ' + ''.join(p.strip() for p in parts[1:])).strip()


# ---------------------------------------------------------------- § heading level
# 教材实测（高数第五版 上/下册）：`###` -> \section* (进 book.toc)，`####` -> \subsection* (不进)。
# 证据：下册成品 book.toc 有 `\contentsline {section}{\S {} 1 常微分方程的概念}{284}`，
# 而 md 里那一行是 `### § 1 常微分方程的概念`。所以 § 标题必须是 h3 —— 写成 h4 会
# **静默不进目录**。OCR 把 § 写成 ###/##### 混用，这里把 § 行统一归一到 h3。
SEC_MARK_RE = re.compile(r'^(#{1,6})\s*(§\s*\S.*)$')
HAN_NUM = '一二三四五六七八九十'


def norm_section_headings(md):
    """§-numbered headings -> `###` (h3 = \\section*, listed in the TOC).
    Returns (md, [(lineno, old_level, text)])."""
    changed, out = [], []
    for i, line in enumerate(md.split('\n'), 1):
        m = SEC_MARK_RE.match(line.strip())
        if m and m.group(1) != '###':
            changed.append([i, len(m.group(1)), m.group(2)])
            out.append('### ' + m.group(2))
        else:
            out.append(line)
    return '\n'.join(out), changed


# （一）（二）… 中文序号标题（§ 之下的一级小节）
CJK_NUM_RE = re.compile(r'^[（(]([' + HAN_NUM + r']{1,3})[）)]\s*(\S.*)$')


def cn_num(s):
    """（一）（十二）… -> int, else None"""
    m = re.match(r'^[（(]([' + HAN_NUM + r']{1,3})[）)]', (s or '').strip())
    if not m:
        return None
    v, nums = 0, {c: k + 1 for k, c in enumerate(HAN_NUM)}
    for c in m.group(1):
        if c == '十':
            v = (v or 1) * 10
        else:
            v += nums[c]
    return v


def expected_cn_level(ocr_level, toc_listed):
    # 改进 C — input level for a （一）（二）… heading, from evidence only:
    #   * the printed TOC does NOT list it -> it sits directly below its § (or, when no §
    #     precedes it, directly below the chapter): \subsection* = 4;
    #   * the printed TOC DOES list it (the TOC normally stops at §, so a （一） entry means
    #     the book is one level deeper): \subsubsection* = 5.
    # An OCR level of 4/5 is kept as-is, so a sequence the OCR already levelled consistently
    # is never re-levelled on a guess. Returns (level, how): how = 'keep' | 'to4' | 'to5'.
    if ocr_level in (4, 5):
        return ocr_level, 'keep'
    return (5, 'to5') if toc_listed else (4, 'to4')


# -------------------------------------------------- inline-math pre-pass (改进 C: heading loss)
# A single stray `$` (e.g. a math span that lost its closing '$' and continues into the next
# line) makes `\$([^$]+?)\$` swallow whole lines — INCLUDING heading lines. Those headings
# then never reach HEADINGS_SEEN and every one of them shows up as a false
# missing_headings (上册: one such span swallowed ~200 lines and every heading in them).
# This pre-pass applies the inline-math tokenization with a line-local regex, so such a
# span simply does not match: the TOC/missing-heading evidence stays complete. The main
# pass below is unchanged (same output, same output-count semantics).
def pretoken_inline(md):
    """Replace `$...$` spans with \x00I<n>\x00 tokens like the main pass, but a span may
    never cross a newline. Returns (md, n_replaced)."""
    out, n = [], 0

    def rep(m):
        nonlocal n
        n += 1
        return '\x00I%d\x00' % (n - 1)
    for line in md.split('\n'):
        out.append(re.sub(r'\$([^$\n]+?)\$', rep, line))
    return '\n'.join(out), n


# ------------------------------------------ printed-TOC style lines that never became headings
# The 上册 printed TOC is still marked `## 目录`, so drop_toc removes it — but the OCR also
# put TOC-STYLE lines in the BODY (short, dot leaders, page number: "§5 L'Hospital 法则
# ..... 97"), or lists a title that never got a '#' mark. Both look exactly like TOC rows.
# Reported, never touched (--drop-block / --corrections is the human decision).
TOCISH_HEADING_LEN = 40


def heading_style_lines(md):
    """[(lineno, text, is_heading)] for lines that look like a printed-TOC row (dot leaders
    + page number) and that are short enough to be a heading title."""
    out = []
    for i, l in enumerate(md.split('\n'), 1):
        s = l.strip()
        if not DOTLEADER_RE.search(s) or not re.search(r'\s*\d{1,4}\s*$', s):
            continue
        title = re.sub(r'\s*(?:\.{3,}|…|·{3,})\s*\d{1,4}\s*$', '', s).strip()
        if 1 < len(re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]', '', title)) <= TOCISH_HEADING_LEN:
            out.append((i, title, s.startswith('#')))
    return out


# ------------------------------------------------------------ untitled printed TOC (改进 B)
# PROBLEM: a printed TOC whose `目录` line was never recognised as a heading silently bypasses
# drop_toc, leaking hundreds of dot-leader lines into the body (下册 md has NO `## 目录` line).
# POLICY (hard requirement, learned the hard way): NEVER auto-drop. Only REPORT candidate
# blocks + line numbers; deletion happens solely via an explicit `--drop-block L1-L2` after a
# human/AI has looked at them. (A past auto-fix nearly deleted 397 lines of real body text.)
DOTLEADER_RE = re.compile(r'\.{3,}|…|·{3,}')


def _toc_line(txt):
    return bool(DOTLEADER_RE.search(txt)) and bool(re.search(r'\s*\d{1,4}\s*$', txt))


def _toc_row_len(txt):
    title = re.sub(r'\s*(?:\.{3,}|…|·{3,})\s*\d{1,4}\s*$', '', txt)
    return len(re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]', '', title))


def find_toc_candidates(md, min_rows=8, min_span=12):
    """Detect an *untitled* printed TOC: at least min_rows dot-leader rows (each ending in a
    page number) spread over >= min_span lines. A lone dot-leader line in the body (running
    head) is not a TOC. REPORT-ONLY: returns [{start,end,lines,dot_lines,after_heading}];
    nothing is ever deleted automatically."""
    lines = md.split('\n')
    rows = [k + 1 for k, ln in enumerate(lines) if _toc_line(ln.strip())]
    if not rows:
        return []
    groups, cur = [], [rows[0]]
    for r in rows[1:]:
        if r - cur[-1] <= 4:               # tolerate wrapped continuation rows
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)
    result = []
    for grp in groups:
        a = grp[0]
        b = grp[-1]
        if len(grp) < min_rows or b - a < min_span:
            continue
        # narrow the proposed range to TOC-looking rows only: blank / TOC / heading rows are
        # fine; a real body line (long title) stops it. Over-wide ranges are what once nearly
        # deleted 397 lines of body text — stay conservative.
        candidates = list(range(a, b + 1))
        candidates.reverse()
        keep = a
        stopped = False
        for probe in candidates:
            if stopped:
                break
            txt = lines[probe - 1].strip()
            if not txt or txt.startswith('#'):
                keep = probe
            elif _toc_line(txt):
                keep = probe
            elif _toc_row_len(txt) > TOCISH_HEADING_LEN:
                stopped = True
            else:
                keep = probe
        n_dot = 0
        for q in range(a, keep + 1):
            if _toc_line(lines[q - 1].strip()):
                n_dot += 1
        result.append({"start": a, "end": keep, "lines": keep - a + 1, "dot_lines": n_dot,
                       "after_heading": (lines[a - 2].strip() if a >= 2 else "")[:60]})
    return result


def parse_drop_blocks(spec):
    """--drop-block 'L1-L2[,L3-L4...]' -> [(L1, L2), ...] (1-based, inclusive)."""
    out = []
    for part in re.split(r'[,;\s]+', spec or ''):
        if not part:
            continue
        m = re.match(r'^(\d+)\s*[-~:]\s*(\d+)$', part)
        if not m:
            raise SystemExit("--drop-block: bad range %r (expected L1-L2)" % part)
        a, b = int(m.group(1)), int(m.group(2))
        if a < 1 or b < a:
            raise SystemExit("--drop-block: bad range %r" % part)
        out.append((a, b))
    return out


# ---------------------------------------------------------------- main pipeline
def convert(md, imgdir, fontset, title, author, drop_toc, drop_blocks=()):
    md = re.sub(r'\\n(?![A-Za-z])', ' ', md)
    md = re.sub(r'\\textcircled\{\s*(\d+)\s*\}',
                lambda m: CIRCLED[int(m.group(1)) - 1] if 1 <= int(m.group(1)) <= 10 else m.group(0), md)
    # 改进 B: detect an UNTITLED printed TOC and only REPORT it. Evidence for `missing_
    # heading_candidates` as well: dot-leader lines are exactly the TOC-style lines that
    # never became a heading.
    toc_cands = find_toc_candidates(md) if drop_toc else []
    REPORT["toc_candidates"] = toc_cands
    toc_line_set = set()
    for blk in toc_cands:
        toc_line_set.update(range(blk["start"], blk["end"] + 1))
    parse_toc(md)
    if drop_toc:
        # stop at the next heading of ANY level — a chapter title marked ### right
        # after the printed TOC must survive (e.g. unnumbered chapter titles).
        md = re.sub(r'\n#{2,6}\s*目\s*录[^\n]*\n.*?(?=\n#{1,6}\s)', '\n', md, flags=re.S)
    # 改进 B: printed-TOC style rows that the OCR left in the BODY (after the real TOC was
    # dropped). Evidence only — the conversion below never deletes them.
    REPORT["untitled_toc"] = [{"line": n, "text": t}
                              for (n, t, is_h) in heading_style_lines(md) if not is_h]
    md, swallowed = norm_section_headings(md)
    # 改进 C: headings that a stray '$' would swallow into an inline span are recovered for
    # the evidence layer only (the rendered .tex is untouched — that stray '$' is still
    # listed in broken_inline_spans and must be fixed via --corrections).
    REPORT["heading_level_fixes"] = [
        {"line": n, "was": "#" * old, "now": "####", "text": t} for n, old, t in swallowed]
    md_pretok, pretok_n = pretoken_inline(md)
    # headings a stray '$' span swallows: present in md_pretok but absent from HEADINGS_SEEN
    pre_headings = [mm.group(1) for mm in (re.match(r'^#{1,6}\s+(.*\S)\s*$', l)
                                          for l in md_pretok.split('\n')) if mm]
    # 改进 B: explicit, human-confirmed deletions only (--drop-block L1-L2); line numbers
    # refer to the md file on disk, which the normalizer above never changes.
    dropped_lines = set()
    for a, b in drop_blocks:
        dropped_lines.update(range(a, b + 1))

    def orig_line(ln):
        """current 1-based index -> line number in the md file on disk (for the report)."""
        return ln + sum(1 for i in dropped_lines if i < ln)
    REPORT["dropped_blocks"] = []
    for a, b in drop_blocks:
        seg = [l for i, l in enumerate(md.split('\n'), 1) if a <= i <= b][:200]
        REPORT["dropped_blocks"].append({"start": a, "end": b, "lines": b - a + 1,
                                         "preview": [x[:60] for x in seg[:3]]})
    # a section title OCR'd as a spaced-out display formula -> a real #### heading
    md = re.sub(r'\$\$(.+?)\$\$',
                lambda m: ('\n#### ' + formula_heading_text(m.group(1)) + '\n')
                          if formula_is_heading(m.group(1)) else m.group(0),
                md, flags=re.S)
    # A stray unpaired '$' (odd count) silently shifts EVERY inline pairing after it —
    # catch it loudly instead of shipping corrupted math. Likewise, an inline span that
    # crosses lines or swallows markup (## headings, <div>...) is by definition broken.
    # Line numbers are computed on a copy that keeps the original line structure
    # (display formulas emptied but their newlines preserved), so they point at the
    # md file the user has on disk.
    md_check = re.sub(r'\$\$.+?\$\$',
                      lambda m: '\n' * m.group(0).count('\n'), md, flags=re.S)
    nd = md_check.count('$')
    REPORT["inline_dollar_count"] = nd
    if nd % 2:
        print("WARNING: odd number of inline '$' (%d) — a stray '$' shifts EVERY "
              "inline pairing after it. Fix the source (e.g. via --corrections) "
              "before trusting the output." % nd)
    for m in re.finditer(r'\$([^$]+?)\$', md_check):
        c = m.group(1)
        if '\n' in c or '<div' in c or c.lstrip().startswith('#'):
            if len(REPORT["broken_inline_spans"]) < 20:
                REPORT["broken_inline_spans"].append({
                    "line": md_check.count('\n', 0, m.start()) + 1,
                    "preview": c[:60].replace('\n', '⏎')})
    if REPORT["broken_inline_spans"]:
        print("WARNING: %d inline math span(s) cross lines or contain markup — almost "
              "certainly a stray '$' earlier in the document. First broken span starts "
              "near md line %d. See review.json: broken_inline_spans."
              % (len(REPORT["broken_inline_spans"]), REPORT["broken_inline_spans"][0]["line"]))
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
    # strip only the tags PaddleOCR actually emits. A generic <[a-zA-Z]...> sweep
    # eats body text: a bare math inequality like "(a<b)" looks like a tag opening
    # and deletes everything up to the next ">" in the document (silent content loss).
    md = re.sub(r'</?(?:div|table|thead|tbody|tr|td|th|img|br|center|span)\b[^<>]*>', '', md)

    HEAD = re.compile(r'^(#{1,6})\s+(.*\S)\s*$')
    BLOCK_TOK = re.compile(r'^\x00(D|T|G)(\d+)\x00$')
    # chapters AND parts (第X篇), CJK or Arabic numerals — Chinese textbooks use
    # 第一章/第一篇, and the OCR heading level of these lines is unreliable anyway
    CHAPTER_PAT = re.compile(r'第\s*(?:\d+|[一二三四五六七八九十百]+)\s*[章篇]|chapter\s+\d+', re.I)
    FRONT_BACK = {'原书序', '原书前言', '目录', '参考文献', '符号列表', '名词索引', '译者前言',
                  '内容简介', 'preface', 'references', 'bibliography', 'index'}
    out, buf = [], []

    def flush():
        if buf:
            text = ''.join(buf).strip()
            if text:
                out.append(render_inline(text)); out.append('')
            buf.clear()

    def heading(level, t, source_heading=None):
        flush()
        # store the heading with inline-math tokens resolved back to their formula
        # content, so the missing-heading check can compare against TOC entries
        # whose math is still written out
        HEADINGS_SEEN.add(re.sub('\x00I(\\d+)\x00',
                                 lambda m: INLINE[int(m.group(1))], t).strip())
        section = SECTION_PREV[0] or ''
        ts = t.strip()
        # The book's own printed TOC is ground truth: when this heading's title appears
        # in the TOC with a numbered level, trust that level over OCR's (PaddleOCR
        # frequently mis-levels headings, e.g. sections at ## and chapters at ###).
        # Map TOC level -> input level: the converter renders level 1 as \chapter*,
        # 3 as \section*, 4 as \subsection*; levels <=2 that are not 第N章 get DEMOTED
        # to body, so a TOC section must land on 3, not 2.
        toc_promoted = False
        tl = TOC_LEVELS.get(norm_title(t))
        if tl is None:
            # body chapter headings carry the 第N章/篇 prefix that the printed TOC
            # entry had stripped (TOC keys are prefix-free) — retry without it
            m2 = re.match(r'^第\s*(?:\d+|[一二三四五六七八九十百]+)\s*[章篇]\s*(.+)$', ts)
            if m2:
                tl = TOC_LEVELS.get(norm_title(m2.group(1)))
        if tl == 1:
            level, toc_promoted = 1, True
        elif tl == 2:
            level, toc_promoted = 3, True
        elif tl == 3:
            level, toc_promoted = 4, True
        # 改进 C — （一）（二）中文序号标题. They carry no number of their own, so the level
        # comes from expected_cn_level(): the printed TOC (listed = deeper level) and the
        # OCR's own mark (4/5 kept). Without a match the enclosing § gives the level.
        if cn_num(ts) is not None and not toc_promoted:
            toc_listed = norm_title(ts) in TOC_LEVELS
            level, how = expected_cn_level(level, toc_listed)
            CN_NUM_HEADINGS.append([level, ts, section, how])
            level = CN_NUM_LEVEL = level
        if level == 1 and not toc_promoted and re.match(r'^\d+[\.\s、]', ts):
            # OCR promoted an exercise/item number ("11. 证明定理…") to a top-level
            # heading — a chapter never looks like that; render as body text.
            REPORT["demoted_headings"].append(ts)
            out.append(render_inline(t)); out.append('')
            return
        if level == 3 and not toc_promoted and SECTION_THM_RE.match(ts):
            # a theorem/example paragraph mis-prefixed with ### — render as body text
            # (footnote markers kept inline), never as a TOC section.
            REPORT["demoted_headings"].append(ts)
            out.append(render_inline(t)); out.append('')
            return
        if tl is not None:
            # (level, canon) of a heading that DID match the TOC — used to tell a
            # genuinely lost heading from an un-marked one (改进 C: report-only)
            HEAD_ORD_MATCH.append((level, canon_title(t)))
        if source_heading is not None and SEC_MARK_RE.match(source_heading.strip()):
            # a (possibly OCR-original) § heading; new § = new section context
            SECTION_PREV[0] = ts
        t = re.sub('\x00F(\\d+)\x00',
                   lambda m: FOOTNOTE_LEFTOVERS.append(FOOTNOTES[int(m.group(1))]) or '', t)
        printed = render_inline(t)
        plain = plain_text(t) or 'sec'
        if level <= 2:
            is_chapter = (level == 1 or bool(CHAPTER_PAT.search(ts))
                          or ts in FRONT_BACK or '沃塞曼' in ts)
            if is_chapter:
                out.append(r'\chapter*{%s}' % printed)
                out.append(r'\markboth{%s}{%s}' % (plain, plain))
                out.append(r'\phantomsection\addcontentsline{toc}{chapter}{\texorpdfstring{%s}{%s}}'
                           % (printed, plain))
            else:
                REPORT["demoted_headings"].append(ts)
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

    lineno = 0
    for line in md.split('\n'):
        lineno += 1
        if orig_line(lineno) in dropped_lines:
            # 改进 B: only explicit --drop-block ranges get removed (never automatic)
            continue
        st = line.strip()
        if st == '':
            flush(); continue
        if st == '\x00CB\x00':
            flush(); out.append(r'\begin{center}'); continue
        if st == '\x00CE\x00':
            flush(); out.append(r'\end{center}'); continue
        # <!--bookpage:N--> marks a source-PDF page boundary (emitted by the
        # md reconstruction for split scans). 1:1 page correspondence lets the
        # reader's book pagination (homework is assigned by book page!) be
        # resolved directly: the footer shows P<N> and \label{bookpage:N}
        # feeds the book-page -> PDF-page map (page_map.json).
        if st.startswith('<!--bookpage:') and st.endswith('-->'):
            flush()
            n = st[len('<!--bookpage:'):-len('-->')]
            if out:
                out.append(r'\newpage')
            out.append(r'\setcounter{bookpage}{%s}\label{bookpage:%s}' % (n, n))
            out.append('')
            continue
        mb = BLOCK_TOK.match(st)
        if mb:
            flush()
            kind, idx = mb.group(1), int(mb.group(2))
            if kind == 'D':
                norm = render_math(DISPLAY[idx], 'display')
                if not norm:
                    out.append('')
                elif re.match(r'\\begin\{(align|gather|equation|multline|eqnarray|flalign)\*?\}', norm):
                    out.append(norm)
                elif norm.lstrip().startswith('['):
                    # amsmath warns "Bracket group [x] at formula start" — \relax silences it
                    out.append(r'\[\relax ' + norm + r'\]')
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
            heading(len(mh.group(1)), mh.group(2), mh.group(2)); continue
        if INTRO_SEC_RE.match(st):       # "N.1 引言" left un-marked by OCR -> section
            heading(3, st); continue
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

    scan_unmapped(body)
    REPORT["footnotes_unplaced"] = FOOTNOTE_LEFTOVERS
    # printed-TOC entries that never appear as a body heading — OCR lost the heading;
    # restore them via --corrections (the TOC is the evidence for the exact title).
    # Compared canonically (math split differently in TOC vs body) and with a suffix
    # allowance (a body chapter heading may carry the 第N章 prefix the TOC line had).
    seen_canon = {canon_title(nb) for nb in HEADINGS_SEEN}
    # headings that WOULD have been seen if no stray '$' span swallowed them (see the
    # pretoken_inline pre-pass): their TOC entries are NOT reported as missing, the cause
    # (one broken inline span) is reported there instead.
    swallowed_canon = {canon_title(h) for h in pre_headings}
    swallowed_canon -= seen_canon
    toc_norms = {e["norm"] for e in TOC_ENTRIES}

    def toc_seen(title):
        c = canon_title(title)
        return bool(c) and (c in seen_canon or any(c2.endswith(c) for c2 in seen_canon)
                            or any(c2.endswith(c) for c2 in swallowed_canon))

    # 改进 B — missing_headings is SPLIT, not just filtered:
    #  * toc_only_entries: the title IS a body heading, just one level DEEPER than the
    #    printed entry (e.g. 几个例子 / 无穷小量的运算, listed under §2 but written in the body
    #    without a heading mark). Not a loss — the content is there. Reported separately so
    #    it stops drowning the real gaps (上册: most of the old 142 were exactly this).
    #  * missing_headings: everything else = a heading the OCR really lost.
    for ent in TOC_ENTRIES:
        orig = ent["title"]
        if toc_seen(orig):
            continue
        c = canon_title(orig)
        deeper = {lvl for lvl, cc in HEAD_ORD_MATCH if cc.endswith(c)}
        if deeper and any(lv > ent["level"] for lv in deeper):
            REPORT["toc_only_entries"].append(orig)
        else:
            REPORT["missing_headings"].append(orig)
    # 改进 C — TOC entries that never became a heading at all but DO exist in the body as a
    # plain line (the OCR lost only the '#' marks). Report-only: never auto-promote.
    at_body_line = set()
    for raw in md.split('\n'):
        s2 = raw.strip()
        if s2 and not s2.startswith('#') and not s2.startswith('\x00'):
            at_body_line.add(canon_title(s2))
    for ent in TOC_ENTRIES:
        orig = ent["title"]
        if toc_seen(orig):
            continue
        c = canon_title(orig)
        if c and any(p.endswith(c) for p in at_body_line):
            REPORT["missing_heading_candidates"].append(
                {"title": orig, "toc_level": ent["level"],
                 "hint": "目录有此条、正文只有无标记的普通行 —— 用 --corrections 补 '#### ' 前缀，或核对"
                         "是否 OCR 丢了标题"})
    # 改进 C — （一）（二）headings: report the evidence-based decision (level + how) taken by
    # expected_cn_level(); a sequence the OCR already levelled consistently shows 'keep'.
    REPORT["cn_num_headings"] = [
        {"title": t, "level": lv, "how": how, "section": sec}
        for (lv, t, sec, how) in CN_NUM_HEADINGS]
    REPORT["normalize_report"] = dict(sorted(NORM_COUNTS.items()))

    preamble = PREAMBLE_TMPL % {
        'fontset': fontset, 'imgdir': imgdir,
        'title': r'\bfseries ' + title, 'author': author,
        'maketitle': r'\maketitle' if title else '',
    }
    return preamble + body + "\n\\end{document}\n"


def main():
    ap = argparse.ArgumentParser(description="PaddleOCR markdown -> XeLaTeX book")
    ap.add_argument("input")
    ap.add_argument("--json", help="sibling *.json (default: input with .md->.json)")
    ap.add_argument("--imgmap", help="image url->file map json (from download_images.py)")
    ap.add_argument("--imgdir", default="imgs", help="image folder for \\graphicspath (default: imgs)")
    ap.add_argument("--out", help="output .tex path (default: book.tex next to input)")
    ap.add_argument("--fontset", default="mac", help="ctex fontset: mac | windows | ubuntu | fandol")
    ap.add_argument("--title")
    ap.add_argument("--author", default="")
    ap.add_argument("--keep-ocr-toc", action="store_true")
    ap.add_argument("--no-footnotes", action="store_true")
    ap.add_argument("--report", help="write a review.json of high-risk spots for AI/human review")
    ap.add_argument("--overrides", help="{math_id: corrected_latex_body} json applied to math")
    ap.add_argument("--symbols", help="{char: latex} json merged into the symbol map")
    ap.add_argument("--corrections", help="{wrong_text: right_text} literal fixes applied to "
                    "the raw md (for semantic OCR errors the core can't detect, e.g. a "
                    "misread character in a heading)")
    ap.add_argument("--drop-block", metavar="L1-L2", default="",
                    help="delete md lines L1..L2 (1-based, inclusive; several ranges comma-"
                         "separated). ONLY for blocks you have confirmed first — review.json "
                         "toc_candidates lists untitled printed-TOC blocks that would otherwise "
                         "leak into the body. Nothing is ever dropped automatically.")
    a = ap.parse_args()

    md_text = open(a.input, encoding="utf-8").read()
    if a.corrections and os.path.exists(a.corrections):
        for wrong, right in json.load(open(a.corrections, encoding="utf-8")).items():
            md_text = md_text.replace(wrong, right)
    json_path = a.json or (os.path.splitext(a.input)[0] + ".json")
    out_path = a.out or os.path.join(os.path.dirname(os.path.abspath(a.input)), "book.tex")
    try:
        J = json.load(open(json_path, encoding="utf-8"))
    except Exception:
        J = []
    if a.imgmap and os.path.exists(a.imgmap):
        IMGMAP.update(json.load(open(a.imgmap, encoding="utf-8")))
    if a.overrides and os.path.exists(a.overrides):
        OVERRIDES.update(json.load(open(a.overrides, encoding="utf-8")))
    if a.symbols and os.path.exists(a.symbols):
        SYM.update(json.load(open(a.symbols, encoding="utf-8")))
        rebuild_sym_re()

    build_formula_index(J)
    title = a.title or detect_title(md_text, J)
    if title == "Document":
        # last resort: the OCR markdown is named "<book>.pdf_by_PaddleOCR-VL-<ver>.md"
        base = os.path.basename(a.input)
        base = re.split(r'\.pdf_by_', base)[0] or base
        base = re.sub(r'\s*\(z-library.*$', '', base).strip()
        if base:
            title = base
    if not a.no_footnotes and J:
        md_text = recover_footnotes(md_text, J)

    drop_blocks = parse_drop_blocks(a.drop_block)
    tex = convert(md_text, a.imgdir, a.fontset, title, a.author,
                  drop_toc=not a.keep_ocr_toc, drop_blocks=drop_blocks)
    open(out_path, "w", encoding="utf-8").write(tex)

    print("wrote %s  (display=%d inline=%d table=%d img=%d footnotes=%d, +%d addendum; "
          "overrides applied=%d)"
          % (out_path, len(DISPLAY), len(INLINE), len(TABLE), len(IMG),
             len(FOOTNOTES), len(FOOTNOTE_LEFTOVERS), len(OVERRIDES)))
    for blk in REPORT["toc_candidates"]:
        print("WARNING: possible UNTITLED printed TOC at md lines %d-%d (%d lines, %d with dot "
              "leaders, preceded by %r) — NOT dropped. Confirm, then re-run with "
              "--drop-block %d-%d"
              % (blk["start"], blk["end"], blk["lines"], blk["dot_lines"],
                 blk["after_heading"], blk["start"], blk["end"]))
    if a.report:
        json.dump(REPORT, open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("review report -> %s  (math_flagged=%d, unmapped_chars=%d, demoted_headings=%d, "
              "broken_inline_spans=%d, missing_headings=%d, footnotes_unplaced=%d, "
              "missing_images=%d | toc_candidates=%d, toc_only_entries=%d, "
              "heading_level_fixes=%d, cn_num_headings=%d, missing_heading_candidates=%d, "
              "normalize_rewrites=%d)"
              % (a.report, len(REPORT["math"]), len(REPORT["unmapped_chars"]),
                 len(REPORT["demoted_headings"]), len(REPORT["broken_inline_spans"]),
                 len(REPORT["missing_headings"]), len(REPORT["footnotes_unplaced"]),
                 len(REPORT["missing_images"]), len(REPORT["toc_candidates"]),
                 len(REPORT["toc_only_entries"]), len(REPORT["heading_level_fixes"]),
                 len(REPORT["cn_num_headings"]), len(REPORT["missing_heading_candidates"]),
                 sum(REPORT["normalize_report"].values())))


if __name__ == "__main__":
    main()
