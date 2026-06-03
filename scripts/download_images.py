#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Download every image referenced by a PaddleOCR markdown export to a local folder
and write a {url: local_filename} map that md_to_latex.py consumes.

PaddleOCR embeds images as <img src="https://.../xxx.jpg?authorization=..."> with
short-lived signed URLs, so run this promptly. Local/relative srcs are mapped as-is.

Usage:  python3 download_images.py INPUT.md [--out-dir DIR] [--map MAP.json]
Pure standard library.
"""
import re, os, json, argparse, urllib.request, concurrent.futures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="the *.md produced by PaddleOCR-VL")
    ap.add_argument("--out-dir", help="folder to save images (default: <md dir>/imgs)")
    ap.add_argument("--map", help="output url->file json (default: <md dir>/imgmap.json)")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    base = os.path.dirname(os.path.abspath(a.input))
    out_dir = a.out_dir or os.path.join(base, "imgs")
    map_path = a.map or os.path.join(base, "imgmap.json")
    os.makedirs(out_dir, exist_ok=True)

    md = open(a.input, encoding="utf-8").read()
    srcs = re.findall(r'<img[^>]*?src="([^"]+)"', md)
    srcs += re.findall(r'!\[[^\]]*\]\(([^)]+)\)', md)        # markdown image syntax too
    seen, uniq = set(), []
    for u in srcs:
        if u not in seen:
            seen.add(u); uniq.append(u)
    print("unique image references:", len(uniq))

    mapping = {}

    def fetch(i_u):
        i, u = i_u
        if not u.lower().startswith(("http://", "https://")):
            return (u, u, None)                              # local path: map as-is
        name = "img%03d.jpg" % i
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=30).read()
            if len(data) < 100:
                return (u, name, "too small (%d bytes)" % len(data))
            open(os.path.join(out_dir, name), "wb").write(data)
            return (u, name, None)
        except Exception as e:
            return (u, name, str(e))

    ok = fail = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for u, name, err in ex.map(fetch, list(enumerate(uniq))):
            if err:
                fail += 1; print("FAIL", name, err)
            else:
                ok += 1; mapping[u] = name
    json.dump(mapping, open(map_path, "w"), ensure_ascii=False)
    print("downloaded OK=%d FAIL=%d -> %s" % (ok, fail, map_path))


if __name__ == "__main__":
    main()
