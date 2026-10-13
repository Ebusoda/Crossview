"""Draft and polish Word documents (`orch doc`), standard library only.

    orch doc draft "写一份国庆值班安排的通知，10 月 1 日到 7 日，每天两人" --style gongwen
    orch doc draft "根据这些材料写一份月度工作总结" --from 记录.docx --from 数据.txt
    orch doc polish 总结.docx "压缩到 800 字，语气正式一些"
    orch doc show 总结.docx

The model writes plain text with a few marks (# title, ## heading, - list, | table |, **bold**); this
module turns that into a real .docx. `--style gongwen` lays the page out like a Chinese official document
(GB/T 9704: A4, 仿宋 三号 body, 小标宋 title, 黑体 / 楷体 headings, 28 pt lines, two-character indents).

What is sent: for draft, your request and the material files you name; for polish, the whole document.
Nothing else. Check your workplace's rules before sending work documents to an outside model, and never
use this for classified material.
"""
from __future__ import annotations

import json
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .i18n import tr
from .office import Endpoint, OfficeError, Result, complete

MAX_CHARS = 60_000
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = "{%s}" % W

# kinds: title, h1, h2, h3, p, bullet, number, table
@dataclass
class Block:
    kind: str
    text: str = ""
    rows: list[list[str]] | None = None


# -- counting -------------------------------------------------------------------

_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_WORD = re.compile(r"[A-Za-z0-9]+(?:[.'’-][A-Za-z0-9]+)*")


def count(text: str) -> int:
    """Length the way people count it: each Chinese / Japanese character is one, each Latin word is one."""
    text = re.sub(r"[#*|>`-]+", " ", text)
    return len(_CJK.findall(text)) + len(_WORD.findall(_CJK.sub(" ", text)))


# -- reading .docx ---------------------------------------------------------------

def _style_names(z: zipfile.ZipFile) -> dict[str, str]:
    if "word/styles.xml" not in z.namelist():
        return {}
    out = {}
    for st in ET.fromstring(z.read("word/styles.xml")).iter(_W + "style"):
        name = st.find(_W + "name")
        out[st.get(_W + "styleId", "")] = (name.get(_W + "val", "") if name is not None else "").lower()
    return out


def _para_text(p: ET.Element) -> str:
    parts = []
    for el in p.iter():
        if el.tag == _W + "t":
            parts.append(el.text or "")
        elif el.tag == _W + "tab":
            parts.append("\t")
        elif el.tag in (_W + "br", _W + "cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _kind(p: ET.Element, names: dict[str, str]) -> str:
    ppr = p.find(_W + "pPr")
    if ppr is None:
        return "p"
    sid = ppr.find(_W + "pStyle")
    name = names.get(sid.get(_W + "val", ""), sid.get(_W + "val", "")).lower() if sid is not None else ""
    if name in ("title", "标题") or name.startswith("title"):
        return "title"
    m = re.search(r"(?:heading|标题)\s*(\d)", name)
    lvl = ppr.find(_W + "outlineLvl")
    level = int(m.group(1)) if m else (int(lvl.get(_W + "val", "9")) + 1 if lvl is not None else 0)
    if 1 <= level <= 9:
        return f"h{min(level, 3)}"
    if ppr.find(_W + "numPr") is not None or "list" in name:
        return "bullet"
    return "p"


def read_docx(path: Path) -> list[Block]:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise OfficeError(tr("{0} 不是 .docx 文件（老的 .doc 请先在 Word 里另存为 .docx）", path.name)) from None
    with z:
        if "word/document.xml" not in z.namelist():
            raise OfficeError(tr("{0} 不是 .docx 文件（老的 .doc 请先在 Word 里另存为 .docx）", path.name))
        names = _style_names(z)
        body = ET.fromstring(z.read("word/document.xml")).find(_W + "body")
    blocks: list[Block] = []
    for el in list(body) if body is not None else []:
        if el.tag == _W + "p":
            text = _para_text(el)
            if text:
                kind = _kind(el, names)
                if kind == "p" and text.startswith("• "):          # our own bullets (no numbering part)
                    kind, text = "bullet", text[2:]
                elif kind == "p" and re.match(r"\d+[.、)]", text):
                    kind = "number"
                blocks.append(Block(kind, text))
        elif el.tag == _W + "tbl":
            rows = [[" ".join(_para_text(p) for p in tc.iter(_W + "p")).strip() for tc in tr_.findall(_W + "tc")]
                    for tr_ in el.findall(_W + "tr")]
            if rows:
                blocks.append(Block("table", rows=rows))
    return blocks


def read_any(path: Path) -> list[Block]:
    """A .docx, or a text / Markdown file."""
    path = Path(path)
    if not path.is_file():
        raise OfficeError(tr("找不到文件：{0}", path))
    from .convert import ready

    path = ready(path)                          # .doc / .wps / .rtf: converted to .docx first
    if path.suffix.lower() in (".docx", ".docm"):
        return read_docx(path)
    if path.suffix.lower() in (".txt", ".md", ".markdown", ".text"):
        data = path.read_bytes()
        for enc in ("utf-8-sig", "gb18030"):
            try:
                return from_markdown(data.decode(enc))
            except UnicodeDecodeError:
                continue
        return from_markdown(data.decode("latin-1"))
    raise OfficeError(tr("只支持 Word（.docx、.doc）、WPS 文字、.txt 和 .md 文件（{0}）", path.name))


# -- the text format the model reads and writes ---------------------------------------

_MARK = {"title": "# ", "h1": "## ", "h2": "### ", "h3": "#### ", "bullet": "- ", "p": "", "number": ""}


def to_markdown(blocks: list[Block]) -> str:
    out = []
    for b in blocks:
        if b.kind == "table":
            rows = b.rows or []
            out.append("\n".join("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in r) + " |"
                                 for r in rows[:1] + [["---"] * len(rows[0])] + rows[1:] if rows))
        else:
            out.append(_MARK.get(b.kind, "") + b.text)
    return "\n\n".join(out)


def from_markdown(text: str) -> list[Block]:
    text = text.strip()
    m = re.fullmatch(r"```[a-zA-Z]*\n(.*)\n```", text, re.S)       # the whole answer in a code block
    if m:
        text = m.group(1)
    blocks: list[Block] = []
    table: list[list[str]] = []
    for raw in text.splitlines() + [""]:
        line = raw.strip()
        if line.startswith("|") and line.endswith("|") and len(line) > 1:
            cells = [c.strip() for c in line[1:-1].split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                table.append(cells)
            continue
        if table:
            width = max(len(r) for r in table)
            blocks.append(Block("table", rows=[r + [""] * (width - len(r)) for r in table]))
            table = []
        if not line or re.fullmatch(r"[-*_]{3,}", line):
            continue
        h = re.match(r"(#{1,6})\s+(.*)", line)
        if h:
            level = len(h.group(1))
            kind = "title" if level == 1 and not any(b.kind == "title" for b in blocks) else f"h{min(max(level - 1, 1), 3)}"
            blocks.append(Block(kind, h.group(2).strip()))
        elif re.match(r"[-*•]\s+", line):
            blocks.append(Block("bullet", re.sub(r"^[-*•]\s+", "", line)))
        elif re.match(r"\d+[.、)]\s*", line):
            blocks.append(Block("number", line))
        else:
            blocks.append(Block("p", line))
    return blocks


# -- writing .docx -----------------------------------------------------------------

STYLES = {
    # name: page margins in mm (top, bottom, left, right), and per block kind:
    # (east-asian font, latin font, size in half-points, bold, centered, first-line indent in chars)
    "plain": {
        "margins": (25.4, 25.4, 31.8, 31.8), "line": ("auto", 300), "after": 120,
        "title": ("黑体", "Calibri", 36, True, True, 0), "h1": ("黑体", "Calibri", 32, True, False, 0),
        "h2": ("黑体", "Calibri", 28, True, False, 0), "h3": ("宋体", "Calibri", 24, True, False, 0),
        "p": ("宋体", "Calibri", 24, False, False, 2), "bullet": ("宋体", "Calibri", 24, False, False, 0),
        "number": ("宋体", "Calibri", 24, False, False, 0), "table": ("宋体", "Calibri", 21, False, False, 0),
    },
    "gongwen": {    # GB/T 9704-2012, simplified
        "margins": (37, 35, 28, 26), "line": ("exact", 560), "after": 0,
        "title": ("方正小标宋简体", "Times New Roman", 44, False, True, 0),
        "h1": ("黑体", "Times New Roman", 32, False, False, 2), "h2": ("楷体_GB2312", "Times New Roman", 32, False, False, 2),
        "h3": ("仿宋_GB2312", "Times New Roman", 32, True, False, 2), "p": ("仿宋_GB2312", "Times New Roman", 32, False, False, 2),
        "bullet": ("仿宋_GB2312", "Times New Roman", 32, False, False, 2),
        "number": ("仿宋_GB2312", "Times New Roman", 32, False, False, 2),
        "table": ("仿宋_GB2312", "Times New Roman", 24, False, False, 0),
    },
}


def _runs(text: str, font: tuple, bold_all: bool) -> str:
    east, latin, size, *_ = font
    out = []
    for n, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if not part:
            continue
        bold = bold_all or n % 2 == 1
        rpr = (f'<w:rPr><w:rFonts w:ascii="{latin}" w:hAnsi="{latin}" w:eastAsia="{east}"/>'
               f'{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>')
        for k, line in enumerate(part.split("\n")):
            if k:
                out.append(f"<w:r>{rpr}<w:br/></w:r>")
            out.append(f'<w:r>{rpr}<w:t xml:space="preserve">{escape(line)}</w:t></w:r>')
    return "".join(out)


def _para(text: str, kind: str, st: dict, in_table: bool = False) -> str:
    font = st[kind]
    _, _, size, bold, center, indent = font
    rule, line = st["line"]
    ppr = [f'<w:spacing w:before="0" w:after="{0 if in_table else st["after"]}" w:line="{line}" w:lineRule="{rule}"/>']
    if kind == "title":
        ppr.insert(0, '<w:pStyle w:val="Title"/>')
    elif kind in ("h1", "h2", "h3"):
        ppr.insert(0, f'<w:pStyle w:val="Heading{kind[1]}"/>')
    if center:
        ppr.append('<w:jc w:val="center"/>')
    if kind == "bullet":
        ppr.append(f'<w:ind w:left="{size * 20}" w:hanging="{size * 10}"/>')
        text = "• " + text
    elif indent:
        ppr.append(f'<w:ind w:firstLineChars="{indent * 100}" w:firstLine="{indent * size * 10}"/>')
    return f'<w:p><w:pPr>{"".join(ppr)}</w:pPr>{_runs(text, font, bold)}</w:p>'


def _table(rows: list[list[str]], st: dict) -> str:
    cols = max(len(r) for r in rows)
    width = 9000 // cols
    border = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
                     for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>{border}</w:tblBorders></w:tblPr>'
           f'<w:tblGrid>{"".join(f"<w:gridCol w:w={chr(34)}{width}{chr(34)}/>" for _ in range(cols))}</w:tblGrid>']
    for i, r in enumerate(rows):
        cells = "".join(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/></w:tcPr>'
                        f'{_para(f"**{c}**" if i == 0 and c else c, "table", st, True)}</w:tc>'
                        for c in r + [""] * (cols - len(r)))
        out.append(f"<w:tr>{cells}</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def write_docx(blocks: list[Block], path: Path, style: str = "plain") -> Path:
    st = STYLES.get(style)
    if st is None:
        raise OfficeError(tr("不认识的版式 {0}；可以用：{1}", style, ", ".join(STYLES)))
    body = []
    for b in blocks:
        if b.kind == "table" and b.rows:
            body.append(_table(b.rows, st))
            body.append(_para("", "p", st))           # Word needs a paragraph after a table
        elif b.kind != "table":
            body.append(_para(b.text, b.kind if b.kind in st else "p", st))
    t, bo, le, ri = (round(mm * 56.7) for mm in st["margins"])
    sect = (f'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="{t}" w:right="{ri}" w:bottom="{bo}" '
            f'w:left="{le}" w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>')
    east, latin, size, *_ = st["p"]
    heads = "".join(
        f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
        f'<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/>{f"<w:outlineLvl w:val={chr(34)}{lvl}{chr(34)}/>" if lvl >= 0 else ""}'
        f'</w:pPr></w:style>'
        for sid, name, lvl in (("Title", "Title", -1), ("Heading1", "heading 1", 0), ("Heading2", "heading 2", 1),
                               ("Heading3", "heading 3", 2)))
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>',
        "word/_rels/document.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>',
        "word/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr>'
        f'<w:rFonts w:ascii="{latin}" w:hAnsi="{latin}" w:eastAsia="{east}" w:cs="{latin}"/>'
        f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault>'
        '</w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
        f'{heads}</w:styles>',
        "word/document.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{"".join(body)}{sect}</w:body></w:document>',
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


# -- asking the model ------------------------------------------------------------------

FORMAT = """Write the document as plain text with these marks only:
# Title (once, at the top)
## Heading, ### Sub-heading, #### Third level
- list item
| table | cells |   (first row = header)
**bold** inside a line
One paragraph per line. No other Markdown, no code blocks, no remarks before or after the document."""

GONGWEN = """Follow the conventions of a Chinese official document (公文): a formal, concise tone; headings numbered
一、 then （一） then 1. ; no exclamation marks, no emoji; put the issuing body and date at the end when known."""

SYSTEM = ("You write and edit office documents for the user. Answer in the language of the user's request "
          "unless it asks for another. Never invent facts, names, numbers or dates the user did not give: "
          "write a placeholder such as [日期] instead.")


def _system(masker) -> str:
    from .redact import INSTRUCTION

    return SYSTEM + (" " + INSTRUCTION if masker is not None and masker.enabled else "")


def draft_messages(request: str, materials: list[tuple[str, str]], style: str, masker=None) -> list[dict[str, str]]:
    m = masker.mask if masker is not None else (lambda x: x)
    parts = [f"Request: {m(request.strip())}"]
    for name, text in materials:
        parts.append(f"\n### Material: {m(name)}\n{m(text)}")
    parts += ["", FORMAT] + ([GONGWEN] if style == "gongwen" else [])
    return [{"role": "system", "content": _system(masker)}, {"role": "user", "content": "\n".join(parts)}]


def polish_messages(text: str, request: str, style: str, masker=None) -> list[dict[str, str]]:
    m = masker.mask if masker is not None else (lambda x: x)
    req = m(request.strip()) or ("润色：语句通顺、用词准确、去掉重复，不改原意，不增删事实")
    parts = [f"Edit the document below. What to do: {req}",
             "Keep every fact, number, name and date unless the request says otherwise.",
             "", "### Document", m(text), "", FORMAT] + ([GONGWEN] if style == "gongwen" else [])
    return [{"role": "system", "content": _system(masker)}, {"role": "user", "content": "\n".join(parts)}]


def _check_size(text: str) -> None:
    if len(text) > MAX_CHARS:
        raise OfficeError(tr("内容太长（{0} 字符，上限 {1}）：请分几段处理", len(text), MAX_CHARS))


def draft(request: str, eps: list[Endpoint], materials: list[Path] = (), style: str = "plain",
          timeout: float = 300, masker=None) -> tuple[list[Block], Result]:
    mats = [(Path(p).name, to_markdown(read_any(Path(p)))) for p in materials]
    msgs = draft_messages(request, mats, style, masker)
    _check_size(msgs[1]["content"])
    res = complete(eps, msgs, timeout, tool="doc draft")
    text = masker.unmask(res.text) if masker is not None else res.text
    return from_markdown(text), res


def polish(blocks: list[Block], request: str, eps: list[Endpoint], style: str = "plain",
           timeout: float = 300, masker=None) -> tuple[list[Block], Result]:
    text = to_markdown(blocks)
    if not text.strip():
        raise OfficeError(tr("这个文档没有文字"))
    _check_size(text)
    res = complete(eps, polish_messages(text, request, style, masker), timeout, tool="doc polish")
    return from_markdown(masker.unmask(res.text) if masker is not None else res.text), res


# -- templates: {{field}} placeholders in your own .docx -----------------------------------

_FIELD = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_PARA = re.compile(r"<w:p(?:\s[^>]*?)?(?<!/)>.*?</w:p>", re.S)
_TEXT = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.S)
_PARTS = re.compile(r"word/(document|header\d*|footer\d*)\.xml")


def _xml_unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _para_plain(p: str) -> str:
    return "".join(_xml_unescape(m.group(2)) for m in _TEXT.finditer(p))


def template_fields(path: Path) -> list[str]:
    """The {{field}} names in a .docx template (body, headers, footers), in order, without repeats."""
    path = Path(path)
    if not path.is_file():
        raise OfficeError(tr("找不到文件：{0}", path))
    from .convert import ready

    path = ready(path)
    try:
        with zipfile.ZipFile(path) as z:
            xml = [z.read(n).decode("utf-8") for n in z.namelist() if _PARTS.fullmatch(n)]
    except zipfile.BadZipFile:
        raise OfficeError(tr("{0} 不是 .docx 文件（老的 .doc 请先在 Word 里另存为 .docx）", path.name)) from None
    out: list[str] = []
    for part in xml:
        for p in _PARA.findall(part):
            for name in _FIELD.findall(_para_plain(p)):
                if name not in out:
                    out.append(name)
    return out


def _fill_para(p: str, values: dict[str, str]) -> str:
    texts = list(_TEXT.finditer(p))
    plain = "".join(_xml_unescape(m.group(2)) for m in texts)
    if "{{" not in plain or not _FIELD.search(plain):
        return p
    new = _FIELD.sub(lambda m: values.get(m.group(1).strip(), m.group(0)), plain)
    if new == plain:
        return p
    # Word splits text into runs at random places, so the paragraph's text goes into its first run
    # (keeping that run's font) and the other runs are emptied
    first = escape(new).replace("\n", '</w:t><w:br/><w:t xml:space="preserve">')
    out, last = [], 0
    for i, m in enumerate(texts):
        out.append(p[last:m.start()])
        open_tag = m.group(1) if "xml:space" in m.group(1) else m.group(1)[:-1] + ' xml:space="preserve">'
        out.append(open_tag + (first if i == 0 else "") + m.group(3))
        last = m.end()
    out.append(p[last:])
    return "".join(out)


def fill_docx(template: Path, values: dict[str, str], dest: Path) -> list[str]:
    """Copy the template with each {{field}} replaced; returns the fields left empty."""
    from .convert import ready

    template = ready(Path(template))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(template) as src, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            data = src.read(item.filename)
            if _PARTS.fullmatch(item.filename):
                xml = data.decode("utf-8")
                data = _PARA.sub(lambda m: _fill_para(m.group(0), values), xml).encode("utf-8")
            out.writestr(item, data)
    return [f for f in template_fields(template) if not str(values.get(f, "")).strip()]


def fill_messages(fields: list[str], request: str, materials: list[tuple[str, str]], masker=None) -> list[dict[str, str]]:
    m = masker.mask if masker is not None else (lambda x: x)
    parts = [f"Fill in a document template. Its fields: {json.dumps(fields, ensure_ascii=False)}",
             f"What the user wants: {m(request.strip())}"]
    for name, text in materials:
        parts.append(f"\n### Material: {m(name)}\n{m(text)}")
    parts += ["", "Answer with ONE JSON object mapping every field name to its text, and nothing else. "
              "Use \\n for a line break inside a value. Use \"\" for a field the request and materials do not "
              "give: never invent names, numbers or dates."]
    return [{"role": "system", "content": _system(masker)}, {"role": "user", "content": "\n".join(parts)}]


def fill(template: Path, request: str, eps: list[Endpoint], materials: list[Path] = (), timeout: float = 300,
         masker=None) -> tuple[dict[str, str], Result]:
    fields = template_fields(template)
    if not fields:
        raise OfficeError(tr("模板里没有 {{字段}}：在 Word 里把要填的地方写成 {{标题}}、{{日期}} 这样"))
    mats = [(Path(p).name, to_markdown(read_any(Path(p)))) for p in materials]
    msgs = fill_messages(fields, request, mats, masker)
    _check_size(msgs[1]["content"])
    res = complete(eps, msgs, timeout, temperature=0, tool="doc fill")
    text = res.text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    text = fence.group(1) if fence else text
    a, b = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[a:b + 1]) if a >= 0 and b > a else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise OfficeError(tr("模型没有按要求给出各字段的内容"))
    values = {str(k).strip(): ("" if v is None else str(v)) for k, v in data.items()}
    if masker is not None:
        values = {k: masker.unmask(v) for k, v in values.items()}
    return {f: values.get(f, "") for f in fields}, res


def out_path(blocks: list[Block], folder: Path, stem: str | None = None) -> Path:
    """<folder>/<stem or the title>.docx, with the time added when that file exists already."""
    if stem is None:
        title = next((b.text for b in blocks if b.kind in ("title", "h1")), "") or tr("文档")
        stem = re.sub(r'[\\/:*?"<>|\s]+', " ", title).strip()[:40] or "doc"
    p = Path(folder) / f"{stem}.docx"
    if p.exists():
        p = p.with_name(f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.docx")
    return p
