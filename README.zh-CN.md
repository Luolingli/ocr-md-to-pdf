[English](README.md) | **简体中文**

# ocr-md-to-pdf

把 **PaddleOCR-VL** 导出的 markdown 编译成一本**全矢量、文字可选中**的 PDF 书（用 XeLaTeX）。

对扫描版中文 / 数学教材做 OCR 后，会得到一个 `*.pdf_by_PaddleOCR-VL-*.md`（公式是
`$ ... $` / `$$ ... $$`，表格是裸 `<table>`，插图是远程 `<img>`），外加一个同名 `*.json`。
本工具把它变成排版整洁的 PDF——每个字都是真正的矢量文字，可搜索、可复制，而不是图片。

它打包成了一个 [Claude Code](https://claude.com/claude-code) **skill**，但脚本是纯 Python 3
（只用标准库），单独运行也完全没问题。

## 它处理了什么

- 带多余空格的行内 / 展示公式，以及**公式里夹的中文**（每段中文都被 `\text{}` 包起来，
  数学字体不会再出现缺字方块）。
- HTML `<table>` → 带框线的 `longtable`（支持 colspan）；`<img>` → 居中的
  `\includegraphics`；居中 `<div>` → `center` 环境。
- 标题 → `\chapter*` / `\section*` / …，带 PDF 书签和每章页眉；自动生成一份可点击的目录
  （OCR 自带那份杂乱的"目录"默认删掉）。
- **脚注恢复**：`.md` 导出时会丢掉脚注——本工具从 `.json` 里把它们捞回来，按
  `①②③` 标记放回正文页脚（定位不到的收进书末附录）。
- 对常见 OCR 乱码做稳健修复，保证总能编译通过：HTML 实体、字面 `\n`、
  `\textcircled{n}`、正文里裸写的 `<`/`>`、双上下标、列数不足的 `array`、
  缺定界符的 `\left`/`\right`、不配平的花括号等等。

## 依赖

- **XeLaTeX**（TeX Live / MacTeX），需 **ctex** 宏包和中文字体。
  默认 `--fontset mac` 用 macOS 系统字体；Linux/Windows 上用
  `--fontset fandol`（`tlmgr install fandol`，自带字体）或 `ubuntu` / `windows`。
- **Python 3**（仅标准库）。
- 可选：Ghostscript（`gs`）或 `pdftoppm`，用于把页面渲染成图片预览。

## 快速开始

```bash
bash scripts/build.sh "/path/to/书名.pdf_by_PaddleOCR-VL-1.6.md"
# → 在 md 同目录生成 book.pdf（用 BUILD=/tmp/out 可换构建目录）
# 多余参数会透传给转换器，例如：
bash scripts/build.sh foo.md --fontset fandol --title "书名" --author "作者"
```

`build.sh` 跑完三步，并汇报页数和残留的 LaTeX 错误数。

## 分步运行（便于调试 / 定制）

```bash
# 1) 下载远程插图（签名 URL 会过期，尽早下）
python3 scripts/download_images.py "INPUT.md" --out-dir imgs --map imgmap.json
# 2) markdown -> LaTeX
python3 scripts/md_to_latex.py "INPUT.md" --imgmap imgmap.json --imgdir imgs --out book.tex
# 3) 编译两遍（第二遍补全目录和页眉）
xelatex -interaction=nonstopmode book.tex
xelatex -interaction=nonstopmode book.tex
```

### 转换器参数

| 参数 | 含义 |
|---|---|
| `--json PATH` | 同名 JSON（默认把输入的 `.md` 换成 `.json`）；用于恢复脚注 |
| `--fontset NAME` | `mac`（默认）· `windows` · `ubuntu` · `fandol` |
| `--title` / `--author` | 标题页文字（书名默认从 JSON 的 `doc_title` 自动识别） |
| `--imgmap` / `--imgdir` | 图片 url→文件 映射，以及 `\graphicspath` 用的文件夹名 |
| `--keep-ocr-toc` | 保留书自带那份杂乱目录（默认删除） |
| `--no-footnotes` | 跳过从 JSON 恢复脚注 |

## 为什么不直接 `pandoc input.md`？

markdown 当然能直接编译成 PDF，但 **PaddleOCR-VL 的输出直接喂给 pandoc 基本编译不出能看的结果**。
本工具的价值就在于把下面这些 OCR 专有的坑都填了——这些是通用 md→PDF 工具不会替你做的：

- **行内公式 `$ x $` 带空格**：pandoc 要求 `$` 紧贴非空格字符，否则整段行内公式不识别。
- **公式里有中文**：XeLaTeX 数学模式下不套 `\text{}` 就是缺字方块或报错。
- **表格是 HTML**：pandoc 的 markdown→LaTeX 会直接丢掉裸 `<table>`。
- **插图是带签名的远程 URL**：会过期，需要先抓到本地并建映射。
- **脚注根本不在 md 里**：被 OCR 导出阶段丢了，只能从 JSON 配对捞回——任何通用工具都做不到。
- **大量 OCR 乱码**：不配平的花括号、`\left0.8`、双下标、`align*` 嵌套、宏包冲突等，
  会让编译直接中断。

所以：**输入是干净 markdown，就直接用 pandoc，本工具是多余的**；
**输入是 PaddleOCR-VL 的中文 / 数学教材 OCR，本工具把"能编译但全是坑"变成"开箱即用"**。
详见 [`SKILL.md`](SKILL.md) 里的完整流程与排错说明。

## 作为 Claude Code skill 使用

把本仓库克隆（或软链）到 skills 目录，目录名即 skill 名：

```bash
git clone https://github.com/Luolingli/ocr-md-to-pdf ~/.claude/skills/ocr-md-to-pdf
```

然后在 Claude Code 里：`/ocr-md-to-pdf`，或直接说"把这个 OCR markdown 编译成 PDF 书"。

## 说明与局限

- 若签名图片 URL 过期（HTTP 403），转换前重新跑一遍 `download_images.py`；
  缺图会渲染成一个小小的 `[图]` 占位符。
- PDF 只反映 **OCR 实际识别到的内容**——真正没被识别出来的页面或字符无法凭空补出。
- 针对 PaddleOCR-VL 的输出（HTML 表格/图片、`$...$` 公式）调校；别的 OCR markdown
  风格可能需要小改 `scripts/md_to_latex.py` 里的正则。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
