"""Follow money through bank statements (`orch trace`): everything is computed on this computer, no model.

    orch trace 张三流水.xlsx 李四流水.xls            # or a folder of statements
    orch trace 流水/ --days 5 --known 名单.txt
    orch trace 流水/ --columns                      # only show which column was taken for what

Each statement (Excel, WPS, CSV; old .xls converted first) is read row by row. Its columns are found by their
headers (交易日期, 交易金额, 借贷标志, 对方户名, 对方账号, 对方开户行, 摘要 ...); `--col 对方户名=收款人` fixes one
that was missed. The holder of a statement comes from its account / name columns, the lines above the header
("户名：张三"), or the file name.

The result is one Excel workbook and a picture:
  转账核对     every transfer from one loaded holder to another, and whether the other side's statement shows
               it arriving (same amount, within --days days, pointing back at the sender)
  未知去向     cash withdrawals and money sent to accounts that are none of the loaded holders (nor --known)
  对方汇总     every outside account: names, the bank written in the statements, the bank its card number
               suggests, how much went out and came in
  资金往来     who sent how much to whom
  流向图.svg   the same as a picture

What it cannot do: say who owns an account, or where cash went, beyond what the statements themselves say.
The bank guessed from a card number (its first six digits) is a hint, not a lookup.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

from .i18n import settings_path, tr
from .office import OfficeError
from .sheet import Table, parse_text, read_csv, read_xlsx, show, write_book

STATEMENT_EXTS = (".xlsx", ".xlsm", ".xls", ".et", ".csv", ".tsv")
HEADER_SCAN = 25           # the header row is looked for among the first rows of a sheet
DEFAULT_DAYS = 3
MAX_GRAPH_NODES = 16


class TraceError(OfficeError):
    pass


# -- columns ----------------------------------------------------------------------

# checked in this order: the counterparty columns before the plain ones ("对方账号" also contains "账号"),
# and the bank before the name ("对方开户行名称")
ROLES: list[tuple[str, re.Pattern]] = [
    ("cp_bank", re.compile(r"对方.*(开户行|行名|银行|机构|网点|行号)|对手.*(开户行|行名|银行|机构)|收.?付.?款人.*(开户行|银行)|"
                           r"counterpart(y)? ?bank|opposite bank", re.I)),
    ("cp_name", re.compile(r"对方.*(户名|名称|姓名|账户名)|对手.*(户名|名称|姓名)|收.?付.?款人(名称|户名|姓名)?$|"
                           r"交易对手$|对方单位|counterpart(y)? ?name|payee|opposite name", re.I)),
    ("cp_account", re.compile(r"对方.*(账号|帐号|卡号|账户$)|对手.*(账号|帐号|卡号|账户$)|收.?付.?款人(账号|帐号|卡号)|"
                              r"counterpart(y)? ?(account|acct)|opposite account", re.I)),
    ("in", re.compile(r"^(收入|收入金额|贷方|贷方金额|贷方发生额|存入|存入金额|转入金额|入账金额|credit|credit amount|deposits?)$", re.I)),
    ("out", re.compile(r"^(支出|支出金额|借方|借方金额|借方发生额|支取|支取金额|转出金额|出账金额|debit|debit amount|withdrawals?)$", re.I)),
    ("direction", re.compile(r"借贷|收支|收付|进出|交易方向|资金流向|^方向$|dr ?/ ?cr|direction", re.I)),
    ("amount", re.compile(r"金额|发生额|^amount$|transaction amount", re.I)),
    ("date", re.compile(r"日期|时间|^date$|transaction date|time", re.I)),
    ("account", re.compile(r"账号|帐号|卡号|^account( no\.?| number)?$", re.I)),
    ("name", re.compile(r"户名|客户名称|账户名称|^姓名$|查询对象|^name$|account name", re.I)),
    ("memo", re.compile(r"摘要|用途|附言|备注|交易类型|交易说明|业务类型|交易渠道|^渠道$|memo|description|remark|narrative", re.I)),
]
NOT_A_ROLE = re.compile(r"余额|balance|币种|currency|序号|流水号|凭证|柜员|^no\.?$", re.I)
ROLE_NAMES = {"date": "交易日期", "amount": "金额", "in": "收入", "out": "支出", "direction": "借贷标志",
              "account": "本方账号", "name": "本方户名", "cp_name": "对方户名", "cp_account": "对方账号",
              "cp_bank": "对方开户行", "memo": "摘要"}
_ROLE_ALIASES = {**{v: k for k, v in ROLE_NAMES.items()}, **{k: k for k in ROLE_NAMES},
                 "日期": "date", "交易金额": "amount", "收支": "direction", "借贷": "direction", "账号": "account",
                 "户名": "name", "对方银行": "cp_bank", "对方行": "cp_bank", "用途": "memo", "备注": "memo"}


def role_of(header: str) -> str | None:
    h = re.sub(r"\s+", "", str(header))
    if not h or NOT_A_ROLE.search(h):
        return None
    for role, pat in ROLES:
        if pat.search(h):
            return role
    return None


def parse_overrides(items: Iterable[str] | None) -> dict[str, str]:
    """`--col 对方户名=收款人名称` -> {"cp_name": "收款人名称"}."""
    out: dict[str, str] = {}
    for item in items or []:
        for part in str(item).split(","):
            if not part.strip():
                continue
            if "=" not in part:
                raise TraceError(tr("--col 要写成 角色=列名，例如 对方户名=收款人名称（{0}）", part))
            k, v = (s.strip() for s in part.split("=", 1))
            role = _ROLE_ALIASES.get(k) or _ROLE_ALIASES.get(k.lower())
            if role is None:
                raise TraceError(tr("不认识的角色「{0}」；可以用：{1}", k, "、".join(ROLE_NAMES.values())))
            out[role] = v
    return out


def map_columns(headers: list[str], overrides: dict[str, str] | None = None) -> dict[str, list[int]]:
    """role -> column indexes (memo can have several; the others take the first match)."""
    cols: dict[str, list[int]] = {}
    taken: set[int] = set()
    for role, name in (overrides or {}).items():
        if name in headers:
            i = headers.index(name)
            cols[role] = [i]
            taken.add(i)
    for i, h in enumerate(headers):
        if i in taken:
            continue
        role = role_of(h)
        if role is None or (role in cols and role != "memo") or role in (overrides or {}):
            continue
        cols.setdefault(role, []).append(i)
    return cols


def _usable(cols: dict[str, list[int]]) -> bool:
    return "date" in cols and ("amount" in cols or ("in" in cols and "out" in cols))


# -- reading statements --------------------------------------------------------------

@dataclass
class Record:
    date: dt.date | dt.datetime | None
    amount: float               # always positive
    out: bool                   # True: money left this account
    account: str = ""           # this holder's account (normalised)
    name: str = ""
    cp_name: str = ""
    cp_account: str = ""
    cp_bank: str = ""
    memo: str = ""
    source: str = ""            # file [sheet] row n
    holder: "Holder | None" = None
    cp_holder: "Holder | None" = None
    cp_by: str = ""             # "account" / "name": how the counterparty was recognised as a holder

    @property
    def day(self) -> dt.date | None:
        return self.date.date() if isinstance(self.date, dt.datetime) else self.date


@dataclass
class Sheet:
    file: Path
    sheet: str
    headers: list[str]
    cols: dict[str, list[int]]
    records: list[Record]
    holder_hint: tuple[str, str] = ("", "")       # (name, account) from the lines above the header
    skipped: int = 0


def norm_account(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return re.sub(r"[\s\-'\"`]", "", s).upper()


def norm_name(v: Any) -> str:
    if v is None:
        return ""
    s = re.sub(r"\s+", "", str(v))
    return s.replace("（", "(").replace("）", ")")


def same_account(a: str, b: str) -> bool:
    """Equal, or equal where one side is masked like 6222****1234 (prefix and suffix agree)."""
    if not a or not b:
        return False
    if a == b:
        return True
    for masked, full in ((a, b), (b, a)):
        if "*" in masked and "*" not in full:
            head, tail = masked.split("*", 1)[0], masked.rsplit("*", 1)[1]
            if len(head) + len(tail) >= 6 and full.startswith(head) and full.endswith(tail) and len(full) >= len(head) + len(tail):
                return True
    return False


_MONEY = re.compile(r"^[+-]?[\d,]*\.?\d+$")


def to_amount(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("，", ",").replace("¥", "").replace("￥", "").replace("元", "").replace(" ", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if not s or not _MONEY.match(s):
        return None
    n = float(s.replace(",", ""))
    return -n if neg else n


def direction(v: Any) -> bool | None:
    """True = out, False = in, None = unknown."""
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s:
        return None
    if s in ("借", "借方", "支出", "出", "支", "付", "付款", "转出", "d", "dr", "debit", "-"):
        return True
    if s in ("贷", "贷方", "收入", "入", "进", "收", "收款", "存", "存入", "转入", "c", "cr", "credit", "+"):
        return False
    if s[0] in "借支付出":
        return True
    if s[0] in "贷收入进存":
        return False
    return None


def to_date(v: Any) -> dt.date | dt.datetime | None:
    if isinstance(v, (dt.date, dt.datetime)):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        s = str(int(v))
        if len(s) == 8 and s[:2] in ("19", "20"):
            try:
                return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
            except ValueError:
                return None
        return None
    if isinstance(v, str):
        s = v.strip()
        m = re.match(r"^(\d{4})(\d{2})(\d{2})(?:\s*(\d{1,2}):?(\d{2}):?(\d{2})?)?$", s)
        if m:
            y, mo, d, h, mi, sec = m.groups()
            try:
                if h is None:
                    return dt.date(int(y), int(mo), int(d))
                return dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec or 0))
            except ValueError:
                return None
        p = parse_text(s)
        if isinstance(p, (dt.date, dt.datetime)):
            return p
        p = parse_text(s.split()[0]) if " " in s else None
        if isinstance(p, (dt.date, dt.datetime)):
            return p
    return None


_HINT_NAME = re.compile(r"(?:户名|客户名称|账户名称|姓名|查询对象)\s*[:：]\s*([^\s:：,，;；|]{2,30})")
_HINT_ACCT = re.compile(r"(?:账号|帐号|卡号|账户)\s*[:：]\s*([0-9*Xx\- ]{6,30}\d)")


def _hint(rows: list[list[Any]]) -> tuple[str, str]:
    name = acct = ""
    for r in rows:
        line = " ".join(str(v) for v in r if v is not None)
        if not name and (m := _HINT_NAME.search(line)):
            name = norm_name(m.group(1))
        if not acct and (m := _HINT_ACCT.search(line)):
            acct = norm_account(m.group(1))
    return name, acct


def _header_row(raw: list[list[Any]], overrides: dict[str, str]) -> int | None:
    best, best_score = None, 0
    for i, row in enumerate(raw[:HEADER_SCAN]):
        heads = [str(v).strip() if v is not None else "" for v in row]
        cols = map_columns(heads, overrides)
        score = len(cols) + (3 if _usable(cols) else 0)
        if score > best_score:
            best, best_score = i, score
    if best is None or not _usable(map_columns([str(v).strip() if v is not None else "" for v in raw[best]], overrides)):
        return None
    return best


def _cell(row: list[Any], cols: dict[str, list[int]], role: str) -> Any:
    idx = cols.get(role)
    if not idx:
        return None
    return row[idx[0]] if idx[0] < len(row) else None


def read_sheet(path: Path, name: str, raw: list[list[Any]], overrides: dict[str, str]) -> Sheet | None:
    h = _header_row(raw, overrides)
    if h is None:
        return None
    width = max(len(r) for r in raw[h:])
    headers = [str(v).strip() if v is not None else "" for v in (raw[h] + [None] * width)[:width]]
    cols = map_columns(headers, overrides)
    hint = _hint(raw[:h])
    sh = Sheet(path, name, headers, cols, [], hint)
    label = path.name + (f" [{name}]" if name and name != path.stem else "")
    for n, row in enumerate(raw[h + 1:], start=h + 2):
        row = (row + [None] * width)[:width]
        if not any(v is not None and str(v).strip() for v in row):
            continue
        day = to_date(_cell(row, cols, "date"))
        if "in" in cols and "out" in cols:
            a_in, a_out = to_amount(_cell(row, cols, "in")), to_amount(_cell(row, cols, "out"))
            if a_out:
                amount, out = abs(a_out), a_out > 0
            elif a_in:
                amount, out = abs(a_in), a_in < 0
            else:
                amount, out = None, None
        else:
            a = to_amount(_cell(row, cols, "amount"))
            d = direction(_cell(row, cols, "direction")) if "direction" in cols else None
            if a is None or a == 0:
                amount, out = None, None
            elif d is None:
                amount, out = abs(a), a < 0
            else:
                amount, out = abs(a), d
        if day is None or not amount:
            sh.skipped += 1                      # totals, page footers, notes
            continue
        memo = " ".join(str(row[i]).strip() for i in cols.get("memo", []) if row[i] is not None and str(row[i]).strip())
        sh.records.append(Record(
            date=day, amount=round(amount, 2), out=bool(out),
            account=norm_account(_cell(row, cols, "account")) or hint[1],
            name=norm_name(_cell(row, cols, "name")) or hint[0],
            cp_name=norm_name(_cell(row, cols, "cp_name")),
            cp_account=norm_account(_cell(row, cols, "cp_account")),
            cp_bank=str(_cell(row, cols, "cp_bank") or "").strip(),
            memo=memo, source=tr("{0} 第 {1} 行", label, n)))
    return sh


def statement_files(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in STATEMENT_EXTS
                          and not f.name.startswith(("~$", ".")) and not is_own_output(f))
        elif p.is_file():
            out.append(p)
        else:
            raise TraceError(tr("找不到文件：{0}", p))
    return out


def is_own_output(f: Path) -> bool:
    from .i18n_catalog import CATALOG

    stems = {"资金追踪", "查找结果"}
    for k in list(stems):
        stems |= {v for lang, v in CATALOG.get(k, {}).items() if lang != "src"}
    return any(f.stem.startswith(s + "_") for s in stems)


def read_statements(paths: Iterable[Path], overrides: dict[str, str] | None = None) -> tuple[list[Sheet], list[str]]:
    """Every sheet that looks like a statement, and a note for each file or sheet that did not."""
    from .convert import ready

    sheets: list[Sheet] = []
    notes: list[str] = []
    for f in statement_files(paths):
        try:
            src = ready(f)
            if src.suffix.lower() in (".csv", ".tsv"):
                parts = [(f.stem, read_csv(src))]
            elif src.suffix.lower() in (".xlsx", ".xlsm"):
                first, _, names = read_xlsx(src)
                parts = [(names[0] if names else f.stem, first)] + [(n, read_xlsx(src, n)[0]) for n in names[1:]]
            else:
                notes.append(tr("{0}：不是表格文件，跳过", f.name))
                continue
        except OfficeError as e:
            notes.append(f"{f.name}: {e}")
            continue
        found = []
        for name, raw in parts:
            if any(any(v is not None for v in r) for r in raw):
                sh = read_sheet(f, name, raw, overrides or {})
                if sh is not None:
                    found.append(sh)
        if not found:
            notes.append(tr("{0}：没找到日期和金额列，跳过（列名不一样可以用 --col 指定）", f.name))
        sheets += found
    return sheets, notes


# -- holders -------------------------------------------------------------------------

@dataclass
class Holder:
    label: str
    names: set[str] = field(default_factory=set)
    accounts: set[str] = field(default_factory=set)
    first: dt.date | None = None
    last: dt.date | None = None
    records: list[Record] = field(default_factory=list)

    def has_account(self, acct: str) -> bool:
        return any(same_account(acct, a) for a in self.accounts)

    def covers(self, day: dt.date | None, margin: int = 7) -> bool:
        """Is `day` inside the period this holder's statements show (a week of slack on each side: a statement
        usually starts before its first transaction)?"""
        if day is None or self.first is None or self.last is None:
            return True
        return self.first - dt.timedelta(days=margin) <= day <= self.last + dt.timedelta(days=margin)


def build_holders(sheets: list[Sheet]) -> list[Holder]:
    """One holder per person: statements that share an account or a name are the same holder."""
    holders: list[Holder] = []
    for sh in sheets:
        for r in sh.records:
            if not r.account and not r.name:
                r.name = norm_name(sh.file.stem)
            h = next((x for x in holders if (r.account and x.has_account(r.account)) or (r.name and r.name in x.names)), None)
            if h is None:
                h = Holder(r.name or r.account)
                holders.append(h)
            if r.account:
                h.accounts.add(r.account)
            if r.name:
                h.names.add(r.name)
                if h.label != r.name and h.label in h.accounts:
                    h.label = r.name
            r.holder = h
            h.records.append(r)
    for h in holders:
        days = [r.day for r in h.records if r.day]
        h.first, h.last = (min(days), max(days)) if days else (None, None)
    return holders


def find_holder(holders: list[Holder], acct: str, name: str) -> tuple[Holder | None, str]:
    if acct:
        for h in holders:
            if h.has_account(acct):
                return h, "account"
    if name:
        hits = [h for h in holders if name in h.names]
        if len(hits) == 1:
            return hits[0], "name"
    return None, ""


# -- banks from card numbers -----------------------------------------------------------

# first six digits of common Chinese debit cards (public BIN ranges). A hint only: banks have many ranges.
BINS: dict[str, str] = {
    "622202": "工商银行", "622208": "工商银行", "621226": "工商银行", "621225": "工商银行", "622200": "工商银行",
    "620200": "工商银行", "621558": "工商银行", "621559": "工商银行", "621721": "工商银行", "621723": "工商银行",
    "622848": "农业银行", "622845": "农业银行", "622846": "农业银行", "622841": "农业银行", "622823": "农业银行",
    "622821": "农业银行", "621282": "农业银行", "622827": "农业银行",
    "621661": "中国银行", "621663": "中国银行", "621660": "中国银行", "621785": "中国银行", "621786": "中国银行",
    "621787": "中国银行", "621788": "中国银行", "621789": "中国银行", "621790": "中国银行", "601382": "中国银行",
    "456351": "中国银行", "621756": "中国银行",
    "621700": "建设银行", "622700": "建设银行", "436742": "建设银行", "622280": "建设银行", "621081": "建设银行",
    "621467": "建设银行", "621284": "建设银行", "622166": "建设银行", "621673": "建设银行",
    "622260": "交通银行", "622262": "交通银行", "622258": "交通银行", "622261": "交通银行", "405512": "交通银行",
    "601428": "交通银行",
    "622588": "招商银行", "622580": "招商银行", "621483": "招商银行", "621485": "招商银行", "621486": "招商银行",
    "410062": "招商银行", "622575": "招商银行", "622609": "招商银行", "621286": "招商银行",
    "621799": "邮储银行", "622188": "邮储银行", "621098": "邮储银行", "620062": "邮储银行", "622150": "邮储银行",
    "622151": "邮储银行", "621096": "邮储银行",
    "622909": "兴业银行", "622908": "兴业银行", "438588": "兴业银行", "622901": "兴业银行",
    "622622": "民生银行", "622617": "民生银行", "622615": "民生银行", "621691": "民生银行", "415599": "民生银行",
    "622521": "浦发银行", "622522": "浦发银行", "622523": "浦发银行", "621792": "浦发银行", "621791": "浦发银行",
    "622516": "浦发银行", "622518": "浦发银行",
    "622690": "中信银行", "622691": "中信银行", "622692": "中信银行", "622696": "中信银行", "621773": "中信银行",
    "621771": "中信银行",
    "622660": "光大银行", "622661": "光大银行", "622662": "光大银行", "622663": "光大银行", "622664": "光大银行",
    "622665": "光大银行", "622666": "光大银行", "622667": "光大银行", "622668": "光大银行", "622669": "光大银行",
    "622670": "光大银行", "621489": "光大银行", "621490": "光大银行",
    "622155": "平安银行", "622156": "平安银行", "622157": "平安银行", "621626": "平安银行", "623058": "平安银行",
    "622630": "华夏银行", "622631": "华夏银行", "622632": "华夏银行", "622633": "华夏银行", "621222": "华夏银行",
    "622568": "广发银行", "621462": "广发银行",
}


def bins_path() -> Path:
    return settings_path().parent / "bins.txt"


def _user_bins() -> dict[str, str]:
    """More card ranges, one per line: `622202,工商银行` (in the orch settings folder)."""
    out: dict[str, str] = {}
    try:
        text = bins_path().read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        parts = re.split(r"[,，\t ]+", line.strip(), maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit() and not line.startswith("#"):
            out[parts[0]] = parts[1].strip()
    return out


def bank_from_card(acct: str, extra: dict[str, str] | None = None) -> str:
    """The bank a personal card number most likely belongs to, or "" (company accounts have no BIN)."""
    digits = acct if acct.isdigit() else ""
    if not (15 <= len(digits) <= 19):
        if "*" in acct and acct[:6].isdigit():
            digits = acct[:6]
        else:
            return ""
    table = {**BINS, **(extra or {})}
    for n in (8, 7, 6):
        if digits[:n] in table:
            return table[digits[:n]]
    return ""


# -- the analysis ------------------------------------------------------------------------

CASH = re.compile(r"取现|取款|现金|ATM|自助|柜面取|cash|withdraw", re.I)


@dataclass
class Report:
    holders: list[Holder]
    sheets: list[Sheet]
    notes: list[str]
    transfers: Table
    unknown: Table
    counterparties: Table
    flows: Table
    columns: Table
    counts: dict[str, int] = field(default_factory=dict)
    edges: list[tuple[str, str, int, float]] = field(default_factory=list)   # for the picture


def _d(r: Record) -> Any:
    return r.date


def _who(r: Record) -> str:
    h = r.holder
    return h.label if h else (r.name or r.account)


def _cp_label(r: Record) -> str:
    if r.cp_holder:
        return r.cp_holder.label
    return r.cp_name or r.cp_account or ""


def _points_back(r: Record, sender: Holder) -> bool | None:
    """Does a receiving record name the sender? None when it names nobody."""
    if not r.cp_account and not r.cp_name:
        return None
    if r.cp_account and sender.has_account(r.cp_account):
        return True
    if r.cp_name and r.cp_name in sender.names:
        return True
    return False


def known_list(items: Iterable[str] | None) -> tuple[set[str], set[str]]:
    """Names and accounts that are not "unknown": --known 名单.txt or --known 王五,6222...."""
    names: set[str] = set()
    accts: set[str] = set()
    for item in items or []:
        p = Path(item)
        words: list[str]
        if p.is_file():
            words = re.split(r"[\r\n,，;；\t]+", p.read_text(encoding="utf-8-sig", errors="replace"))
        else:
            words = re.split(r"[,，;；]+", item)
        for w in words:
            w = w.strip()
            if not w or w.startswith("#"):
                continue
            a = norm_account(w)
            if re.fullmatch(r"[0-9*X]{6,}", a):
                accts.add(a)
            else:
                names.add(norm_name(w))
    return names, accts


def analyse(sheets: list[Sheet], notes: list[str] | None = None, days: int = DEFAULT_DAYS,
            known: Iterable[str] | None = None, sender: str = "", receiver: str = "") -> Report:
    holders = build_holders(sheets)
    k_names, k_accts = known_list(known)
    bins = _user_bins()
    records = [r for h in holders for r in h.records]
    for r in records:
        r.cp_holder, r.cp_by = find_holder(holders, r.cp_account, r.cp_name)

    # 1. transfers between loaded holders: did it arrive?
    used: set[int] = set()
    t_rows: list[list[Any]] = []
    counts = {"transfers": 0, "arrived": 0, "missing": 0, "unknown": 0, "cash": 0}
    outs = sorted((r for r in records if r.out and r.cp_holder and r.cp_holder is not r.holder),
                  key=lambda r: _when(r.date))
    for r in outs:
        to = r.cp_holder
        assert to is not None and r.holder is not None
        cands = []
        for c in to.records:
            if c.out or id(c) in used or abs(c.amount - r.amount) > 0.005:
                continue
            if r.day and c.day:
                gap = (c.day - r.day).days
                if gap < -1 or gap > days:
                    continue
            else:
                gap = 0
            back = _points_back(c, r.holder)
            if back is False:
                continue
            cands.append((0 if back else 1, abs(gap), gap, c, back))
        if cands:
            cands.sort(key=lambda x: x[:2])
            _, _, gap, c, back = cands[0]
            used.add(id(c))
            status = tr("已到账") if back else tr("已到账（对方流水没写对方信息，只按金额和日期对上）")
            got = [c.date, c.source]
            counts["arrived"] += 1
        else:
            gap = None
            if not to.covers(r.day):
                status = tr("无法核对：对方流水不包含这段时间（{0} 至 {1}）", show(to.first), show(to.last))
            else:
                status = tr("未找到到账：对方流水 {0} 天内没有这笔收入", days)
                counts["missing"] += 1
            got = [None, ""]
        counts["transfers"] += 1
        t_rows.append([r.date, _who(r), to.label, r.amount, status, got[0], gap, r.memo, r.source, got[1]])
    # money that arrived from a loaded holder whose own statement shows no such transfer
    for h in holders:
        for c in h.records:
            if c.out or id(c) in used or not c.cp_holder or c.cp_holder is h:
                continue
            frm = c.cp_holder
            if not frm.covers(c.day):
                status = tr("无法核对：对方流水不包含这段时间（{0} 至 {1}）", show(frm.first), show(frm.last))
            else:
                status = tr("收到了，但对方流水里没有这笔转出")
                counts["missing"] += 1
            counts["transfers"] += 1
            t_rows.append([None, frm.label, h.label, c.amount, status, c.date, None, c.memo, "", c.source])
    if sender:
        t_rows = [row for row in t_rows if sender in str(row[1])]
    if receiver:
        t_rows = [row for row in t_rows if receiver in str(row[2])]
    t_rows.sort(key=lambda row: _when(row[0] or row[5]))
    transfers = Table([tr("转出日期"), tr("转出人"), tr("收款人"), tr("金额"), tr("结果"), tr("到账日期"), tr("相差天数"),
                       tr("摘要"), tr("转出记录"), tr("到账记录")], [_plain_dates(r) for r in t_rows], tr("转账核对"))

    # 2. cash and money to outside accounts
    u_rows: list[list[Any]] = []
    for r in sorted(records, key=lambda r: _when(r.date)):
        if not r.out or r.cp_holder:
            continue
        if r.cp_account and any(same_account(r.cp_account, a) for a in k_accts):
            continue
        if r.cp_name and r.cp_name in k_names:
            continue
        own_names = r.holder.names if r.holder else set()
        if not r.cp_account and CASH.search(r.memo) and (not r.cp_name or r.cp_name in own_names):
            kind = tr("取现")
            counts["cash"] += 1
        elif r.cp_name and r.cp_name in own_names:
            kind = tr("转给本人的其他账户（没有这个账户的流水）")
        elif not r.cp_account and not r.cp_name:
            kind = tr("流水里没写对方")
        else:
            kind = tr("转给外部账户")
        counts["unknown"] += 1
        u_rows.append([r.date, _who(r), r.amount, kind, r.cp_name, r.cp_account, r.cp_bank,
                       bank_from_card(r.cp_account, bins), r.memo, r.source])
    unknown = Table([tr("日期"), tr("转出人"), tr("金额"), tr("类型"), tr("对方户名"), tr("对方账号"),
                     tr("对方银行（流水里写的）"), tr("按卡号推测的银行"), tr("摘要"), tr("记录")],
                    [_plain_dates(r) for r in u_rows], tr("未知去向"))

    # 3. every outside counterparty
    groups: dict[str, dict[str, Any]] = {}
    for r in records:
        if r.cp_holder or not (r.cp_account or r.cp_name):
            continue
        key = ("a:" + r.cp_account) if r.cp_account else ("n:" + r.cp_name)
        if r.cp_account and "*" in r.cp_account:            # a masked number joins a full one when they agree
            key = next((k for k in groups if k.startswith("a:") and same_account(k[2:], r.cp_account)), key)
        g = groups.setdefault(key, {"names": [], "acct": r.cp_account, "banks": [], "out_n": 0, "out_sum": 0.0,
                                    "in_n": 0, "in_sum": 0.0, "with": [], "first": None, "last": None})
        if r.cp_name and r.cp_name not in g["names"]:
            g["names"].append(r.cp_name)
        if r.cp_bank and r.cp_bank not in g["banks"]:
            g["banks"].append(r.cp_bank)
        if r.out:
            g["out_n"] += 1
            g["out_sum"] += r.amount
        else:
            g["in_n"] += 1
            g["in_sum"] += r.amount
        if _who(r) not in g["with"]:
            g["with"].append(_who(r))
        if r.day:
            g["first"] = min(g["first"] or r.day, r.day)
            g["last"] = max(g["last"] or r.day, r.day)
    c_rows = []
    for g in sorted(groups.values(), key=lambda g: -(g["out_sum"] + g["in_sum"])):
        c_rows.append(["、".join(g["names"]), g["acct"], "、".join(g["banks"]), bank_from_card(g["acct"], bins),
                       g["out_n"], round(g["out_sum"], 2), g["in_n"], round(g["in_sum"], 2), "、".join(g["with"]),
                       show(g["first"]), show(g["last"])])
    counterparties = Table([tr("对方户名"), tr("对方账号"), tr("对方银行（流水里写的）"), tr("按卡号推测的银行"),
                            tr("转给对方次数"), tr("转给对方合计"), tr("对方转来次数"), tr("对方转来合计"),
                            tr("和谁往来"), tr("最早"), tr("最晚")], c_rows, tr("对方汇总"))

    # 4. who sent how much to whom (each transfer once: from the sender's statement when it is loaded)
    agg: dict[tuple[str, str], list[Any]] = {}
    for r in records:
        if r.out:
            frm, to = _who(r), (_cp_label(r) or (tr("取现") if CASH.search(r.memo) else tr("（流水里没写对方）")))
            if r.cp_holder is r.holder:
                continue
        else:
            if r.cp_holder and r.cp_holder is not r.holder:
                continue                                   # counted on the sender's side
            if r.cp_holder is r.holder:
                continue
            frm, to = (_cp_label(r) or tr("（流水里没写对方）")), _who(r)
        e = agg.setdefault((frm, to), [0, 0.0, None, None])
        e[0] += 1
        e[1] += r.amount
        if r.day:
            e[2] = min(e[2] or r.day, r.day)
            e[3] = max(e[3] or r.day, r.day)
    f_rows = [[a, b, n, round(s, 2), show(f), show(l)] for (a, b), (n, s, f, l) in
              sorted(agg.items(), key=lambda kv: -kv[1][1])]
    flows = Table([tr("从"), tr("到"), tr("笔数"), tr("合计"), tr("最早"), tr("最晚")], f_rows, tr("资金往来"))

    col_rows = []
    for sh in sheets:
        found = "，".join(f"{tr(ROLE_NAMES[k])}={sh.headers[v[0]]}" + ("+" + "+".join(sh.headers[i] for i in v[1:]) if len(v) > 1 else "")
                         for k, v in sh.cols.items())
        missing = "，".join(tr(ROLE_NAMES[k]) for k in ("cp_name", "cp_account", "cp_bank", "memo") if k not in sh.cols)
        holder = sh.records[0].holder.label if sh.records and sh.records[0].holder else ""
        col_rows.append([sh.file.name, sh.sheet, holder, len(sh.records), sh.skipped, found, missing])
    columns = Table([tr("文件"), tr("工作表"), tr("户主"), tr("读到的交易"), tr("跳过的行"), tr("认出的列"), tr("没有的列")],
                    col_rows, tr("读取情况"))
    return Report(holders, sheets, list(notes or []), transfers, unknown, counterparties, flows, columns, counts,
                  [(a, b, n, s) for (a, b), (n, s, _, _) in agg.items()])


def _when(v: Any) -> dt.datetime:
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    return dt.datetime.min


def _plain_dates(row: list[Any]) -> list[Any]:
    return [show(v) if isinstance(v, (dt.date, dt.datetime)) else v for v in row]


# -- the picture ---------------------------------------------------------------------------

def write_graph(rep: Report, path: Path) -> Path | None:
    """Holders and the largest outside accounts on a circle, an arrow for each flow with its total."""
    from .sheet import ACCENT, BG, INK, MUTED

    if not rep.edges:
        return None
    holders = [h.label for h in rep.holders]
    weight: dict[str, float] = {}
    for a, b, _, s in rep.edges:
        for n in (a, b):
            if n not in holders:
                weight[n] = weight.get(n, 0.0) + s
    outside = sorted(weight, key=lambda n: -weight[n])[:max(0, MAX_GRAPH_NODES - len(holders))]
    nodes = holders[:MAX_GRAPH_NODES] + outside
    keep = set(nodes)
    edges = [e for e in rep.edges if e[0] in keep and e[1] in keep]
    if not edges:
        return None
    W, H = 900, 760
    cx, cy, rad = W / 2, H / 2 + 10, 290
    pos = {}
    for i, n in enumerate(nodes):
        a = -math.pi / 2 + 2 * math.pi * i / len(nodes)
        pos[n] = (cx + rad * math.cos(a), cy + rad * math.sin(a))
    top = max(s for *_, s in edges) or 1.0
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="Segoe UI, Microsoft YaHei, sans-serif">',
           f'<rect width="{W}" height="{H}" fill="{BG}"/>',
           '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="11" markerHeight="11" markerUnits="userSpaceOnUse" orient="auto-start-reverse">'
           f'<path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>',
           f'<text x="24" y="34" font-size="18" fill="{INK}">{escape(tr("资金流向"))}</text>',
           f'<text x="24" y="56" font-size="12" fill="{MUTED}">{escape(tr("绿色：有流水的人；灰色：外部账户。线越粗金额越大。"))}</text>']
    pairs = {(a, b) for a, b, *_ in edges}
    for a, b, n, s in sorted(edges, key=lambda e: e[3]):
        (x1, y1), (x2, y2) = pos[a], pos[b]
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy) or 1
        ux, uy = dx / dist, dy / dist
        sx, sy, ex, ey = x1 + ux * 30, y1 + uy * 30, x2 - ux * 32, y2 - uy * 32
        bend = 40 if (b, a) in pairs else 0                  # two-way: bend the lines apart
        mx, my = (sx + ex) / 2 - uy * bend, (sy + ey) / 2 + ux * bend
        width = 1 + 5 * s / top
        out.append(f'<path d="M{sx:.1f},{sy:.1f} Q{mx:.1f},{my:.1f} {ex:.1f},{ey:.1f}" fill="none" stroke="{MUTED}" '
                   f'stroke-opacity="0.75" stroke-width="{width:.1f}" marker-end="url(#arr)"/>')
        lx, ly = (sx + 2 * mx + ex) / 4, (sy + 2 * my + ey) / 4
        out.append(f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="11" fill="{INK}" text-anchor="middle" '
                   f'paint-order="stroke" stroke="{BG}" stroke-width="3">{escape(show(round(s, 2)))}（{n}）</text>')
    for nm in nodes:
        x, y = pos[nm]
        fill = ACCENT if nm in holders else "#C9C5BB"
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="26" fill="{fill}"/>')
        label = nm if len(nm) <= 12 else nm[:5] + "…" + nm[-5:]
        ty = y + 44 if y >= cy else y - 34
        out.append(f'<text x="{x:.1f}" y="{ty:.1f}" font-size="13" fill="{INK}" text-anchor="middle">{escape(label)}</text>')
    out.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def save(rep: Report, out_dir: Path) -> dict[str, Path]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = out_dir / f"{tr('资金追踪')}_{stamp}"
    files = {"xlsx": write_book([rep.transfers, rep.unknown, rep.counterparties, rep.flows, rep.columns],
                                base.with_suffix(".xlsx"))}
    svg = write_graph(rep, base.with_suffix(".svg"))
    if svg:
        files["chart"] = svg
    return files


def run(paths: list[Path], days: int = DEFAULT_DAYS, known: Iterable[str] | None = None,
        overrides: dict[str, str] | None = None, sender: str = "", receiver: str = "") -> Report:
    if days < 0:
        raise TraceError(tr("--days 不能是负数"))
    sheets, notes = read_statements(paths, overrides)
    if not sheets:
        raise TraceError(tr("没有读到任何流水。{0}", " ".join(notes)))
    return analyse(sheets, notes, days, known, sender, receiver)


def summary_lines(rep: Report) -> list[str]:
    c = rep.counts
    lines = [tr("读了 {0} 份流水，{1} 个人：{2}", len(rep.sheets), len(rep.holders), "、".join(h.label for h in rep.holders))]
    lines.append(tr("互相转账 {0} 笔：已到账 {1}，没对上 {2}", c["transfers"], c["arrived"], c["missing"]))
    lines.append(tr("转给外部或取现 {0} 笔（其中取现 {1}），外部账户 {2} 个", c["unknown"], c["cash"], len(rep.counterparties.rows)))
    return lines
