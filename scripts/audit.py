#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Content-fidelity audit for the md -> .tex conversion.

Every long CJK run present in the OCR markdown must also appear in the generated
.tex (whitespace-insensitively). Formula/table/image content is stripped from the
markdown first, so only real body text is checked. A non-zero result means the
converter dropped or mangled prose — investigate before shipping the PDF.

Usage:  audit.py INPUT.md book.tex
Exit status: 0 if nothing is missing, 1 otherwise (for scripting).
"""
import re
import sys


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: audit.py INPUT.md book.tex")
    md = open(sys.argv[1], encoding="utf-8").read()
    tex = re.sub(r'\s+', '', open(sys.argv[2], encoding="utf-8").read())
    md = re.sub(r'<table.*?</table>', ' ', md, flags=re.S)
    md = re.sub(r'\$\$.+?\$\$', ' ', md, flags=re.S)
    md = re.sub(r'\$[^$]+?\$', ' ', md)
    # same tag whitelist as the converter: a generic <[^>]+> sweep eats body text
    # (a bare math inequality like "(a<b)" looks like a tag and deletes up to the
    # next ">"), which would MASK converter losses on the audit side as well
    md = re.sub(r'</?(?:div|table|thead|tbody|tr|td|th|img|br|center|span)\b[^<>]*>', ' ', md)
    # the converter intentionally drops the book's printed TOC — exclude it, else
    # TOC entries (e.g. "分块矩阵及运算") are always reported as missing
    md = re.sub(r'\n##\s*目录\s*\n.*?(?=\n#{1,6}\s)', ' ', md, flags=re.S)
    miss = [r for r in set(re.findall(r'[一-鿿]{6,}', md)) if r not in tex]
    print("CJK runs missing from tex: %d" % len(miss))
    for x in miss[:10]:
        print("  ", x)
    sys.exit(1 if miss else 0)


if __name__ == "__main__":
    main()
