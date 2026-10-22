#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Extract the source-book-page -> PDF-page mapping from a XeLaTeX aux file.

md_to_latex.py turns <!--bookpage:N--> marker lines into
\setcounter{bookpage}{N}\label{bookpage:N}; this reads the resolved labels out
of the aux file (2nd xelatex pass) and writes {"<bookpage>": <pdfpage>} JSON
to stdout. Used by build.sh to produce page_map.json, so printed-book page
references (homework is assigned that way) can be resolved to PDF pages.
"""
import json
import re
import sys


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: page_map.py book.aux  (writes JSON to stdout)")
    aux = open(sys.argv[1], encoding="utf-8").read()
    m = {b: p for b, p in re.findall(
        r'\\newlabel\{bookpage:(\d+)\}\{\{[^{}]*\}\{(\d+)\}', aux)}
    m = {int(b): int(p) for b, p in m.items()}
    json.dump({str(b): p for b, p in sorted(m.items())},
              sys.stdout, ensure_ascii=False)
    sys.stderr.write("page_map: %d book pages mapped\n" % len(m))


if __name__ == "__main__":
    main()
