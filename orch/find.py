"""Find a piece of information in a pile of files (`orch find`): searched on this computer, nothing is sent anywhere.

    orch find 资料/ 张三
    orch find 资料/ 张三 6222021234567890123 --all       # files that contain every word
    orch find 资料/ --type 身份证 --type 银行卡            # every ID / card number in the files

Reads Excel / WPS / CSV (every sheet, every cell), Word / WPS (paragraphs and tables), text files, and PDF when
pypdf or pdftotext is installed. Old .doc / .xls / .wps are converted first, as for orch sheet / orch doc.
A number is found even when the file writes it with spaces or dashes (6222 0212 3456 ...).
The hits go to the screen and to an Excel file: file, where in it, what was found, and the text around it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .i18n import tr
from .office import OfficeError
from .sheet import Table, read_csv, read_xlsx, show, write_book

SHEET_EXTS = (".xlsx", ".xlsm", ".xls", ".et", ".csv", ".tsv")
WORD_EXTS = (".docx", ".docm", ".doc", ".wps", ".rtf")
TEXT_EXTS = (".txt", ".md", ".log", ".json", ".xml", ".html", ".htm", ".ini", ".cfg", ".yaml", ".yml", ".eml")
PDF_EXTS = (".pdf",)
MAX_BYTES = 100 * 1024 * 1024
CONTEXT = 40
MAX_PRINT = 50


class FindError(OfficeError):
    pass


def _types() -> dict[str, re.Pattern]:
    from .redact import _CARD, _EMAIL, _ID, _PHONE

    return {"身份证": _ID, "手机": _PHONE, "银行卡": _CARD, "邮箱": _EMAIL}


TYPE_ALIASES = {"身份证": "身份证", "id": "身份证", "手机": "手机", "手机号": "手机", "phone": "手机",
                "银行卡": "银行卡", "卡号": "银行卡", "card": "银行卡", "邮箱": "邮箱", "email": "邮箱"}


@dataclass
class Needle:
    label: str
    pattern: re.Pattern


def needles(words: Iterable[str], types: Iterable[str] = ()) -> list[Needle]:
    out: list[Needle] = []
    for w in words:
        w = str(w).strip()
        if not w:
            continue
        digits = re.sub(r"[\s\-]", "", w)
        if digits.isdigit() and len(digits) >= 6:
            # numbers: also when written with spaces or dashes, but not inside a longer number
            body = r"[\s\-]?".join(re.escape(c) for c in digits)
            out.append(Needle(w, re.compile(rf"(?<!\d){body}(?!\d)")))
        else:
            out.append(Needle(w, re.compile(re.escape(w), re.I)))
    table = _types()
    for t in types:
        key = TYPE_ALIASES.get(str(t).strip().lower()) or TYPE_ALIASES.get(str(t).strip())
        if key is None:
            raise FindError(tr("不认识的类型「{0}」；可以用：身份证、手机、银行卡、邮箱", t))
        out.append(Needle(tr(key), table[key]))
    if not out:
        raise FindError(tr("要找什么？例如：orch find 文件夹 张三，或 --type 身份证"))
    return out


# -- reading files as (where, text) pieces ----------------------------------------------------

def _plain(v: Any) -> str:
    """A cell as written: long numbers (accounts, dates like 20260302) without thousands separators."""
    if isinstance(v, bool):
        return show(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return show(v)


def _sheet_pieces(path: Path) -> Iterator[tuple[str, str]]:
    from .convert import ready
    from .sheet import _ref

    src = ready(path)
    if src.suffix.lower() in (".csv", ".tsv"):
        parts = [("", read_csv(src))]
    else:
        first, name, names = read_xlsx(src)
        parts = [(names[0] if names else name, first)] + [(n, read_xlsx(src, n)[0]) for n in names[1:]]
    for name, rows in parts:
        for r, row in enumerate(rows, start=1):
            line = " | ".join(_plain(v) for v in row if v is not None and str(v).strip())
            for c, v in enumerate(row):
                if v is None:
                    continue
                text = _plain(v)
                where = (f"{name}!" if name and len(parts) > 1 else "") + _ref(c, r)
                yield where, text + "\x00" + line


def _word_pieces(path: Path) -> Iterator[tuple[str, str]]:
    from .doc import read_any

    n_par = n_tab = 0
    for b in read_any(path):
        if b.kind == "table":
            n_tab += 1
            for r, row in enumerate(b.rows, start=1):
                yield tr("表格 {0} 第 {1} 行", n_tab, r), " | ".join(row)
        else:
            n_par += 1
            yield tr("第 {0} 段", n_par), b.text


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _text_pieces(path: Path) -> Iterator[tuple[str, str]]:
    for n, line in enumerate(_decode(path.read_bytes()).splitlines(), start=1):
        if line.strip():
            yield tr("第 {0} 行", n), line


def pdf_reader() -> str:
    """'pypdf', 'pdftotext' or '' (PDFs are then skipped and listed)."""
    try:
        import pypdf  # noqa: F401
        return "pypdf"
    except ImportError:
        pass
    return "pdftotext" if shutil.which("pdftotext") else ""


def _pdf_pages(path: Path) -> list[str]:
    how = pdf_reader()
    if how == "pypdf":
        import pypdf

        return [(p.extract_text() or "") for p in pypdf.PdfReader(str(path)).pages]
    if how == "pdftotext":
        p = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=120)
        if p.returncode != 0:
            raise FindError(tr("读不了这个 PDF"))
        return p.stdout.decode("utf-8", errors="replace").split("\f")
    raise FindError(tr("读 PDF 要先装 pypdf（pip install pypdf）"))


def _pdf_pieces(path: Path) -> Iterator[tuple[str, str]]:
    pages = _pdf_pages(path)
    if not any(p.strip() for p in pages):
        raise FindError(tr("PDF 里没有文字（可能是扫描件，要先做文字识别）"))
    for n, page in enumerate(pages, start=1):
        for m, line in enumerate(page.splitlines(), start=1):
            if line.strip():
                yield tr("第 {0} 页", n), line


def pieces(path: Path) -> Iterator[tuple[str, str]]:
    ext = path.suffix.lower()
    if ext in SHEET_EXTS:
        return _sheet_pieces(path)
    if ext in WORD_EXTS:
        return _word_pieces(path)
    if ext in TEXT_EXTS:
        return _text_pieces(path)
    if ext in PDF_EXTS:
        return _pdf_pieces(path)
    raise FindError("unsupported")


def readable(path: Path) -> bool:
    return path.suffix.lower() in SHEET_EXTS + WORD_EXTS + TEXT_EXTS + PDF_EXTS


# -- searching ------------------------------------------------------------------------------

@dataclass
class Hit:
    file: Path
    where: str
    found: str
    text: str


@dataclass
class Result:
    hits: list[Hit] = field(default_factory=list)
    searched: int = 0
    skipped: list[tuple[Path, str]] = field(default_factory=list)   # files that could not be read, and why
    other: int = 0                                                   # files of kinds orch does not read
    files_with_hits: int = 0


def _snippet(text: str, start: int, end: int, width: int = CONTEXT) -> str:
    a, b = max(0, start - width), min(len(text), end + width)
    s = text[a:b].replace("\n", " ").replace("\r", " ")
    return ("…" if a else "") + s.strip() + ("…" if b < len(text) else "")


def files_in(paths: Iterable[Path]) -> tuple[list[Path], int]:
    from .trace import is_own_output

    files: list[Path] = []
    other = 0
    for p in paths:
        p = Path(p)
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file() or any(part.startswith(".") for part in f.relative_to(p).parts) \
                        or f.name.startswith("~$") or is_own_output(f):
                    continue
                if readable(f):
                    files.append(f)
                else:
                    other += 1
        else:
            raise FindError(tr("找不到文件：{0}", p))
    return files, other


def search(paths: Iterable[Path], ns: list[Needle], all_words: bool = False) -> Result:
    files, other = files_in(paths)
    res = Result(other=other)
    for f in files:
        try:
            if f.stat().st_size > MAX_BYTES:
                res.skipped.append((f, tr("文件太大（超过 100 MB）")))
                continue
            hits: list[Hit] = []
            seen: set[str] = set()
            for where, text in pieces(f):
                cell, _, line = text.partition("\x00")       # a sheet cell, and its whole row for context
                for n in ns:
                    for m in n.pattern.finditer(cell):
                        ctx = line or cell
                        mm = n.pattern.search(ctx) if line else m
                        snip = _snippet(ctx, mm.start(), mm.end()) if mm else _snippet(cell, m.start(), m.end())
                        hits.append(Hit(f, where, m.group(0) if n.label in _types_labels() else n.label, snip))
                        seen.add(n.label)
                        if line:
                            break                            # one hit per cell is enough
        except (OfficeError, OSError, subprocess.SubprocessError, ValueError, KeyError) as e:
            msg = str(e) if str(e) != "unsupported" else tr("不支持这种文件")
            res.skipped.append((f, msg))
            continue
        except Exception as e:  # a damaged file must not stop the whole search
            res.skipped.append((f, tr("打不开：{0}", type(e).__name__)))
            continue
        res.searched += 1
        if all_words and len(seen) < len(ns):
            continue
        if hits:
            res.files_with_hits += 1
            res.hits += hits
    return res


def _types_labels() -> set[str]:
    return {tr(k) for k in ("身份证", "手机", "银行卡", "邮箱")}


def table(res: Result, root: Path | None = None) -> Table:
    def rel(f: Path) -> str:
        try:
            return str(f.relative_to(root)) if root else f.name
        except ValueError:
            return f.name

    rows: list[list[Any]] = [[rel(h.file), h.where, h.found, h.text, str(h.file)] for h in res.hits]
    rows += [[rel(f), "", tr("（没读到）"), why, str(f)] for f, why in res.skipped]
    return Table([tr("文件"), tr("位置"), tr("找到的"), tr("内容"), tr("完整路径")], rows, tr("查找结果"))


def save(res: Result, out_dir: Path, root: Path | None = None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return write_book([table(res, root)], out_dir / f"{tr('查找结果')}_{stamp}.xlsx")


def summary_lines(res: Result, max_print: int = MAX_PRINT) -> list[str]:
    lines = [tr("查了 {0} 个文件，{1} 个里有，共 {2} 处", res.searched, res.files_with_hits, len(res.hits))]
    last = None
    for h in res.hits[:max_print]:
        if h.file != last:
            lines.append(f"\n{h.file}")
            last = h.file
        lines.append(f"  {h.where}  [{h.found}]  {h.text}")
    if len(res.hits) > max_print:
        lines.append(tr("……还有 {0} 处，全部在 Excel 文件里", len(res.hits) - max_print))
    if res.skipped:
        lines.append("")
        lines.append(tr("没读到的文件 {0} 个：", len(res.skipped)))
        lines += [f"  {f.name}: {why}" for f, why in res.skipped[:20]]
    if res.other:
        lines.append(tr("另有 {0} 个不是文档或表格的文件（图片、压缩包等），没查", res.other))
    return lines
