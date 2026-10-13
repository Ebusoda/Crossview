"""Masking for office work: personal details are swapped for placeholders before anything goes to a
model, and swapped back in the answer, on this computer.

    13812345678          -> [PHONE_1]
    11010519491231002X   -> [ID_1]
    6222 0212 3456 7890 128 (passes the card checksum) -> [CARD_1]
    010-12345678         -> [TEL_1]
    zhang@example.com    -> [EMAIL_1]
    姓名：张三 / 联系人：李四  -> 姓名：[NAME_1]   (a name after one of these labels)
    words you list       -> [WORD_1]       (--mask 张三,某某单位, or one per line in mask_words.txt)

In a table, a whole column whose header says it holds names, phones or ID numbers is masked.
The same value always gets the same placeholder, so the model can still tell people apart.
Masking catches the common patterns; it cannot promise to find every personal detail. Names in running
text are only caught after a label or when you list them.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .i18n import settings_path, tr

LABELS = ("姓名|名字|联系人|经办人|负责人|申请人|收件人|收款人|当事人|报告人|填报人|填表人|审核人|审批人|签收人|"
          "承办人|值班人|患者|学生|员工|家长|原告|被告")
_NAME_AFTER_LABEL = re.compile(rf"((?:{LABELS})\s*[:：]\s*)([一-龥·]{{2,4}})")
_ID = re.compile(r"(?<![0-9A-Za-z])(\d{17}[\dXx])(?![0-9A-Za-z])")
_CARD = re.compile(r"(?<!\d)(\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}(?:[ -]?\d{1,3})?)(?!\d)")
_PHONE = re.compile(r"(?<!\d)(?:\+?86[ -]?)?(1[3-9]\d[ -]?\d{4}[ -]?\d{4})(?!\d)")
_TEL = re.compile(r"(?<!\d)(0\d{2,3}-\d{7,8})(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TOKEN = re.compile(r"\[(NAME|PHONE|ID|CARD|TEL|EMAIL|WORD)_(\d+)\]")

# table columns masked as a whole, by header
COLUMN_KINDS = [("NAME", re.compile(r"姓名|名字|联系人|经办人|负责人|申请人|收件人|收款人|^name$|full ?name", re.I)),
                ("PHONE", re.compile(r"手机|电话|联系方式|phone|mobile|tel", re.I)),
                ("ID", re.compile(r"身份证|证件号|id ?card|passport", re.I)),
                ("CARD", re.compile(r"银行卡|卡号|账号|account", re.I)),
                ("EMAIL", re.compile(r"邮箱|e-?mail", re.I))]

INSTRUCTION = ("Placeholders such as [NAME_1], [PHONE_2] or [WORD_1] stand for hidden personal details. "
               "Keep them exactly as written, wherever they belong in your answer; do not invent the real values.")


def words_path() -> Path:
    return settings_path().parent / "mask_words.txt"


def saved_words() -> list[str]:
    try:
        text = words_path().read_text(encoding="utf-8-sig")
    except OSError:
        return []
    return [w.strip() for w in text.splitlines() if w.strip() and not w.strip().startswith("#")]


def _id_ok(s: str) -> bool:
    w = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    total = sum(int(c) * k for c, k in zip(s[:17], w))
    return "10X98765432"[total % 11] == s[17].upper()


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


class Masker:
    """Swaps personal details for placeholders and back. One Masker per request, so the same value always
    gets the same placeholder across the question, the material and the samples."""

    def __init__(self, words: list[str] | tuple[str, ...] = (), enabled: bool = True):
        self.enabled = enabled
        self.words = sorted({w.strip() for w in words if w and w.strip()}, key=len, reverse=True)
        self.real: dict[str, str] = {}          # placeholder -> real value
        self.token: dict[tuple[str, str], str] = {}

    def _put(self, kind: str, value: str) -> str:
        key = (kind, re.sub(r"[ -]", "", value) if kind in ("PHONE", "CARD", "ID") else value)
        if key not in self.token:
            n = sum(1 for k, _ in self.token if k == kind) + 1
            tok = f"[{kind}_{n}]"
            self.token[key] = tok
            self.real[tok] = value
        return self.token[key]

    def mask(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        for w in self.words:
            if w in text:
                text = text.replace(w, self._put("WORD", w))
        # names already met (in a name column, or after a label) are hidden wherever they appear again
        for (kind, value), tok in sorted(self.token.items(), key=lambda kv: -len(kv[0][1])):
            if kind == "NAME" and len(value) >= 2 and value in text:
                text = text.replace(value, tok)
        text = _EMAIL.sub(lambda m: self._put("EMAIL", m.group(0)), text)
        text = _ID.sub(lambda m: self._put("ID", m.group(1)) if _id_ok(m.group(1)) else m.group(0), text)

        def card(m: re.Match) -> str:
            digits = re.sub(r"[ -]", "", m.group(1))
            return self._put("CARD", m.group(1)) if 16 <= len(digits) <= 19 and _luhn(digits) else m.group(0)

        text = _CARD.sub(card, text)
        text = _PHONE.sub(lambda m: m.group(0).replace(m.group(1), self._put("PHONE", m.group(1))), text)
        text = _TEL.sub(lambda m: self._put("TEL", m.group(1)), text)
        text = _NAME_AFTER_LABEL.sub(lambda m: m.group(1) + self._put("NAME", m.group(2)), text)
        return text

    def mask_cell(self, value: str, header: str) -> str:
        """A table cell: the whole value when its column holds personal details, else the patterns."""
        if not self.enabled or not value:
            return value
        for kind, pat in COLUMN_KINDS:
            if pat.search(header):
                return self._put(kind, value)
        return self.mask(value)

    def learn_column(self, values: list[str], header: str) -> None:
        """Give every value of a personal column its placeholder up front, so the same names are hidden in
        the question too (nothing is sent by this)."""
        if not self.enabled:
            return
        for kind, pat in COLUMN_KINDS:
            if pat.search(header):
                for v in values:
                    if v:
                        self._put(kind, v)
                return

    def unmask(self, text: str) -> str:
        if not self.real or not text:
            return text
        return _TOKEN.sub(lambda m: self.real.get(m.group(0), m.group(0)), text)

    def unmask_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.unmask(obj)
        if isinstance(obj, list):
            return [self.unmask_obj(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.unmask_obj(v) for k, v in obj.items()}
        return obj

    def summary(self) -> str:
        if not self.enabled:
            return tr("没有脱敏（--no-mask）")
        if not self.real:
            return tr("脱敏：没找到需要隐藏的个人信息")
        names = {"NAME": tr("姓名"), "PHONE": tr("手机号"), "ID": tr("身份证号"), "CARD": tr("银行卡号"),
                 "TEL": tr("座机号"), "EMAIL": tr("邮箱"), "WORD": tr("你指定的词")}
        counts: dict[str, int] = {}
        for kind, _ in self.token:
            counts[kind] = counts.get(kind, 0) + 1
        return tr("脱敏：{0}（发出去的是占位符，结果里已换回原文）",
                  tr("，").join(f"{names[k]} {n}" for k, n in counts.items()))


def make(extra: list[str] | None = None, enabled: bool = True) -> Masker:
    words = list(saved_words())
    for item in extra or []:
        words += [w for w in re.split(r"[,，、]", item) if w.strip()]
    return Masker(words, enabled)
