"""Ask questions about a spreadsheet (`orch sheet`): Excel (.xlsx) or CSV, standard library only.

The numbers are computed here, on this computer, never by the model. The model sees only the column
names, their types and a few sample rows (none with --no-samples), and answers with a small JSON plan:
which rows to keep, how to group them, what to add up, how to sort, and which chart to draw. This module
checks the plan against the real columns and runs it over the whole table.

    orch sheet show sales.xlsx
    orch sheet ask sales.xlsx "每个部门这个季度花了多少？画个柱状图"
    orch sheet run sales.xlsx plan.json          # run a saved plan again, no model

Results go next to the file, in <name>_orch/: the result table as .xlsx, the chart as .svg and the plan
as .json. The model is the first one you have a key for (DeepSeek, Qwen, GPT, Claude, Gemini), or the
pools under `office = [...]` in the project's [routing]; when one fails the next one is tried.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from . import llm
from .i18n import tr
from .office import Endpoint, OfficeError, endpoints  # noqa: F401  (re-exported for callers)

SAMPLE_ROWS = 5
MAX_PRINT_ROWS = 30
MAX_CHART_ITEMS = 40
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
_M = "{%s}" % _NS["m"]


SheetError = OfficeError       # one error type for all office commands


@dataclass
class Table:
    headers: list[str]
    rows: list[list[Any]]
    name: str = ""
    sheets: list[str] = field(default_factory=list)


# -- reading ------------------------------------------------------------------

_NUM = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$|^[+-]?\.\d+$")
_DATE = re.compile(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")


def parse_text(s: str) -> Any:
    """A CSV cell: number, date or text (None when empty)."""
    s = s.strip()
    if not s:
        return None
    if _NUM.match(s):
        n = s.replace(",", "")
        return float(n) if "." in n else int(n)
    m = _DATE.match(s)
    if m:
        y, mo, d, h, mi, sec = m.groups()
        try:
            if h is None:
                return dt.date(int(y), int(mo), int(d))
            return dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec or 0))
        except ValueError:
            return s
    return s


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def read_csv(path: Path) -> list[list[Any]]:
    text = _decode(path.read_bytes())
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    if delim == "," and text.count("\t") > text.count(","):
        delim = "\t"
    return [[parse_text(c) for c in row] for row in csv.reader(text.splitlines(), delimiter=delim)]


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


_DATE_FMT_IDS = set(range(14, 23)) | {45, 46, 47}


def _is_date_format(code: str) -> bool:
    code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code).lower()
    return any(c in code for c in "ymd") or ("h" in code and "s" in code)


def _xlsx_names(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(sheet name, path inside the zip)] in workbook order."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target", "") for r in rels.findall("pr:Relationship", _NS)}
    out = []
    for s in wb.findall("m:sheets/m:sheet", _NS):
        t = target.get(s.get("{%s}id" % _NS["r"]), "")
        t = t.lstrip("/") if t.startswith("/") else "xl/" + t
        out.append((s.get("name", ""), t))
    return out


def read_xlsx(path: Path, sheet: str | None = None) -> tuple[list[list[Any]], str, list[str]]:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise SheetError(tr("{0} 不是 .xlsx 文件（老的 .xls 请先在 Excel 里另存为 .xlsx）", path.name)) from None
    with z:
        names = _xlsx_names(z)
        if not names:
            raise SheetError(tr("{0} 里没有工作表", path.name))
        if sheet is None:
            name, part = names[0]
        elif sheet.isdigit() and 1 <= int(sheet) <= len(names):
            name, part = names[int(sheet) - 1]
        else:
            match = [n for n in names if n[0] == sheet]
            if not match:
                raise SheetError(tr("没有叫「{0}」的工作表；有：{1}", sheet, ", ".join(n for n, _ in names)))
            name, part = match[0]
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(_M + "t")))
        date_styles: set[int] = set()
        date1904 = False
        if "xl/styles.xml" in z.namelist():
            st = ET.fromstring(z.read("xl/styles.xml"))
            custom = {int(f.get("numFmtId", "0")): f.get("formatCode", "")
                      for f in st.findall("m:numFmts/m:numFmt", _NS)}
            for i, xf in enumerate(st.findall("m:cellXfs/m:xf", _NS)):
                fid = int(xf.get("numFmtId", "0"))
                if fid in _DATE_FMT_IDS or (fid in custom and _is_date_format(custom[fid])):
                    date_styles.add(i)
        pr = ET.fromstring(z.read("xl/workbook.xml")).find("m:workbookPr", _NS)
        if pr is not None and pr.get("date1904") in ("1", "true"):
            date1904 = True
        epoch = dt.datetime(1904, 1, 1) if date1904 else dt.datetime(1899, 12, 30)

        rows: list[list[Any]] = []
        with z.open(part) as f:
            for _, el in ET.iterparse(f):
                if el.tag != _M + "row":
                    continue
                cells: dict[int, Any] = {}
                for i, c in enumerate(el.findall("m:c", _NS)):
                    ref = c.get("r")
                    col = _col_index(ref) if ref else i
                    t = c.get("t", "n")
                    v = c.find("m:v", _NS)
                    raw = v.text if v is not None else None
                    if t == "s" and raw is not None:
                        val: Any = parse_text(shared[int(raw)]) if shared[int(raw)].strip() else None
                        if isinstance(val, (int, float)):
                            val = shared[int(raw)].strip()   # text that looks like a number stays text
                    elif t == "inlineStr":
                        text = "".join(x.text or "" for x in c.iter(_M + "t")).strip()
                        val = parse_text(text) if text else None
                        if isinstance(val, (int, float)):
                            val = text
                    elif t in ("str", "e"):
                        val = raw
                    elif t == "b":
                        val = raw == "1"
                    elif raw is None:
                        val = None
                    else:
                        num = float(raw)
                        if int(c.get("s", "0")) in date_styles:
                            d = epoch + dt.timedelta(days=num)
                            val = d.date() if num == int(num) else d.replace(microsecond=0)
                        else:
                            val = int(num) if num.is_integer() and "." not in raw and "E" not in raw.upper() else num
                    cells[col] = val
                r = int(el.get("r", len(rows) + 1)) - 1
                while len(rows) < r:
                    rows.append([])
                width = max(cells) + 1 if cells else 0
                rows.append([cells.get(i) for i in range(width)])
                el.clear()
        return rows, name, [n for n, _ in names]


def load(path: Path, sheet: str | None = None, header: int | None = None) -> Table:
    """The table in a .xlsx/.csv/.tsv file. `header` = 1-based row with the column names (default: the
    first row that is not empty)."""
    path = Path(path)
    if not path.is_file():
        raise SheetError(tr("找不到文件：{0}", path))
    from .convert import ready

    path = ready(path)                          # .xls / .et: converted to .xlsx first
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        raw, name, sheets = read_xlsx(path, sheet)
        if sheet is None:                       # skip empty sheets in front (a new workbook's "Sheet1")
            for n in range(2, len(sheets) + 1):
                if any(v is not None for r in raw for v in r):
                    break
                raw, name, _ = read_xlsx(path, str(n))
    elif ext in (".csv", ".tsv", ".txt"):
        raw, name, sheets = read_csv(path), path.stem, []
    else:
        raise SheetError(tr("只支持 Excel（.xlsx、.xls）、WPS 表格和 .csv 文件（{0}）", path.name))
    if header is not None:
        start = header - 1
    else:
        start = next((i for i, r in enumerate(raw) if any(v is not None for v in r)), None)
        if start is None:
            raise SheetError(tr("这个表是空的"))
    if start >= len(raw):
        raise SheetError(tr("这个表是空的"))
    width = max((len(r) for r in raw[start:]), default=0)
    headers: list[str] = []
    for i in range(width):
        v = raw[start][i] if i < len(raw[start]) else None
        h = (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else str(v)).strip() if v is not None else ""
        h = h or f"col{i + 1}"
        base, n = h, 2
        while h in headers:
            h, n = f"{base}_{n}", n + 1
        headers.append(h)
    rows = [(r + [None] * width)[:width] for r in raw[start + 1:] if any(v is not None for v in r)]
    return Table(headers, rows, name, sheets)


# -- what the model sees -------------------------------------------------------

def kind(values: list[Any]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "empty"
    if all(isinstance(v, bool) for v in vals):
        return "bool"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "number"
    if all(isinstance(v, (dt.date, dt.datetime)) for v in vals):
        return "date"
    return "text"


def show(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1e15 else f"{v:g}"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    return str(v)


def profile(t: Table) -> list[dict[str, Any]]:
    cols = []
    for i, h in enumerate(t.headers):
        col = [r[i] for r in t.rows]
        k = kind(col)
        info: dict[str, Any] = {"name": h, "type": k, "filled": sum(v is not None for v in col)}
        vals = [v for v in col if v is not None]
        if k in ("number", "date") and vals:
            info["min"], info["max"] = show(min(vals)), show(max(vals))
        elif k == "text":
            info["distinct"] = len({str(v) for v in vals})
        cols.append(info)
    return cols


PLAN_HELP = """Answer with ONE JSON object and nothing else:
{
  "title": "short title of the result, in the user's language",
  "filter": [{"column": "...", "op": "= | != | > | >= | < | <= | contains | in | between", "value": ...}],
  "group_by": ["column", {"column": "a date column", "by": "year | quarter | month | week | day"}],
  "metrics": [{"column": "a column, or * to count rows", "agg": "sum | avg | count | count_distinct | min | max", "name": "result column name"}],
  "columns": ["columns to list when there are no metrics and no group_by"],
  "sort": [{"by": "a result column", "desc": true}],
  "limit": 10,
  "chart": {"type": "bar | line | pie", "x": "a result column", "y": "a result column"},
  "note": "one sentence for the user: what was computed, or what the table cannot answer"
}
Every key except "title" is optional. Use only the column names listed. Dates in values: "YYYY-MM-DD".
"in" takes a list, "between" takes [low, high]. Without metrics, group_by counts rows (result column "count").
Do not compute numbers yourself: the program runs the plan over the whole table. Add a chart only when it helps.
If the question cannot be answered from these columns, return just a title and a note that says why."""


def _private_column(t: Table, i: int, masker) -> bool:
    """A column whose range must not be shown when masking: personal by its header, or long numbers
    (phone or ID numbers typed as numbers)."""
    from .redact import COLUMN_KINDS

    if any(pat.search(t.headers[i]) for _, pat in COLUMN_KINDS):
        return True
    return any(isinstance(r[i], int) and not isinstance(r[i], bool) and abs(r[i]) >= 10**10 for r in t.rows)


def _sample(v: Any, header: str, masker) -> Any:
    if isinstance(v, bool) or v is None:
        return show(v)
    if isinstance(v, float):
        return v
    text = str(v) if isinstance(v, int) else show(v)
    if masker is not None and masker.enabled:
        masked = masker.mask_cell(text, header)
        if masked != text:
            return masked
    return v if isinstance(v, int) else text


def build_prompt(t: Table, question: str, samples: int = SAMPLE_ROWS, masker=None) -> str:
    """What the model sees. With a masker, personal details in the question and the samples become
    placeholders, and the value range of personal columns is left out."""
    on = masker is not None and masker.enabled
    if on:
        for i, h in enumerate(t.headers):
            masker.learn_column([str(r[i]) if isinstance(r[i], int) else show(r[i]) for r in t.rows], h)
    lines = [f"Table \"{t.name}\": {len(t.rows)} rows.", "Columns:"]
    for i, c in enumerate(profile(t)):
        extra = ""
        if "min" in c and not (on and _private_column(t, i, masker)):
            extra = f", {c['min']} .. {c['max']}"
        elif "distinct" in c:
            extra = f", {c['distinct']} distinct values"
        lines.append(f"- {c['name']} ({c['type']}{extra})")
    if samples > 0 and t.rows:
        lines.append(f"First {min(samples, len(t.rows))} rows (CSV):")
        lines.append(",".join(json.dumps(h, ensure_ascii=False) for h in t.headers))
        for r in t.rows[:samples]:
            lines.append(",".join(json.dumps(_sample(v, h, masker), ensure_ascii=False) for v, h in zip(r, t.headers)))
    q = masker.mask(question.strip()) if on else question.strip()
    lines += ["", "Question: " + q, "", PLAN_HELP]
    return "\n".join(lines)


# -- running a plan -------------------------------------------------------------

AGGS = ("sum", "avg", "count", "count_distinct", "min", "max")
OPS = ("=", "!=", ">", ">=", "<", "<=", "contains", "in", "between")
PERIODS = ("year", "quarter", "month", "week", "day")


def parse_plan(text: str) -> dict[str, Any]:
    s = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b < a:
        raise SheetError(tr("模型的回答里没有 JSON 计划"))
    try:
        plan = json.loads(s[a:b + 1])
    except json.JSONDecodeError as e:
        raise SheetError(tr("模型给的 JSON 计划格式不对：{0}", e)) from None
    if not isinstance(plan, dict):
        raise SheetError(tr("模型给的 JSON 计划格式不对：{0}", "not an object"))
    return plan


def _as_list(x: Any) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def check_plan(plan: dict[str, Any], headers: list[str]) -> None:
    """Raises SheetError naming the first problem (the message goes back to the model once)."""
    def col(name: Any, where: str) -> None:
        if name not in headers:
            raise SheetError(tr("计划里的列「{0}」（{1}）不在表里；可用的列：{2}", name, where, ", ".join(headers)))

    for f in _as_list(plan.get("filter")):
        if not isinstance(f, dict):
            raise SheetError(tr("计划的 {0} 写法不对", "filter"))
        col(f.get("column"), "filter")
        if f.get("op", "=") not in OPS:
            raise SheetError(tr("不认识的条件 {0}", f.get("op")))
        if f.get("op") == "between" and not (isinstance(f.get("value"), list) and len(f["value"]) == 2):
            raise SheetError(tr("between 需要 [下限, 上限]"))
    for g in _as_list(plan.get("group_by")):
        if isinstance(g, dict):
            col(g.get("column"), "group_by")
            if g.get("by") and g["by"] not in PERIODS:
                raise SheetError(tr("不认识的时间分组 {0}", g["by"]))
        else:
            col(g, "group_by")
    for m in _as_list(plan.get("metrics")):
        if not isinstance(m, dict) or m.get("agg") not in AGGS:
            raise SheetError(tr("计划的 {0} 写法不对", "metrics"))
        if m.get("column", "*") != "*":
            col(m.get("column"), "metrics")
    for c in _as_list(plan.get("columns")):
        col(c, "columns")


def _cmp_value(cell: Any, v: Any) -> Any:
    """The filter value in the cell's type, so 2026-01-05 compares with dates and "12" with numbers."""
    if isinstance(cell, (dt.date, dt.datetime)) and isinstance(v, str):
        p = parse_text(v)
        if isinstance(p, dt.datetime) and not isinstance(cell, dt.datetime):
            return p.date()
        if isinstance(p, dt.date) and not isinstance(p, dt.datetime) and isinstance(cell, dt.datetime):
            return dt.datetime(p.year, p.month, p.day)
        return p
    if isinstance(cell, (int, float)) and isinstance(v, str):
        p = parse_text(v)
        return p if isinstance(p, (int, float)) else v
    return v


def _match(cell: Any, op: str, v: Any) -> bool:
    if op == "contains":
        return cell is not None and str(v).lower() in show(cell).lower()
    if op == "in":
        return any(_eq(cell, _cmp_value(cell, x)) for x in _as_list(v))
    if op == "=":
        return _eq(cell, _cmp_value(cell, v))
    if op == "!=":
        return not _eq(cell, _cmp_value(cell, v))
    if cell is None:
        return False
    try:
        if op == "between":
            lo, hi = _cmp_value(cell, v[0]), _cmp_value(cell, v[1])
            return lo <= cell <= hi
        x = _cmp_value(cell, v)
        return {">": cell > x, ">=": cell >= x, "<": cell < x, "<=": cell <= x}[op]
    except TypeError:
        return False


def _eq(cell: Any, v: Any) -> bool:
    if isinstance(cell, str) and isinstance(v, str):
        return cell.strip().lower() == v.strip().lower()
    if isinstance(cell, (int, float)) and isinstance(v, (int, float)):
        return abs(cell - v) < 1e-9
    return cell == v


def _period(v: Any, by: str) -> Any:
    if not isinstance(v, (dt.date, dt.datetime)):
        return v
    if by == "year":
        return str(v.year)
    if by == "quarter":
        return f"{v.year}-Q{(v.month - 1) // 3 + 1}"
    if by == "month":
        return f"{v.year}-{v.month:02d}"
    if by == "week":
        y, w, _ = v.isocalendar()
        return f"{y}-W{w:02d}"
    return v.date().isoformat() if isinstance(v, dt.datetime) else v.isoformat()


def _agg(values: list[Any], agg: str) -> Any:
    if agg == "count":
        return sum(v is not None for v in values)
    if agg == "count_distinct":
        return len({show(v) for v in values if v is not None})
    vals = [v for v in values if v is not None]
    if agg in ("min", "max"):
        try:
            return (min if agg == "min" else max)(vals) if vals else None
        except TypeError:
            vals = [show(v) for v in vals]
            return (min if agg == "min" else max)(vals)
    nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return None
    total = sum(nums)
    if agg == "avg":
        return total / len(nums)
    return round(total, 10) if isinstance(total, float) else total


def _sort_key(v: Any) -> tuple:
    if v is None:
        return (2, "")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (0, v)
    return (1, show(v))


def run_plan(t: Table, plan: dict[str, Any]) -> Table:
    check_plan(plan, t.headers)
    idx = {h: i for i, h in enumerate(t.headers)}
    rows = t.rows
    for f in _as_list(plan.get("filter")):
        i, op = idx[f["column"]], f.get("op", "=")
        rows = [r for r in rows if _match(r[i], op, f.get("value"))]

    groups = _as_list(plan.get("group_by"))
    metrics = _as_list(plan.get("metrics"))
    if groups and not metrics:
        metrics = [{"column": "*", "agg": "count", "name": "count"}]
    if metrics:
        keys = []
        for g in groups:
            c, by = (g["column"], g.get("by")) if isinstance(g, dict) else (g, None)
            keys.append((idx[c], by, f"{c} ({by})" if by else c))
        names = [m.get("name") or (f"{m['agg']}({m.get('column', '*')})") for m in metrics]
        buckets: dict[tuple, list[list[Any]]] = {}
        for r in rows:
            k = tuple(_period(r[i], by) if by else r[i] for i, by, _ in keys)
            buckets.setdefault(k, []).append(r)
        if not keys:
            buckets = {(): rows}
        out = []
        for k, rs in buckets.items():
            vals = []
            for m in metrics:
                c = m.get("column", "*")
                vals.append(len(rs) if c == "*" else _agg([r[idx[c]] for r in rs], m["agg"]))
            out.append(list(k) + vals)
        headers = [n for _, _, n in keys] + names
        if keys:
            out.sort(key=lambda r: tuple(_sort_key(v) for v in r[:len(keys)]))
    else:
        cols = _as_list(plan.get("columns")) or t.headers
        headers = list(cols)
        out = [[r[idx[c]] for c in cols] for r in rows]

    for s in reversed(_as_list(plan.get("sort"))):
        by = s.get("by") if isinstance(s, dict) else s
        if by not in headers:
            raise SheetError(tr("排序用的列「{0}」不在结果里；结果的列：{1}", by, ", ".join(headers)))
        j = headers.index(by)
        nones = [r for r in out if r[j] is None]
        rest = sorted((r for r in out if r[j] is not None), key=lambda r: _sort_key(r[j]),
                      reverse=bool(isinstance(s, dict) and s.get("desc")))
        out = rest + nones
    limit = plan.get("limit")
    if isinstance(limit, int) and limit > 0:
        out = out[:limit]
    return Table(headers, out, str(plan.get("title") or t.name))


# -- writing results ------------------------------------------------------------

def render_text(res: Table, max_rows: int = MAX_PRINT_ROWS) -> list[str]:
    if len(res.rows) == 1 and len(res.headers) <= 3:
        return [f"{res.name}: " + tr("，").join(f"{h} {show(v)}" for h, v in zip(res.headers, res.rows[0]))]
    cells = [res.headers] + [[show(v) for v in r] for r in res.rows[:max_rows]]

    def width(s: str) -> int:
        return sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)

    w = [max(width(row[i]) for row in cells) for i in range(len(res.headers))]
    lines = [res.name]
    for n, row in enumerate(cells):
        lines.append("  ".join(c + " " * (w[i] - width(c)) for i, c in enumerate(row)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * x for x in w))
    if len(res.rows) > max_rows:
        lines.append(tr("……共 {0} 行，完整结果在 Excel 文件里", len(res.rows)))
    return lines


def _cell_xml(ref: str, v: Any, style: int = 0) -> str:
    s = f' s="{style}"' if style else ""
    if v is None:
        return ""
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"{s}><v>{int(v)}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"{s}><v>{v!r}</v></c>'
    return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{escape(show(v))}</t></is></c>'


def _ref(col: int, row: int) -> str:
    s, n = "", col + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def write_xlsx(t: Table, path: Path) -> Path:
    """A plain .xlsx that Excel, WPS and LibreOffice open (bold header, numbers stay numbers)."""
    return write_book([t], path)


def _sheet_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[\\/?*\[\]:]", " ", name or "Sheet1")[:31].strip() or "Sheet1"
    out, n = base, 2
    while out.lower() in used:
        out, n = f"{base[:28]}_{n}", n + 1
    used.add(out.lower())
    return out


def write_book(tables: list[Table], path: Path) -> Path:
    """One .xlsx with a sheet per table, in order (bold header row, numbers stay numbers)."""
    tables = list(tables) or [Table([], [], "Sheet1")]
    used: set[str] = set()
    names = [escape(_sheet_name(t.name, used), {'"': "&quot;"}) for t in tables]
    sheets_xml = []
    for t in tables:
        rows_xml = []
        for r, row in enumerate([t.headers] + t.rows, start=1):
            cells = "".join(_cell_xml(_ref(c, r), v, 1 if r == 1 else 0) for c, v in enumerate(row))
            rows_xml.append(f'<row r="{r}">{cells}</row>')
        sheets_xml.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                          '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                          f'<sheetData>{"".join(rows_xml)}</sheetData></worksheet>')
    n = len(tables)
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                  for i in range(1, n + 1)) +
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        + "".join(f'<sheet name="{names[i - 1]}" sheetId="{i}" r:id="rId{i}"/>' for i in range(1, n + 1)) +
        '</sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                  for i in range(1, n + 1)) +
        f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>',
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        '</styleSheet>',
    }
    for i, xml in enumerate(sheets_xml, start=1):
        files[f"xl/worksheets/sheet{i}.xml"] = xml
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


BG, INK, MUTED, ACCENT = "#FAF8F4", "#2B2B28", "#8A877F", "#8BAF56"
PIE = ["#8BAF56", "#5E8C6A", "#C9A66B", "#7FA7B5", "#B98A82", "#A3A86B", "#6F7FA8", "#D2B48C", "#9C9C94"]


def write_svg(res: Table, chart: dict[str, Any], path: Path) -> Path | None:
    """A bar, line or pie chart of two result columns. None when the columns do not fit a chart."""
    x, y = chart.get("x"), chart.get("y")
    if x not in res.headers:
        x = res.headers[0]
    if y not in res.headers:
        nums = [h for i, h in enumerate(res.headers) if kind([r[i] for r in res.rows]) == "number" and h != x]
        if not nums:
            return None
        y = nums[0]
    xi, yi = res.headers.index(x), res.headers.index(y)
    pts = [(show(r[xi]), float(r[yi])) for r in res.rows[:MAX_CHART_ITEMS]
           if isinstance(r[yi], (int, float)) and not isinstance(r[yi], bool)]
    if not pts:
        return None
    kind_ = chart.get("type") if chart.get("type") in ("bar", "line", "pie") else "bar"
    W, H, L, R, T, B = 760, 440, 70, 20, 50, 90
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="Segoe UI, Microsoft YaHei, sans-serif">',
           f'<rect width="{W}" height="{H}" fill="{BG}"/>',
           f'<text x="{L}" y="30" font-size="17" fill="{INK}">{escape(res.name)}</text>']
    if kind_ == "pie":
        total = sum(v for _, v in pts if v > 0)
        if total <= 0:
            return None
        import math

        cx, cy, rad, a = 250, 245, 160, -math.pi / 2
        for n, (label, v) in enumerate(p for p in pts if p[1] > 0):
            b = a + 2 * math.pi * v / total
            big = 1 if b - a > math.pi else 0
            x1, y1, x2, y2 = cx + rad * math.cos(a), cy + rad * math.sin(a), cx + rad * math.cos(b), cy + rad * math.sin(b)
            path_d = (f"M{cx},{cy} L{x1:.1f},{y1:.1f} A{rad},{rad} 0 {big} 1 {x2:.1f},{y2:.1f} Z" if b - a < 2 * math.pi - 1e-9
                      else f"M{cx - rad},{cy} a{rad},{rad} 0 1 0 {2 * rad},0 a{rad},{rad} 0 1 0 {-2 * rad},0")
            color = PIE[n % len(PIE)]
            out.append(f'<path d="{path_d}" fill="{color}" stroke="{BG}" stroke-width="2"/>')
            ly = 80 + n * 22
            if ly < H - 20:
                out.append(f'<rect x="450" y="{ly - 11}" width="12" height="12" fill="{color}"/>'
                           f'<text x="470" y="{ly}" font-size="13" fill="{INK}">{escape(label)}  '
                           f'{escape(show(v))} ({v / total:.0%})</text>')
            a = b
        out.append("</svg>")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(out), encoding="utf-8")
        return path

    lo = min(0.0, min(v for _, v in pts))
    hi = max(0.0, max(v for _, v in pts)) or 1.0
    pw, ph = W - L - R, H - T - B

    def ypos(v: float) -> float:
        return T + ph - (v - lo) / (hi - lo) * ph

    for k in range(5):
        v = lo + (hi - lo) * k / 4
        yy = ypos(v)
        out.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{W - R}" y2="{yy:.1f}" stroke="#E6E2D9"/>'
                   f'<text x="{L - 8}" y="{yy + 4:.1f}" font-size="11" fill="{MUTED}" text-anchor="end">{escape(show(round(v, 2)))}</text>')
    step = pw / len(pts)
    rotate = len(pts) > 8 or max(len(lbl) for lbl, _ in pts) > 6
    coords = []
    for n, (label, v) in enumerate(pts):
        cxp = L + step * (n + 0.5)
        if kind_ == "bar":
            top, base = ypos(max(v, 0)), ypos(min(v, 0))
            out.append(f'<rect x="{cxp - step * 0.35:.1f}" y="{top:.1f}" width="{step * 0.7:.1f}" '
                       f'height="{max(base - top, 0.5):.1f}" fill="{ACCENT}" rx="2"/>')
        coords.append((cxp, ypos(v)))
        lbl = escape(label[:18])
        if rotate:
            out.append(f'<text x="{cxp:.1f}" y="{T + ph + 14}" font-size="11" fill="{INK}" text-anchor="end" '
                       f'transform="rotate(-40 {cxp:.1f} {T + ph + 14})">{lbl}</text>')
        else:
            out.append(f'<text x="{cxp:.1f}" y="{T + ph + 18}" font-size="12" fill="{INK}" text-anchor="middle">{lbl}</text>')
    if kind_ == "line":
        out.append('<polyline fill="none" stroke="%s" stroke-width="2.5" points="%s"/>'
                   % (ACCENT, " ".join(f"{a:.1f},{b:.1f}" for a, b in coords)))
        out += [f'<circle cx="{a:.1f}" cy="{b:.1f}" r="3.5" fill="{ACCENT}"/>' for a, b in coords]
    out.append(f'<line x1="{L}" y1="{ypos(0):.1f}" x2="{W - R}" y2="{ypos(0):.1f}" stroke="{MUTED}"/>')
    out.append(f'<text x="{W - R}" y="30" font-size="12" fill="{MUTED}" text-anchor="end">{escape(y)}</text>')
    out.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def save(src: Path, res: Table, plan: dict[str, Any], out_dir: Path | None = None) -> dict[str, Path]:
    out_dir = Path(out_dir) if out_dir else src.parent / f"{src.stem}_orch"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    files = {"xlsx": write_xlsx(res, out_dir / f"{stamp}.xlsx")}
    p = out_dir / f"{stamp}.json"
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files["plan"] = p
    if isinstance(plan.get("chart"), dict) and res.rows:
        svg = write_svg(res, plan["chart"], out_dir / f"{stamp}.svg")
        if svg:
            files["chart"] = svg
    return files


# -- asking the model -------------------------------------------------------------

SYSTEM = ("You turn questions about a spreadsheet into a JSON plan that a program runs over the whole table. "
          "You only see column names and a few sample rows. Reply with the JSON object only.")


@dataclass
class Answer:
    plan: dict[str, Any]
    result: Table
    endpoint: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    tried: list[str] = field(default_factory=list)


def ask(t: Table, question: str, eps: list[Endpoint], samples: int = SAMPLE_ROWS, timeout: float = 120,
        masker=None) -> Answer:
    """Ask each endpoint in turn until one gives a plan that runs. A plan that names a wrong column is sent
    back once with the error; an endpoint that fails (no key, quota, timeout) passes the turn to the next."""
    from .redact import INSTRUCTION

    prompt = build_prompt(t, question, samples, masker)
    system = SYSTEM + (" " + INSTRUCTION if masker is not None and masker.enabled else "")
    tried: list[str] = []
    last: Exception | None = None
    for ep in eps:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        tok_in = tok_out = 0
        for attempt in range(2):
            try:
                reply = llm.chat(ep.base_url, ep.key, ep.model, messages, timeout=timeout, temperature=0,
                                 headers=ep.headers)
            except llm.LLMError as e:
                tried.append(f"{ep.label}: {e.kind}")
                last = e
                break
            tok_in += reply.tokens_in or 0
            tok_out += reply.tokens_out or 0
            try:
                plan = parse_plan(reply.text)
                if masker is not None:
                    plan = masker.unmask_obj(plan)        # filter values like [NAME_1] back to the real name
                res = run_plan(t, plan)
            except SheetError as e:
                last = e
                if attempt == 0:
                    messages += [{"role": "assistant", "content": reply.text},
                                 {"role": "user", "content": f"That plan cannot run: {e}\nReply with a corrected JSON plan only."}]
                    continue
                tried.append(f"{ep.label}: {e}")
                break
            tried.append(f"{ep.label}: ok")
            from .office import record

            record("sheet ask", ep, reply.model, tok_in or None, tok_out or None, calls=attempt + 1)
            return Answer(plan, res, ep.label, reply.model, tok_in or None, tok_out or None, tried)
    raise SheetError(tr("没有模型给出能用的计划（{0}）：{1}", "; ".join(tried), last))
