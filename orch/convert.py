"""Open old office formats: .doc / .wps / .rtf become .docx, .xls / .et become .xlsx, before orch reads them.

The conversion is done by an office program already on this computer, tried in this order:
  1. Microsoft Word / Excel (Windows, through COM)
  2. WPS Office (Windows, through COM: KWps.Application / Ket.Application)
  3. LibreOffice (`soffice --headless --convert-to`, any system)
The original file is never changed; the converted copy goes to <settings folder>/converted/ and is reused while the
original is unchanged. Without any of the three, the message says to save the file as .docx / .xlsx by hand.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .i18n import settings_path, tr
from .office import OfficeError

WORD_EXTS = (".doc", ".wps", ".rtf", ".wpt", ".dot")
SHEET_EXTS = (".xls", ".et", ".ett", ".xlt")
TIMEOUT = 180

# PowerShell: paths come in environment variables, so nothing in a file name needs quoting
_PS_WORD = r"""
$ErrorActionPreference = 'Stop'
$app = New-Object -ComObject $env:MAO_PROGID
try {
  $app.Visible = $false
  try { $app.DisplayAlerts = 0 } catch {}
  $d = $app.Documents.Open($env:MAO_SRC, $false, $true)
  try { $d.SaveAs2($env:MAO_DST, 16) } catch { $d.SaveAs($env:MAO_DST, 16) }
  $d.Close($false)
} finally { $app.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null }
"""
_PS_SHEET = r"""
$ErrorActionPreference = 'Stop'
$app = New-Object -ComObject $env:MAO_PROGID
try {
  $app.Visible = $false
  $app.DisplayAlerts = $false
  $w = $app.Workbooks.Open($env:MAO_SRC, 0, $true)
  $w.SaveAs($env:MAO_DST, 51)
  $w.Close($false)
} finally { $app.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null }
"""


class ConvertError(OfficeError):
    pass


def needs(path: Path) -> bool:
    return Path(path).suffix.lower() in WORD_EXTS + SHEET_EXTS


def target_ext(path: Path) -> str:
    return ".docx" if Path(path).suffix.lower() in WORD_EXTS else ".xlsx"


def cache_dir() -> Path:
    return settings_path().parent / "converted"


def _run(cmd: list[str], env: dict[str, str] | None = None) -> bool:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL,
                           env={**os.environ, **(env or {})})
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0


def _com(progid: str, src: Path, dst: Path) -> bool:
    if sys.platform != "win32":
        return False
    script = _PS_WORD if dst.suffix == ".docx" else _PS_SHEET
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                {"MAO_PROGID": progid, "MAO_SRC": str(src), "MAO_DST": str(dst)})


def _libreoffice(src: Path, dst: Path) -> bool:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe and sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
            cand = Path(base) / "LibreOffice" / "program" / "soffice.exe"
            if base and cand.is_file():
                exe = str(cand)
                break
    if not exe:
        return False
    out = dst.parent / f"lo-{dst.stem}"
    out.mkdir(parents=True, exist_ok=True)
    if not _run([exe, "--headless", "--convert-to", dst.suffix.lstrip("."), "--outdir", str(out), str(src)]):
        return False
    made = out / (src.stem + dst.suffix)
    if not made.is_file():
        return False
    made.replace(dst)
    shutil.rmtree(out, ignore_errors=True)
    return True


def converters(dst_ext: str) -> list[tuple[str, Callable[[Path, Path], bool]]]:
    word = dst_ext == ".docx"
    return [("Microsoft " + ("Word" if word else "Excel"),
             lambda s, d: _com("Word.Application" if word else "Excel.Application", s, d)),
            ("WPS", lambda s, d: _com("KWps.Application" if word else "Ket.Application", s, d)),
            ("LibreOffice", _libreoffice)]


def convert(path: Path, tried: list[str] | None = None) -> Path:
    """The .docx / .xlsx copy of an old-format file (made now, or reused from the cache)."""
    src = Path(path).resolve()
    if not src.is_file():
        raise ConvertError(tr("找不到文件：{0}", src))
    ext = target_ext(src)
    stat = src.stat()
    key = hashlib.sha1(f"{src}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")).hexdigest()[:12]
    dst = cache_dir() / f"{src.stem}-{key}{ext}"
    if dst.is_file() and dst.stat().st_size > 0:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    for name, fn in converters(ext):
        if tried is not None:
            tried.append(name)
        if fn(src, dst) and dst.is_file() and dst.stat().st_size > 0:
            return dst
    raise ConvertError(tr("打不开老格式的 {0}：这台电脑上没有能转换它的 Word / Excel、WPS 或 LibreOffice。"
                          "请在 Word / Excel / WPS 里把它另存为 {1}", src.name, ext))


def ready(path: Path) -> Path:
    """`path` itself, or its converted copy when it is an old format."""
    return convert(path) if needs(path) else Path(path)
