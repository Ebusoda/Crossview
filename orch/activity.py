"""Live activity of one agent run (what it read, changed, ran and said), read from the run's raw events file.

Each executor writes its own JSON-lines dialect: Claude Code stream-json, Codex `exec --json`, opencode
`run --format json`, Gemini stream-json, and the api and fake executors. The dialect is recognised per line
from the line's shape, never from the executor's name. Feed tails one file incrementally and turns the lines
into normalized items, upserted by id so that a tool call and its result end up in one row. feed() keeps one
Feed per file for the whole process (the web polls it every few seconds); a poller that needs items and
ended to agree reads Feed.snapshot().

Item keys (always all of them): id, seq (line number where first seen), t (epoch s or None), kind, target,
detail, ok (None = running/unknown), exit_code, added, removed, tool (raw tool/item name), sub (Claude
sub-agent), task_file (target is an orchestrator file under .task/), output (tail of the command output).
"""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import threading
from collections import Counter, OrderedDict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

MAX_TARGET, MAX_DETAIL, MAX_OUTPUT, MAX_FINAL = 120, 500, 2000, 4000
MAX_FEEDS = 128  # feeds kept by feed(); the least recently used is dropped first
MAX_LINE = 64 << 20  # a longer events line is skipped (bounded memory if a CLI prints a blob without newlines)
MAX_SCRIPT = 16 << 10  # commands are simplified/classified from their first 16 KB (shlex is slow on heredocs)
CHUNK = 4 << 20  # the events file is read this much at a time
KINDS = ("start", "say", "think", "read", "list", "search", "edit", "write", "delete", "run", "web", "todo",
         "agent", "tool", "error", "limit", "done")
TASK_FILES = frozenset({"prompt.md", "task.md", "progress.md", "handoff.md", "review.md", "verify.log",
                        "context.md", "review_prompt.md", "review_diff.patch", "conflicts.md"})
_PATH_KINDS = ("read", "list", "edit", "write", "delete")

# --- text -------------------------------------------------------------------------------------------

_SURROGATE = re.compile("[\ud800-\udfff]")


def _s(v: Any) -> str:
    """str without lone surrogates (a JSON string cut mid-emoji): they would break .encode("utf-8")."""
    s = v if isinstance(v, str) else "" if v is None or isinstance(v, (dict, list)) else str(v)
    return _SURROGATE.sub("\ufffd", s)


def _clip(v: Any, n: int, flat: bool = True) -> str:
    if isinstance(v, str) and len(v) > 20 * n:
        v = v[: 20 * n]                   # a preview: never collapse megabytes to show 500 characters
    s = _s(v).strip()
    if flat:
        s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _clip_left(s: str, n: int = MAX_TARGET) -> str:
    """Paths keep their end (the file name) when too long."""
    return s if len(s) <= n else "…" + s[-(n - 1):]


def _tail(v: Any, n: int = MAX_OUTPUT) -> str:
    if isinstance(v, str) and len(v) > n:
        v = v[-(n + 1):]                  # still longer than n: gets its "…" below
    s = _s(v)
    return s if len(s) <= n else "…" + s[-(n - 1):]


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _l(v: Any) -> list:
    return v if isinstance(v, list) else []


def _num(v: Any) -> float | None:
    """A finite number (JSON allows NaN/Infinity and huge integers; neither may reach a browser)."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def _int(v: Any) -> int:
    f = _num(v)
    return int(f) if f is not None else 0


def _intn(v: Any) -> int | None:
    """An exit code or line count: an int (not a bool), else None."""
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _nlines(text: Any) -> int | None:
    if not isinstance(text, str):
        return None
    return text.count("\n") + (0 if not text or text.endswith("\n") else 1)


def _diff_counts(old: str, new: str) -> tuple[int, int]:
    """Lines added/removed when old is replaced by new: an estimate until the tool reports its patch.
    Common leading/trailing lines are dropped, the rest compared as multisets (linear time; a real diff
    of two large blocks with many equal lines can take minutes)."""
    a, b = old.splitlines(), new.splitlines()
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    j = 0
    while j < len(a) - i and j < len(b) - i and a[-1 - j] == b[-1 - j]:
        j += 1
    ca, cb = Counter(a[i:len(a) - j]), Counter(b[i:len(b) - j])
    return sum((cb - ca).values()), sum((ca - cb).values())


def _args(inp: Any) -> str:
    """Arguments of an unknown tool for its detail line, long values cut (no file contents kept)."""
    short = {k: _clip(v, 80) if isinstance(v, str) else v for k, v in list(_d(inp).items())[:12]}
    return _clip(json.dumps(short, ensure_ascii=False, default=str)[: 20 * MAX_DETAIL], MAX_DETAIL)


def _patch_counts(hunks: Any) -> tuple[int, int]:
    add = rem = 0
    for h in _l(hunks):
        for ln in _l(_d(h).get("lines")):
            if isinstance(ln, str) and ln.startswith("+"):
                add += 1
            elif isinstance(ln, str) and ln.startswith("-"):
                rem += 1
    return add, rem


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(_s(b.get("text")) for b in _l(content) if isinstance(b, dict) and b.get("type", "text") == "text")


def _todo(todos: Any) -> tuple[str, str]:
    """(current item, "done/total") of a todo list in any dialect."""
    items = [x for x in _l(todos) if isinstance(x, dict)]
    done = [x for x in items if x.get("completed") is True or x.get("status") in ("completed", "done")]
    cur = next((x for x in items if x.get("status") == "in_progress"), None) or \
        next((x for x in items if x not in done), None)
    text = (cur.get("content") or cur.get("text") or cur.get("description") or cur.get("activeForm")) if cur else ""
    return _clip(text, MAX_TARGET), f"{len(done)}/{len(items)}"


_LIMIT = re.compile(r"usage limit|rate.?limit|quota|too many requests|resource.?exhausted|\b429\b", re.I)


def _err_kind(msg: str) -> str:
    return "limit" if _LIMIT.search(msg) else "error"


# --- paths ------------------------------------------------------------------------------------------

_DRIVE = re.compile(r"^[A-Za-z]:(?:/|$)")


def _norm(p: Any, gitbash: bool = False) -> str:
    p = _s(p).strip().replace("\\", "/")
    if p.startswith(("//?/", "//./")):
        p = p[4:]
        if p[:4].upper() == "UNC/":
            p = "//" + p[4:]
    if re.match(r"^/[A-Za-z]:(?:/|$)", p):          # markdown links in Codex messages: /D:/Crossview/...
        p = p[1:]
    elif gitbash and re.match(r"^/[A-Za-z](?:/|$)", p):   # Git Bash: /d/Crossview/... -> d:/Crossview/...
        p = p[1] + ":" + p[2:]
    lead = "//" if p.startswith("//") and not p.startswith("///") else ""
    return lead + re.sub(r"/{2,}", "/", p[len(lead):])


def _is_abs(n: str) -> bool:
    return n.startswith("/") or bool(_DRIVE.match(n))


def rel_path(path: str, root: str | None) -> str:
    """A path as people should see it: relative to the worktree root ("." for the root itself), unchanged
    when already relative (minus "./"), ".../<last two parts>" when outside the root. Windows paths (either
    slash, any case), Git Bash /f/... and markdown-link /F:/... forms are understood."""
    if not path:
        return ""
    r = _norm(root).rstrip("/") if root else ""
    win = bool(_DRIVE.match(r))
    n = _norm(path, gitbash=win)
    if r:
        ci = win or bool(_DRIVE.match(n))
        a, b = (n.lower(), r.lower()) if ci else (n, r)
        if a.rstrip("/") == b:
            return "."
        if a.startswith(b + "/"):
            return n[len(b) + 1:] or "."
    if not _is_abs(n):
        while n.startswith("./"):
            n = n[2:]
        return n or "."
    parts = [x for x in n.split("/") if x]
    return ".../" + "/".join(parts[-2:]) if r and len(parts) > 3 else n


@lru_cache(maxsize=64)
def _root_re(root: str) -> re.Pattern | None:
    r = _norm(root).rstrip("/")
    parts = [x for x in r.split("/") if x]
    if len(parts) < 2:
        return None                     # never strip "/" or a drive
    sep = r"[\\/]+"
    win = bool(_DRIVE.match(r))
    head = rf"(?:/?{re.escape(parts[0][0])}:|/{re.escape(parts[0][0])}(?=[\\/]))" if win else ""
    body = head + "".join(sep + re.escape(p) for p in (parts[1:] if win else parts))
    return re.compile(rf"(?<![\w.\\/-]){body}(?:(?P<sep>{sep})|(?![\w.-]))", re.I if win else 0)


def _unroot(text: str, root: str | None) -> str:
    """Drop the worktree prefix from absolute paths inside free text (commands, Codex message links)."""
    rx = _root_re(root) if root and text else None
    return rx.sub(lambda m: "" if m.group("sep") else ".", text) if rx else text


def _is_task_file(p: str) -> bool:
    parts = p.replace("\\", "/").split("/")
    return len(parts) >= 2 and parts[-2].lower() == ".task" and parts[-1].lower() in TASK_FILES


# --- commands ---------------------------------------------------------------------------------------

_SHELLS = {"powershell": "ps", "pwsh": "ps", "bash": "sh", "sh": "sh", "zsh": "sh", "dash": "sh", "cmd": "cmd"}
_WRAPPER = re.compile(r"""^\s*(?:"[^"]*[\\/]|'[^']*[\\/]|[^\s"']*[\\/])?(?:powershell|pwsh|bash|sh|zsh|dash|cmd)"""
                      r"""(?:\.exe)?["']?(?:\s+[-/]\S+)*?\s+(?:-Command|-c|-lc|/c)\s+""", re.I)
_CD = re.compile(r"""^\s*(?:cd|chdir|Set-Location|sl|pushd|Push-Location)\s+(?:/d\s+)?(?:-(?:Literal)?Path\s+)?"""
                 r"""(?:"([^"]*)"|'([^']*)'|([^\s;&|]+))\s*(?:&&|;|\n)\s*""", re.I)
_EXIT_ECHO = re.compile(r"""\s*(?:;|&&|&|\|\||\n)\s*(?:echo|Write-Output|Write-Host|printf)\s+["']?[^"'\n;]*?"""
                        r"""(?:\$\?|\$LASTEXITCODE|%ERRORLEVEL%)[^"'\n;]*["']?\s*;?\s*$""", re.I)
_NAMES = (r"(?P<n>python(?:w|3(?:\.\d+)?)?|py|node|npm|npx|pnpm|yarn|git|pytest|uvx?|pip3?|go|cargo|deno|bun"
          r"|dotnet|java)")
_EXT, _END = r"(?:\.exe|\.cmd|\.bat)?", r"(?=[\s;|&)]|$)"
_EXES = (
    re.compile(rf"""(?:&\s*)?(["'])[^"'\r\n]*[\\/]{_NAMES}{_EXT}\1{_END}""", re.I),   # "C:\...\python.exe"
    re.compile(rf"""&\s*(["']){_NAMES}{_EXT}\1{_END}""", re.I),                       # & 'python.exe'
    re.compile(rf"""(?:&\s*)?(?<![^\s;|&(])[^\s"';|&()]*[\\/]{_NAMES}{_EXT}{_END}""", re.I),  # C:\...\python.exe
    re.compile(rf"""(?<![^\s;|&(]){_NAMES}(?:\.exe|\.cmd|\.bat){_END}""", re.I),       # python.exe
)


def _unwrap(cmd: str) -> str:
    """The script inside a `powershell -Command ...`, `bash -lc ...` or `cmd /c ...` wrapper (else cmd)."""
    try:
        argv = shlex.split(cmd, posix=True)
    except ValueError:
        m = _WRAPPER.match(cmd)
        if not m:
            return cmd
        rest = cmd[m.end():].strip()
        if rest[:1] in ("'", '"'):
            rest = rest[1:-1] if len(rest) > 1 and rest.endswith(rest[0]) else rest[1:]
        return rest
    if len(argv) < 3:
        return cmd
    exe = re.split(r"[\\/]", argv[0])[-1].lower()
    shell = _SHELLS.get(exe.removesuffix(".exe"))
    if not shell:
        return cmd
    for i in range(1, len(argv) - 1):
        a = argv[i].lower()
        if (shell == "sh" and re.fullmatch(r"-[a-z]*c[a-z]*", a) or shell == "ps" and a in ("-command", "-c")
                or shell == "cmd" and a in ("/c", "/k", "/r")):
            return argv[i + 1] if shell == "sh" else " ".join(argv[i + 1:])
        if shell != "ps" and not a.startswith(("-", "/")):
            break
    return cmd


def _bare_exes(s: str) -> str:
    for rx in _EXES:
        s = rx.sub(lambda m: m.group("n").lower(), s)
    return s


def _script(cmd: Any, root: str | None) -> str:
    """Steps 1-4 of the display rules: unwrap the shell, drop `cd <root> &&` and the exit-code echo,
    bare interpreter names, worktree prefixes made relative. Only the first MAX_SCRIPT characters are
    looked at (a heredoc can carry a whole file; the cut leaves unbalanced quotes: regex unwrapping)."""
    s = _unwrap(_s(cmd if not isinstance(cmd, str) else cmd[:MAX_SCRIPT])).strip()
    m = _CD.match(s)
    if m:
        where = next(g for g in m.groups() if g is not None)
        if (rel_path(where, root) == ".") if root else _is_abs(_norm(where)):
            s = s[m.end():]
    end = s[-300:]                        # the exit-code echo is at the very end: never scan the whole script
    s = s[: len(s) - len(end)] + _EXIT_ECHO.sub("", end)
    return _unroot(_bare_exes(s.rstrip(" \t\r\n\f\v;")), root)


def _oneline(s: str, limit: int) -> str:
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if not lines:
        return ""
    first = " ".join(lines[0].split())
    return first[: limit - 2].rstrip() + " …" if len(lines) > 1 else _clip(first, limit)


def simplify_command(cmd: str, root: str | None = None) -> str:
    """A command as people should see it: `python -m unittest test_x`, not the PowerShell wrapper around it."""
    return _oneline(_script(cmd, root), MAX_TARGET)


# Codex runs everything through the shell; read/search/list are recognised from the script.
_WORD = re.compile(r"""(?:"[^"]*"|'[^']*'|[^\s"'])+""")
_READERS = {"cat", "type", "gc", "get-content", "head", "tail", "nl", "bat", "more", "less"}
_SEARCHERS = {"rg", "grep", "egrep", "fgrep", "findstr", "select-string", "sls", "ag", "ack"}
_LISTERS = {"ls", "dir", "get-childitem", "gci", "tree", "find", "fd", "ll"}
_NEUTRAL = {"cd", "chdir", "set-location", "sl", "pushd", "popd", "echo", "write-output", "write-host", "printf",
            "pwd", "get-location"}
_FILTERS = {"head", "tail", "sort", "uniq", "wc", "cut", "nl", "less", "more", "cat", "sed", "grep", "egrep", "rg",
            "findstr", "select-string", "sls", "select-object", "select", "sort-object", "measure-object", "measure",
            "format-list", "fl", "format-table", "ft", "out-string", "out-host", "where-object", "where", "tr",
            "column", "jq"}
_HEAD_OPTS = frozenset({"-n", "-c", "--lines", "--bytes"})
_VALUE_OPTS = {"head": _HEAD_OPTS, "tail": _HEAD_OPTS, "sed": frozenset({"-e", "--expression", "-f", "--file"}),
               **dict.fromkeys(("get-content", "gc", "type"), frozenset({
                   "-totalcount", "-tail", "-head", "-first", "-last", "-encoding", "-delimiter", "-readcount"}))}
_HARMLESS_REDIRECT = re.compile(r"\d?>\s*(?:&\d|/dev/null|\$null|nul\b)", re.I)


def _pieces(script: str) -> list[list[str]]:
    """Commands of a script, each as its pipeline stages (split on ; && || | and newlines outside quotes)."""
    out: list[list[str]] = []
    stages: list[str] = []
    cur: list[str] = []
    q, i = "", 0
    while i < len(script):
        c = script[i]
        if q:
            q = "" if c == q else q
        elif c in "\"'":
            q = c
        elif c in ";\n" or script.startswith(("&&", "||"), i):
            out.append(stages + ["".join(cur)])
            stages, cur = [], []
            i += 1 if c in ";\n" else 2
            continue
        elif c == "|":
            stages.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    out.append(stages + ["".join(cur)])
    return [[st.strip() for st in p] for p in out if "".join(p).strip()]


def _words(seg: str) -> list[str]:
    return [re.sub(r"""["']""", "", w) for w in _WORD.findall(seg)]


def _verb(words: list[str]) -> str:
    v = re.split(r"[\\/]", words[0].lower())[-1] if words else ""
    return v.removesuffix(".exe")


def _piece_kind(stages: list[str]) -> str:
    words = _words(stages[0])
    v, low = _verb(words), [w.lower() for w in words]
    if not v:
        return "neutral"
    bare = re.sub(r'"[^"]*"|\'[^\']*\'', "", " | ".join(stages))
    if ">" in _HARMLESS_REDIRECT.sub("", bare):
        return "run"                    # writes a file
    if any(_verb(_words(st)) not in _FILTERS for st in stages[1:]):
        return "run"
    if v in _NEUTRAL:
        return "neutral"
    if v == "git":
        return {"grep": "search", "ls-files": "list"}.get(low[1] if len(low) > 1 else "", "run")
    if v == "sed":
        return "read" if "-n" in low and not any(w.startswith(("-i", "--in-place")) for w in low) else "run"
    if v == "rg" and "--files" in low:
        return "list"
    if v == "find" and any(w in ("-exec", "-execdir", "-delete", "-ok") for w in low):
        return "run"
    return "read" if v in _READERS else "search" if v in _SEARCHERS else "list" if v in _LISTERS else "run"


def _shell_kind(script: str) -> str:
    """read | search | list when every command of the script does that, else run."""
    kinds = {k for k in map(_piece_kind, _pieces(script)) if k != "neutral"}
    return kinds.pop() if len(kinds) == 1 else "run"


def _read_files(script: str, root: str | None) -> list[str]:
    files: list[str] = []
    for stages in _pieces(script):
        if _piece_kind(stages) != "read":
            continue
        words = _words(stages[0])
        verb = _verb(words)
        opts, skip, sed_script = _VALUE_OPTS.get(verb, ()), False, verb == "sed"
        for w in words[1:]:
            lw = w.lower()
            if skip:
                skip = False
            elif lw in opts:
                skip, sed_script = True, False   # sed -e SCRIPT: every operand is a file
            elif lw.startswith("-") or ">" in w or re.fullmatch(r"[+-]?\d+|\$null", lw):
                continue
            elif sed_script:
                sed_script = False               # otherwise sed's first operand is its script
            else:
                p = rel_path(w, root)
                if p not in files:
                    files.append(p)
    return files


# --- tests in a log ---------------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Line starts match [ \t]*, never \s*: with re.M a \s* there runs across every following blank or separator
# line from each line start (minutes on a log with thousands of "=====" lines).
_UNITTEST = re.compile(r"^Ran (\d+) tests? in [^\n]*\n\s*(OK|FAILED|NO TESTS RAN)\b(?: \(([^)\n]*)\))?", re.M)
_PYTEST = re.compile(r"^[ \t=\-]*((?:\d+ [a-z]+(?: [a-z]+)?,? ?)+)[ \t]*in [\d.]+ ?s(?:econds)?\b", re.M)
_PYTEST_NONE = re.compile(r"^[ \t=]*no tests ran in [\d.]+s", re.M)
_NODE = re.compile(r"^(?:#|ℹ)[ \t]*(tests|pass|fail|cancelled|skipped|todo)[ \t]+(\d+)[ \t]*$", re.M)
_JEST = re.compile(r"^[ \t]*Tests:?[ \t]+([^\n]*\d[^\n]*)$", re.M)
_GO_TEST = re.compile(r"^--- (PASS|FAIL|SKIP): ", re.M)
_GO_PKG = re.compile(r"^(ok|FAIL)[ \t]+\S+[ \t]+(?:[\d.]+s|\(cached\)|\[)", re.M)


def test_counts(log: str) -> dict | None:
    """{"passed", "failed", "total"} from test runner summaries (unittest, pytest, node --test, jest/vitest,
    go test), summed over every command in the log; None when no summary is found. go test counts tests
    with -v (=== RUN lines), else packages."""
    if not log:
        return None
    text = _ANSI.sub("", _s(log)).replace("\r\n", "\n")
    passed = failed = total = 0
    found = False
    for m in _UNITTEST.finditer(text):
        found = True
        n = int(m.group(1))
        info = {k.strip(): int(v) for k, v in re.findall(r"([a-z ]+)=(\d+)", m.group(3) or "")}
        bad = info.get("failures", 0) + info.get("errors", 0) + info.get("unexpected successes", 0)
        passed += max(0, n - bad - info.get("skipped", 0))
        failed += bad
        total += n
    for m in _PYTEST.finditer(text):
        c: dict[str, int] = {}
        for v, k in re.findall(r"(\d+) ([a-z]+(?: [a-z]+)?)", m.group(1)):
            k = k.rstrip("s") if k in ("errors", "warnings") else k
            c[k] = c.get(k, 0) + int(v)       # "2 subtests passed" (pytest 9) is its own key: not counted
        if not {"passed", "failed", "error", "skipped", "xfailed", "xpassed", "deselected"} & c.keys():
            continue
        found = True
        p, f = c.get("passed", 0) + c.get("xpassed", 0), c.get("failed", 0) + c.get("error", 0)
        passed, failed, total = passed + p, failed + f, total + p + f + c.get("skipped", 0) + c.get("xfailed", 0)
    found = found or bool(_PYTEST_NONE.search(text))
    node: dict[str, int] = {}
    for k, v in _NODE.findall(text):
        node[k] = node.get(k, 0) + int(v)
    if {"pass", "fail"} & node.keys():
        found = True
        p, f = node.get("pass", 0), node.get("fail", 0) + node.get("cancelled", 0)
        passed, failed = passed + p, failed + f
        total += node.get("tests", p + f + node.get("skipped", 0) + node.get("todo", 0))
    for m in _JEST.finditer(text):
        c = {k: int(v) for v, k in re.findall(r"(\d+) (failed|passed|skipped|todo|total)", m.group(1))}
        if not {"passed", "failed"} & c.keys():
            continue
        found = True
        paren = re.search(r"\((\d+)\)", m.group(1))
        passed, failed = passed + c.get("passed", 0), failed + c.get("failed", 0)
        total += c.get("total", int(paren.group(1)) if paren else
                       c.get("passed", 0) + c.get("failed", 0) + c.get("skipped", 0) + c.get("todo", 0))
    # without -v a failing test still prints its "--- FAIL:" line: counting those would hide the passing packages
    go = _GO_TEST.findall(text) if re.search(r"^=== RUN ", text, re.M) else _GO_PKG.findall(text)
    if go:
        found = True
        p = sum(1 for x in go if x in ("PASS", "ok"))
        f = sum(1 for x in go if x == "FAIL")
        passed, failed, total = passed + p, failed + f, total + len(go)
    return {"passed": passed, "failed": failed, "total": total} if found else None


# --- the feed ---------------------------------------------------------------------------------------

_EXIT_CODE = re.compile(r"^\s*Exit code (-?\d+)")
_DENIED = re.compile(r"^\s*(?:Permission to use \S+ has been denied|Claude requested permissions? to use)", re.I)
_TOOL_ERROR_TAG = re.compile(r"</?tool_use_error>")
_RECONNECT = re.compile(r"^\s*(Re-?connecting\W*(?:\d+/\d+)?)", re.I)
_GEMINI_KINDS = {"read_file": "read", "read_many_files": "read", "write_file": "write", "replace": "edit",
                 "edit": "edit", "run_shell_command": "run", "glob": "search", "grep_search": "search",
                 "search_file_content": "search", "grep": "search", "list_directory": "list", "ls": "list",
                 "google_web_search": "web", "web_fetch": "web", "write_todos": "todo", "invoke_agent": "agent"}
_CLAUDE_RESULT_KEYS = ("subtype", "is_error", "num_turns", "total_cost_usd", "result")


def _new(rid: str, seq: int, t: float | None, kind: str) -> dict:
    return {"id": rid, "seq": seq, "t": t, "kind": kind, "target": "", "detail": "", "ok": None,
            "exit_code": None, "added": None, "removed": None, "tool": "", "sub": False, "task_file": False,
            "output": ""}


def _line_time(ev: dict) -> float | None:
    ts = ev.get("timestamp")
    t = _num(ts)
    if t is not None:
        return t / 1000 if t > 1e11 else t   # opencode: epoch ms
    if isinstance(ts, str) and ts:
        try:
            return _num(datetime.fromisoformat(ts).timestamp())   # Gemini: ISO
        except (ValueError, OverflowError, OSError):
            return None
    return None


_ODD_LINE = (AttributeError, TypeError, ValueError, KeyError, IndexError, ArithmeticError, RecursionError)


class Feed:
    """Incremental reader of one run's events file. update() reads the bytes added since the last call,
    CHUNK at a time; a half-written last line waits for the next call; a file that shrank or was replaced
    is read again from the start. When update() is done it publishes items, ended, usage and final_text
    together as one snapshot, so other threads can read them at any time (snapshot() gives all four from
    the same update). Published rows are never changed in place."""

    def __init__(self, events_path: str | os.PathLike, root: str | None = None, outputs: bool = False):
        self.path = Path(events_path)
        self.times_path = self.path.with_suffix(".times")   # one time.time() per stdout line (orch.proc)
        self.root = str(root) if root else None
        self.outputs = outputs
        self._lock = threading.Lock()
        self._snap: tuple[list[dict], bool, dict | None, str] = ([], False, None, "")
        self._reset()

    @property
    def items(self) -> list[dict]:
        """Rows in seq order (one per id)."""
        return self._snap[0]

    @property
    def ended(self) -> bool:
        """A final result / turn.completed / turn.failed / step_finish(stop) was seen."""
        return self._snap[1]

    @property
    def usage(self) -> dict | None:
        """{"tokens_in", "tokens_out", "tokens_cached", "cost_usd"} when known (live counts until the end)."""
        return self._snap[2]

    @property
    def final_text(self) -> str:
        """The last assistant text or result text (<= MAX_FINAL characters)."""
        return self._snap[3]

    def snapshot(self) -> tuple[list[dict], bool, dict | None, str]:
        """(items, ended, usage, final_text), all from the same update."""
        return self._snap

    def _reset(self) -> None:
        self._rows: dict[str, dict] = {}
        self._pub: dict[str, dict] = {}       # the published copy of each row
        self._changed: dict[str, None] = {}   # rows changed since the last publish (ordered: new rows in order)
        self._ended, self._use, self._final = False, None, ""
        self._stat: tuple | None = None
        self._off, self._n, self._head = 0, 0, b""
        self._buf = bytearray()               # the last line, still without its newline
        self._skip = False                    # inside a line longer than MAX_LINE
        self._tried = -1                      # len(_buf) when it was last tried as a complete line
        self._early = -1                      # len(_buf) when it was read before its newline arrived
        self._times: list[float | None] = []
        self._t_stat: tuple | None = None
        self._t_off, self._t_buf = 0, b""
        self._untimed: list[str] = []
        self._seq, self._t, self._cwd = 0, None, ""
        self._last = ""                       # id of the newest row
        self._streak: tuple[str, str] | None = None   # (key, row id) of a run of collapsed retries
        self._limit: tuple[str, str] | None = None    # (key, row id) of the rate limit warning shown last
        self._say: tuple[str, str] | None = None      # Gemini: (row id, text so far) of the streamed message
        self._msgs: dict[str, tuple[int, int, int]] = {}   # Claude: usage per assistant message ...
        self._tot = (0, 0, 0)                               # ... and their sum
        self._final_usage = False
        self._dirty = True

    # -- reading --

    def update(self) -> bool:
        """Read new complete lines. True when items, usage, final_text or ended changed."""
        with self._lock:
            self._dirty = False
            try:
                st = os.stat(self.path)
            except OSError:
                if self._stat or self._rows:
                    self._reset()
                return self._publish()
            key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            if self._stat and (st.st_size < self._off or (st.st_ino and st.st_ino != self._stat[1])):
                self._reset()                  # truncated or replaced: start again
            self._read_times()
            if key != self._stat and self._read_events():
                self._stat = key               # (a failed read is tried again next time)
            if self._untimed and self._times:
                self._backfill()
            return self._publish()

    def _publish(self) -> bool:
        if self._dirty:
            for rid in self._changed:
                self._pub[rid] = dict(self._rows[rid])
            self._changed.clear()
            self._snap = (list(self._pub.values()), self._ended, self._use, self._final)
        return self._dirty

    def _read_events(self) -> bool:
        try:
            with open(self.path, "rb") as f:
                if self._head and f.read(len(self._head)) != self._head:
                    self._reset()              # rewritten in place
                    self._read_times()
                if len(self._head) < 256:
                    f.seek(0)
                    self._head = f.read(256)
                f.seek(self._off)
                while data := f.read(CHUNK):   # never the whole file in memory at once
                    self._off += len(data)
                    self._lines(data)
        except OSError:
            return False
        self._last_line()
        return True

    def _lines(self, data: bytes) -> None:
        """The complete lines of a chunk; what follows the last newline waits in _buf."""
        parts = data.split(b"\n")
        rest = parts.pop()
        for raw in parts:
            self._n += 1
            early, self._early, self._tried = self._early, -1, -1
            if self._skip:
                self._skip = False             # the end of a line longer than MAX_LINE: not read
                continue
            if self._buf:
                self._buf += raw
                raw, self._buf = self._buf, bytearray()
            if early >= 0 and not raw[early:].strip():
                continue                       # already read before its newline arrived
            self._line(raw, self._n)
        if not self._skip:
            self._buf += rest
            if len(self._buf) > MAX_LINE:
                self._buf, self._skip, self._early, self._tried = bytearray(), True, -1, -1

    def _last_line(self) -> None:
        """A last line that is already a whole JSON object is complete (no prefix of an object is one):
        read it now, a killed process may never write its newline."""
        b = self._buf
        if self._skip or not b or len(b) == self._tried:
            return
        self._tried = len(b)
        if b[:64].lstrip()[:1] == b"{" and b[-64:].rstrip()[-1:] == b"}" and self._line(b, self._n + 1):
            self._early = len(b)

    def _read_times(self) -> None:
        try:
            st = os.stat(self.times_path)
        except OSError:
            return
        key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        if key == self._t_stat:
            return
        if self._t_stat and (st.st_size < self._t_off or (st.st_ino and st.st_ino != self._t_stat[1])):
            self._times, self._t_off, self._t_buf = [], 0, b""
        try:
            with open(self.times_path, "rb") as f:
                f.seek(self._t_off)
                data = f.read()
        except OSError:
            return
        self._t_stat = key
        self._t_off += len(data)
        lines = (self._t_buf + data).split(b"\n")
        self._t_buf = lines.pop()[-64:]       # a garbage sidecar without newlines stays small
        for ln in lines:
            try:
                v: float | None = float(ln)
            except ValueError:
                v = None
            self._times.append(v if v is not None and math.isfinite(v) else None)

    def _backfill(self) -> None:
        """Rows read before the sidecar caught up get their time now (rows whose sidecar line is not a
        number are dropped from the list: they never get one)."""
        keep = []
        for rid in self._untimed:
            row = self._rows.get(rid)
            if row is None or row["t"] is not None:
                continue
            i = row["seq"] - 1
            if i >= len(self._times):
                keep.append(rid)
            elif self._times[i] is not None:
                row["t"] = self._times[i]
                self._changed[rid] = None
                self._dirty = True
        self._untimed = keep

    def _line(self, raw: bytes | bytearray, seq: int) -> bool:
        """Read one line; True when it was a JSON object (whatever it said)."""
        text = raw.decode("utf-8", errors="replace").strip().lstrip("\ufeff\x00").lstrip()
        if not text.startswith("{"):
            return False
        try:
            ev = json.loads(text)
        except (ValueError, RecursionError):  # RecursionError: absurdly nested JSON
            return False
        if not isinstance(ev, dict):
            return False
        self._seq = seq
        i = seq - 1
        self._t = self._times[i] if i < len(self._times) and self._times[i] is not None else _line_time(ev)
        try:
            self._event(ev)
        except _ODD_LINE:
            pass                               # an odd line never breaks the feed
        return True

    # -- rows --

    def _root(self) -> str | None:
        return self.root or self._cwd or None

    def _path(self, p: Any) -> str:
        return _clip_left(rel_path(_s(p), self._root())) if p else ""

    def _row(self, rid: str, kind: str = "", **kw: Any) -> dict:
        rid = _s(rid) or f"L{self._seq}"
        row = self._rows.get(rid)
        if row is None:
            row = self._rows[rid] = _new(rid, self._seq, self._t, kind or "tool")
            if self._t is None:
                self._untimed.append(rid)
            self._last = rid
        elif kind:
            row["kind"] = kind
        if not self.outputs:
            kw.pop("output", None)
        if "tool" in kw:
            kw["tool"] = kw["tool"][:MAX_TARGET]
        row.update(kw)
        if "task_file" not in kw and ("target" in kw or kind):
            row["task_file"] = row["kind"] in _PATH_KINDS and _is_task_file(row["target"])
        self._changed[rid] = None
        self._dirty = True
        return row

    def _streak_id(self, key: str) -> str:
        """Consecutive retries (Codex reconnects, Claude API retries) share one row."""
        if self._streak and self._streak[0] == key and self._streak[1] == self._last:
            return self._streak[1]
        return f"L{self._seq}"

    def _set(self, name: str, value: Any) -> None:
        if getattr(self, name) != value:
            setattr(self, name, value)
            self._dirty = True

    def _text(self, text: Any) -> str:
        """Assistant text as shown: worktree prefixes dropped (Codex links), cut before the work."""
        return _unroot(_s(text[: 4 * MAX_FINAL] if isinstance(text, str) else text), self._root()).strip()

    def _say_row(self, rid: str, text: str, sub: bool = False) -> None:
        text = self._text(text)
        self._row(rid, "say", detail=_clip(text, MAX_DETAIL, flat=False), ok=True, sub=sub)
        if not sub:
            self._set("_final", _clip(text, MAX_FINAL, flat=False))

    def _usage(self, tokens_in: int | None, tokens_out: int | None, cached: int | None, cost: float | None,
               add: bool = False) -> None:
        new = {"tokens_in": tokens_in, "tokens_out": tokens_out, "tokens_cached": cached, "cost_usd": cost}
        if all(v is None for v in new.values()):
            return
        if add and self._use:
            new = {k: v if self._use[k] is None else self._use[k] + (v or 0) for k, v in new.items()}
        self._set("_use", new)

    def _end(self) -> None:
        self._set("_ended", True)

    # -- dialects --

    def _event(self, ev: dict) -> None:
        t = ev.get("type")
        if not isinstance(t, str):
            return
        if t != "message":
            self._say = None
        if t in ("system", "assistant", "user", "rate_limit_event") or \
                t == "result" and any(k in ev for k in _CLAUDE_RESULT_KEYS):
            self._claude(t, ev)
        elif t in ("thread.started", "turn.started", "turn.completed", "turn.failed") or t.startswith("item."):
            self._codex(t, ev)
        elif t in ("tool_use", "text", "reasoning", "step_start", "step_finish", "error") and \
                ("part" in ev or "sessionID" in ev):
            self._opencode(t, ev)
        elif t in ("init", "message", "tool_result") or t == "tool_use" and "tool_name" in ev or \
                t == "result" and ("stats" in ev or "status" in ev) or t == "error" and "severity" in ev:
            self._gemini(t, ev)
        elif t == "request" or t == "result" and "model" in ev or t == "error" and "kind" in ev:
            self._api(t, ev)
        elif t == "result" and "text" in ev or t == "error" and "error" in ev:
            self._fake(t, ev)
        elif t == "error":
            self._codex(t, ev)

    # Claude Code: stream-json
    def _claude(self, t: str, ev: dict) -> None:
        sub, lid = bool(ev.get("parent_tool_use_id")), f"L{self._seq}"
        if t == "system":
            if ev.get("subtype") == "init":
                if isinstance(ev.get("cwd"), str) and not self._cwd:
                    self._cwd = ev["cwd"]
                self._row(lid, "start", target=_clip(ev.get("model"), MAX_TARGET),
                          detail=_clip(ev.get("session_id"), MAX_DETAIL), ok=True)
            elif ev.get("subtype") == "api_retry":
                label = f"API retry {_s(ev.get('attempt'))}/{_s(ev.get('max_retries'))}"
                if ev.get("error_status"):
                    label += f" (HTTP {_s(ev.get('error_status'))})"
                rid = self._streak_id("api_retry")
                self._row(rid, "error", target=_clip(label, MAX_TARGET), detail=_clip(ev.get("error"), MAX_DETAIL),
                          ok=True, sub=sub)
                self._streak = ("api_retry", rid)
        elif t == "assistant":
            msg = _d(ev.get("message"))
            self._claude_usage(msg)
            err = ev.get("error") if isinstance(ev.get("error"), str) else ""
            for i, b in enumerate(x for x in _l(msg.get("content")) if isinstance(x, dict)):
                rid = lid if i == 0 else f"{lid}.{i}"
                if b.get("type") == "text" and _s(b.get("text")).strip():
                    if err:
                        self._row(rid, "limit" if err in ("rate_limit", "billing_error") else "error",
                                  target=_clip(err, MAX_TARGET),
                                  detail=_clip(b["text"], MAX_DETAIL, flat=False), ok=False, sub=sub)
                    else:
                        self._say_row(rid, b["text"], sub)
                elif b.get("type") == "thinking" and _s(b.get("thinking")).strip():
                    self._row(rid, "think", detail=_clip(b["thinking"], MAX_DETAIL, flat=False), ok=True, sub=sub)
                elif b.get("type") == "tool_use":
                    self._claude_tool(b, rid, sub)
        elif t == "user":
            results = [b for b in _l(_d(ev.get("message")).get("content"))
                       if isinstance(b, dict) and b.get("type") == "tool_result"]
            extra = ev.get("tool_use_result", ev.get("toolUseResult"))
            for b in results:
                self._claude_result(b, _d(extra) if len(results) == 1 else {})
        elif t == "rate_limit_event":
            info = _d(ev.get("rate_limit_info"))
            status = _s(info.get("status"))
            if status == "allowed":
                self._limit = None
            elif status:                       # allowed_warning / rejected: one row until the status changes
                key = f"{_s(info.get('rateLimitType'))}:{status}"
                rid = self._limit[1] if self._limit and self._limit[0] == key else lid
                self._row(rid, "limit", target=_clip(info.get("rateLimitType"), MAX_TARGET),
                          detail=_clip(status, MAX_DETAIL),
                          ok=status != "rejected")
                self._limit = (key, rid)
        elif t == "result":
            subtype = _s(ev.get("subtype"))
            text = self._text(ev.get("result")) if isinstance(ev.get("result"), str) else ""
            errors = "; ".join(_s(e) for e in _l(ev.get("errors")))
            self._row(lid, "done", target=_clip(subtype, MAX_TARGET),
                      detail=_clip(text or errors, MAX_DETAIL, flat=False),
                      ok=not ev.get("is_error") and not subtype.startswith("error"))
            if text:
                self._set("_final", _clip(text, MAX_FINAL, flat=False))
            u = _d(ev.get("usage"))
            tin = sum(_int(u.get(k))
                      for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            if u or _num(ev.get("total_cost_usd")) is not None:
                self._usage(tin or None, _int(u.get("output_tokens")) or None,
                            _int(u.get("cache_read_input_tokens")) if tin else None, _num(ev.get("total_cost_usd")))
                self._final_usage = True
            self._end()

    def _claude_usage(self, msg: dict) -> None:
        u, mid = _d(msg.get("usage")), msg.get("id")
        if not u or not isinstance(mid, str) or self._final_usage:
            return
        cached = _int(u.get("cache_read_input_tokens"))
        new = (_int(u.get("input_tokens")) + _int(u.get("cache_creation_input_tokens")) + cached,
               _int(u.get("output_tokens")), cached)
        old = self._msgs.get(mid, (0, 0, 0))   # a message repeats its usage on each of its lines: count it once
        self._msgs[mid] = new
        self._tot = tin, tout, cached = tuple(t - o + n for t, o, n in zip(self._tot, old, new))
        self._usage(tin or None, tout or None, cached if tin else None, None)

    def _claude_tool(self, b: dict, lid: str, sub: bool) -> None:
        name, inp = _s(b.get("name")), _d(b.get("input"))
        rid = _s(b.get("id")) or lid
        fp = inp.get("file_path") or inp.get("notebook_path") or ""
        kw: dict[str, Any] = {"tool": name, "sub": sub}
        if name == "Read":
            self._row(rid, "read", target=self._path(fp), **kw)
        elif name in ("Edit", "MultiEdit", "NotebookEdit"):
            add = rem = 0
            for e in (_l(inp.get("edits")) if name == "MultiEdit" else [inp]):
                a, r = _diff_counts(_s(_d(e).get("old_string")), _s(_d(e).get("new_string") or _d(e).get("new_source")))
                add, rem = add + a, rem + r
            self._row(rid, "edit", target=self._path(fp), added=add, removed=rem, **kw)
        elif name == "Write":
            self._row(rid, "write", target=self._path(fp), added=_nlines(inp.get("content")), **kw)
        elif name in ("Bash", "PowerShell"):
            self._row(rid, "run", target=simplify_command(_s(inp.get("command")), self._root()),
                      detail=_clip(inp.get("description"), MAX_DETAIL), **kw)
        elif name in ("Grep", "Glob"):
            where = " ".join(x for x in (self._path(inp.get("path")), _s(inp.get("glob"))) if x not in ("", "."))
            self._row(rid, "search", target=_clip(inp.get("pattern"), MAX_TARGET), detail=_clip(where, MAX_DETAIL),
                      **kw)
        elif name == "LS":
            self._row(rid, "list", target=self._path(inp.get("path")) or ".", **kw)
        elif name == "TodoWrite":
            target, detail = _todo(inp.get("todos"))
            self._row(rid, "todo", target=target, detail=detail, **kw)
        elif name in ("WebFetch", "WebSearch"):
            self._row(rid, "web", target=_clip(inp.get("url") or inp.get("query"), MAX_TARGET), **kw)
        elif name in ("Task", "Agent"):
            self._row(rid, "agent", target=_clip(inp.get("description") or inp.get("prompt"), MAX_TARGET),
                      detail=_clip(inp.get("subagent_type"), MAX_DETAIL), **kw)
        else:
            target = ".".join(name.split("__")[1:]) if name.startswith("mcp__") else name
            self._row(rid, "tool", target=_clip(target, MAX_TARGET), detail=_args(inp), **kw)

    def _claude_result(self, b: dict, extra: dict) -> None:
        row = self._rows.get(_s(b.get("tool_use_id")))
        if row is None:
            return
        text = _TOOL_ERROR_TAG.sub("", _content_text(b.get("content"))).strip()
        err, kind = bool(b.get("is_error")), row["kind"]
        kw: dict[str, Any] = {"ok": not err}
        if err and _DENIED.match(text):
            kw.update(detail="denied", output=_tail(text), added=None, removed=None)
        elif kind == "run":
            m = _EXIT_CODE.match(text) if err else None
            if m:
                kw.update(exit_code=int(m.group(1)), output=_tail(text[m.end():].lstrip("\n")))
            elif err:
                kw.update(detail=_clip(text, MAX_DETAIL), output=_tail(text))
            else:
                plain = not (extra.get("interrupted") or "returnCodeInterpretation" in extra
                             or "backgroundTaskId" in extra)       # no exit code to report
                out = "\n".join(_s(extra.get(k)).rstrip("\n") for k in ("stdout", "stderr") if extra.get(k))
                kw.update(exit_code=0 if plain else None, ok=not extra.get("interrupted"),
                          output=_tail(out if extra else text))
        elif err:
            kw.update(detail=_clip(text, MAX_DETAIL), output=_tail(text))
            if kind in ("edit", "write"):
                kw.update(added=None, removed=None)
        elif kind in ("edit", "write"):
            patch = extra.get("structuredPatch")
            if extra.get("type") == "create":
                kw["detail"] = "created"
                if isinstance(extra.get("content"), str):
                    kw["added"], kw["removed"] = _nlines(extra["content"]), 0
            elif isinstance(patch, list):
                kw["added"], kw["removed"] = _patch_counts(patch)
        elif kind != "read":                   # never keep what a Read returned
            kw["output"] = _tail(text)
        self._row(row["id"], **kw)

    # Codex: exec --json
    def _codex(self, t: str, ev: dict) -> None:
        lid = f"L{self._seq}"
        if t == "thread.started":
            self._row(lid, "start", detail=_clip(ev.get("thread_id"), MAX_DETAIL), ok=True)
        elif t == "turn.completed":
            u = _d(ev.get("usage"))
            tin = _int(u.get("input_tokens"))
            self._usage(tin or None, _int(u.get("output_tokens")) or None,
                        _int(u.get("cached_input_tokens")) if tin else None, None, add=True)
            self._row(lid, "done", ok=True)
            self._end()
        elif t == "turn.failed":
            err = ev.get("error")
            msg = _s(_d(err).get("message")) if isinstance(err, dict) else _s(err)
            self._row(lid, "done", detail=_clip(msg, MAX_DETAIL), ok=False)
            self._end()
        elif t == "error":
            msg = _s(ev.get("message") or ev.get("error"))
            m = _RECONNECT.match(msg)
            if m:
                rid = self._streak_id("reconnect")
                self._row(rid, "error", target=_clip(m.group(1), MAX_TARGET), detail=_clip(msg, MAX_DETAIL), ok=True)
                self._streak = ("reconnect", rid)
            else:
                self._row(lid, _err_kind(msg), detail=_clip(msg, MAX_DETAIL), ok=False)
        elif t.startswith("item."):
            self._codex_item(t == "item.completed", _d(ev.get("item")))

    def _codex_item(self, done: bool, it: dict) -> None:
        ty, status = _s(it.get("type")), _s(it.get("status"))
        iid = _s(it.get("id")) or f"L{self._seq}"
        finished = done or status in ("completed", "failed", "declined")
        root = self._root()
        if ty == "command_execution":
            if iid not in self._rows:
                cmd = it.get("command")
                script = _script(cmd, root)
                kind = "run" if isinstance(cmd, str) and len(cmd) > MAX_SCRIPT else _shell_kind(script)
                kw: dict[str, Any] = {"target": _oneline(script, MAX_TARGET)}
                if kind == "read":
                    files = _read_files(script, root)
                    if files:
                        kw.update(target=_clip(", ".join(files), MAX_TARGET), detail=_oneline(script, MAX_DETAIL),
                                  task_file=all(map(_is_task_file, files)))
                self._row(iid, kind, tool=ty, **kw)
            read = self._rows[iid]["kind"] == "read"   # what a read printed is the file: never kept
            if finished:
                code = _intn(it.get("exit_code"))
                ok = status in ("completed", "") and code in (0, None)
                kw = {"exit_code": code, "ok": ok}
                if not read or not ok:
                    kw["output"] = _tail(it.get("aggregated_output"))
                if status == "declined":
                    kw["detail"] = "declined"
                self._row(iid, **kw)
            elif it.get("aggregated_output") and not read:
                self._row(iid, output=_tail(it.get("aggregated_output")))
        elif ty == "file_change":
            ok = status == "completed" if finished and status else (True if done else None)
            for i, c in enumerate(x for x in _l(it.get("changes")) if isinstance(x, dict)):
                k = c.get("kind")
                k = _s(_d(k).get("type")) if isinstance(k, dict) else _s(k)
                self._row(f"{iid}.{i}", {"add": "write", "delete": "delete"}.get(k, "edit"),
                          target=self._path(c.get("path")), detail="created" if k == "add" else "", ok=ok, tool=ty)
        elif ty in ("agent_message", "assistant_message"):
            if done and _s(it.get("text")).strip():
                self._say_row(iid, it["text"])
        elif ty == "reasoning":
            if done and _s(it.get("text")).strip():
                self._row(iid, "think", detail=_clip(it["text"], MAX_DETAIL, flat=False), ok=True)
        elif ty == "mcp_tool_call":
            kw = {"target": _clip(".".join(x for x in (_s(it.get("server")), _s(it.get("tool"))) if x), MAX_TARGET),
                  "tool": ty}
            if finished:
                e = it.get("error")
                msg = _s(_d(e).get("message")) if isinstance(e, dict) else _s(e)
                kw.update(ok=status in ("completed", "") and not msg,
                          output=_tail(msg or _content_text(_d(it.get("result")).get("content"))))
                if msg:
                    kw["detail"] = _clip(msg, MAX_DETAIL)
            self._row(iid, "tool", **kw)
        elif ty == "collab_tool_call":
            self._row(iid, "agent", target=_clip(it.get("prompt") or it.get("tool"), MAX_TARGET), tool=ty,
                      **({"ok": status in ("completed", "")} if finished else {}))
        elif ty == "web_search":
            q = it.get("query") or _d(it.get("action")).get("query") or _d(it.get("action")).get("url")
            self._row(iid, "web", target=_clip(q, MAX_TARGET), tool=ty, **({"ok": True} if done else {}))
        elif ty == "todo_list":
            target, detail = _todo(it.get("items"))
            self._row(iid, "todo", target=target, detail=detail, ok=True, tool=ty)
        elif ty == "error":
            self._row(iid, "error", detail=_clip(it.get("message"), MAX_DETAIL), ok=True, tool=ty)

    # opencode: run --format json (one line per finished tool call)
    def _opencode(self, t: str, ev: dict) -> None:
        part, lid = _d(ev.get("part")), f"L{self._seq}"
        if t == "tool_use":
            self._opencode_tool(part)
        elif t in ("text", "reasoning"):
            text = _s(part.get("text")).strip()
            if text and t == "text":
                self._say_row(_s(part.get("id")) or lid, text)
            elif text:
                self._row(_s(part.get("id")) or lid, "think", detail=_clip(text, MAX_DETAIL, flat=False), ok=True)
        elif t == "step_finish":
            tok, cost = _d(part.get("tokens")), _num(part.get("cost"))
            if tok or cost is not None:
                cache = _d(tok.get("cache"))
                read, write = _int(cache.get("read")), _int(cache.get("write"))
                self._usage(_int(tok.get("input")) + read + write, _int(tok.get("output")), read, cost, add=True)
            if part.get("reason") == "stop":
                self._row(lid, "done", ok=True)
                self._end()
        elif t == "error":
            err = ev.get("error")
            e = _d(err)
            msg = _s(_d(e.get("data")).get("message") or e.get("name")) if isinstance(err, dict) else _s(err)
            self._row(lid, _err_kind(msg), target=_clip(e.get("name"), MAX_TARGET), detail=_clip(msg, MAX_DETAIL),
                      ok=False)

    def _opencode_tool(self, part: dict) -> None:
        st = _d(part.get("state"))
        inp, md, tool = _d(st.get("input")), _d(st.get("metadata")), _s(part.get("tool"))
        cid = _s(part.get("callID")) or _s(part.get("id")) or f"L{self._seq}"
        status, title = _s(st.get("status")), _s(st.get("title"))
        ok = None if status in ("pending", "running", "") else status == "completed"
        err = _s(st.get("error")) if status == "error" else ""
        kw: dict[str, Any] = {"tool": tool, "ok": ok}
        if err:
            kw.update(detail=_clip(err, MAX_DETAIL), output=_tail(err))
        path = self._path(inp.get("filePath") or inp.get("path") or title)
        if tool == "read":
            self._row(cid, "read", target=path, **kw)
        elif tool in ("edit", "multiedit"):
            fd = _d(md.get("filediff"))
            if _intn(fd.get("additions")) is not None:
                add, rem = fd["additions"], _intn(fd.get("deletions")) or 0
            else:
                add, rem = _diff_counts(_s(inp.get("oldString")), _s(inp.get("newString")))
            self._row(cid, "edit", target=path, added=None if err else add, removed=None if err else rem, **kw)
        elif tool == "write":
            created = md.get("exists") is False
            if created and not err:
                kw.update(detail="created", added=_nlines(inp.get("content")), removed=0)
            self._row(cid, "write", target=path, **kw)
        elif tool in ("apply_patch", "patch"):
            files = [f for f in _l(md.get("files")) if isinstance(f, dict)]
            for i, f in enumerate(files):
                k = _s(f.get("type"))
                fkw = dict(kw, target=_clip_left(rel_path(_s(f.get("relativePath")) or _s(f.get("filePath")),
                                                          self._root())),
                           added=_intn(f.get("additions")), removed=_intn(f.get("deletions")))
                fkw.setdefault("detail", "created" if k == "add" else "")
                self._row(f"{cid}.{i}", {"add": "write", "delete": "delete"}.get(k, "edit"), **fkw)
            if not files:
                self._row(cid, "edit", target=_clip(title or tool, MAX_TARGET), **kw)
        elif tool == "bash":
            code = _intn(md.get("exit"))
            out = md.get("output") if isinstance(md.get("output"), str) else st.get("output")
            kw.update(ok=None if ok is None else ok and code in (0, None), exit_code=code)
            if not err:
                kw.update(detail=_clip(inp.get("description"), MAX_DETAIL), output=_tail(out))
            self._row(cid, "run", target=simplify_command(_s(inp.get("command")) or title, self._root()), **kw)
        elif tool in ("grep", "glob"):
            where = " ".join(x for x in (self._path(inp.get("path")), _s(inp.get("include"))) if x not in ("", "."))
            kw.setdefault("detail", _clip(where, MAX_DETAIL))
            kw.setdefault("output", _tail(st.get("output")))
            self._row(cid, "search", target=_clip(inp.get("pattern") or title, MAX_TARGET), **kw)
        elif tool in ("list", "ls"):
            kw.setdefault("output", _tail(st.get("output")))
            self._row(cid, "list", target=self._path(inp.get("path")) or ".", **kw)
        elif tool in ("todowrite", "todoread"):
            target, detail = _todo(inp.get("todos"))
            kw.setdefault("detail", detail)
            self._row(cid, "todo", target=target or _clip(title, MAX_TARGET), **kw)
        elif tool in ("webfetch", "websearch", "codesearch"):
            self._row(cid, "web", target=_clip(inp.get("url") or inp.get("query") or title, MAX_TARGET), **kw)
        elif tool == "task":
            kw.setdefault("output", _tail(st.get("output")))
            self._row(cid, "agent", target=_clip(inp.get("description") or title, MAX_TARGET), **kw)
        else:
            kw.setdefault("detail", _clip(title, MAX_DETAIL))
            self._row(cid, "tool", target=_clip(tool, MAX_TARGET), **kw)

    # Gemini CLI: stream-json
    def _gemini(self, t: str, ev: dict) -> None:
        lid = f"L{self._seq}"
        if t == "init":
            self._row(lid, "start", target=_clip(ev.get("model"), MAX_TARGET),
                      detail=_clip(ev.get("session_id"), MAX_DETAIL), ok=True)
        elif t == "message":
            if ev.get("role") != "assistant":
                self._say = None               # the prompt echo
                return
            rid, text = self._say if ev.get("delta") and self._say else (lid, "")
            text = (text + _s(ev.get("content")))[: MAX_FINAL + 1]
            self._say = (rid, text)
            if text.strip():
                self._say_row(rid, text)
        elif t == "tool_use":
            self._gemini_tool(_s(ev.get("tool_name")), _d(ev.get("parameters")), _s(ev.get("tool_id")) or lid)
        elif t == "tool_result":
            row = self._rows.get(_s(ev.get("tool_id")))
            if row is None:
                return
            e = ev.get("error")
            msg = _s(_d(e).get("message")) if isinstance(e, dict) else _s(e)
            ok = ev.get("status") == "success"
            kw: dict[str, Any] = {"ok": ok}
            if row["kind"] != "read" or msg:
                kw["output"] = _tail(msg if row["kind"] == "read" else ev.get("output") or msg)
            if not ok:
                kw["detail"] = _clip(msg, MAX_DETAIL) or row["detail"]
                if row["kind"] in ("edit", "write"):
                    kw.update(added=None, removed=None)
            self._row(row["id"], **kw)
        elif t == "error":
            msg = _s(ev.get("message"))
            self._row(lid, _err_kind(msg), target=_clip(ev.get("severity"), MAX_TARGET),
                      detail=_clip(msg, MAX_DETAIL), ok=ev.get("severity") != "error")
        elif t == "result":
            e = ev.get("error")
            msg = _s(_d(e).get("message")) if isinstance(e, dict) else _s(e)
            stats = _d(ev.get("stats"))
            tin = _int(stats.get("input_tokens"))
            if stats:
                self._usage(tin or None, _int(stats.get("output_tokens")) or None,
                            _int(stats.get("cached")) if tin else None, None)
            self._row(lid, "done", target=_clip(ev.get("status"), MAX_TARGET), detail=_clip(msg, MAX_DETAIL),
                      ok=ev.get("status") == "success")
            self._end()

    def _gemini_tool(self, name: str, p: dict, rid: str) -> None:
        kind = _GEMINI_KINDS.get(name, "tool")
        fp = p.get("file_path") or p.get("absolute_path") or p.get("path") or ""
        kw: dict[str, Any] = {"tool": name}
        if name == "read_many_files":
            files = [rel_path(_s(x), self._root()) for x in _l(p.get("paths")) or _l(p.get("include"))]
            kw.update(target=_clip(", ".join(files), MAX_TARGET),
                      task_file=bool(files) and all(map(_is_task_file, files)))
        elif kind in ("read", "write", "edit"):
            kw["target"] = self._path(fp)
            if kind == "write":
                kw["added"] = _nlines(p.get("content"))
            elif kind == "edit":
                kw["added"], kw["removed"] = _diff_counts(_s(p.get("old_string")), _s(p.get("new_string")))
        elif kind == "run":
            kw.update(target=simplify_command(_s(p.get("command")), self._root()),
                      detail=_clip(p.get("description"), MAX_DETAIL))
        elif kind == "search":
            where = " ".join(x for x in (self._path(p.get("dir_path") or p.get("path")),
                                         _s(p.get("include_pattern") or p.get("include"))) if x not in ("", "."))
            kw.update(target=_clip(p.get("pattern"), MAX_TARGET), detail=_clip(where, MAX_DETAIL))
        elif kind == "list":
            kw["target"] = self._path(p.get("dir_path") or p.get("path")) or "."
        elif kind == "web":
            kw["target"] = _clip(p.get("query") or p.get("url") or p.get("prompt"), MAX_TARGET)
        elif kind == "todo":
            kw["target"], kw["detail"] = _todo(p.get("todos"))
        elif kind == "agent":
            kw["target"] = _clip(p.get("agent_name") or p.get("description") or name, MAX_TARGET)
        else:
            kw.update(target=_clip(name, MAX_TARGET), detail=_args(p))
        self._row(rid, kind, **kw)

    # api executor (orch/adapters/api.py): request, then result or error
    def _api(self, t: str, ev: dict) -> None:
        lid = f"L{self._seq}"
        if t == "request":
            self._row(lid, "start", target=_clip(ev.get("model"), MAX_TARGET),
                      detail=f"{len(_l(ev.get('files')))} files, {_int(ev.get('chars'))} chars", ok=True)
        elif t == "result":
            u = _d(ev.get("usage"))
            tin = _int(u.get("prompt_tokens") or u.get("input_tokens"))
            hit = u.get("prompt_cache_hit_tokens", _d(u.get("prompt_tokens_details")).get("cached_tokens"))
            self._text_result(ev, tin or None, _int(u.get("completion_tokens") or u.get("output_tokens")) or None,
                              _int(hit) if tin else None, None)
        else:
            kind = _s(ev.get("kind"))
            self._row(lid, "limit" if kind == "rate_limit" else "error", target=_clip(kind, MAX_TARGET),
                      detail=_clip(ev.get("error"), MAX_DETAIL), ok=False)
            self._end()                        # the api executor writes nothing after it

    # fake executor (tests and the offline demo)
    def _fake(self, t: str, ev: dict) -> None:
        if t == "result":
            tin, tout = _int(ev.get("tokens_in")), _int(ev.get("tokens_out"))
            self._text_result(ev, tin or None, tout or None, None, _num(ev.get("cost_usd")))
        else:
            msg = _s(ev.get("error"))
            self._row(f"L{self._seq}", "limit" if "rate" in msg.lower() else _err_kind(msg),
                      detail=_clip(msg, MAX_DETAIL), ok=False)

    def _text_result(self, ev: dict, tin: int | None, tout: int | None, cached: int | None,
                     cost: float | None) -> None:
        lid = f"L{self._seq}"
        if _s(ev.get("text")).strip():
            self._say_row(lid, ev["text"])
        if tin is not None or tout is not None or cost is not None:
            self._usage(tin, tout, cached, cost)
        self._row(lid + ".1", "done", target=_clip(ev.get("model"), MAX_TARGET), ok=True)
        self._end()


# --- process-wide cache -----------------------------------------------------------------------------

_FEEDS: OrderedDict[tuple[str, str | None], Feed] = OrderedDict()
_FEEDS_LOCK = threading.Lock()


def feed(events_path: str | os.PathLike, root: str | None = None) -> Feed:
    """The shared Feed of one events file (with outputs), brought up to date. Thread-safe."""
    key = (os.path.abspath(os.fspath(events_path)), str(root) if root else None)
    with _FEEDS_LOCK:
        f = _FEEDS.get(key)
        if f is None:
            f = _FEEDS[key] = Feed(key[0], root, outputs=True)
            while len(_FEEDS) > MAX_FEEDS:
                _FEEDS.popitem(last=False)
        else:
            _FEEDS.move_to_end(key)
    f.update()
    return f


def activity(events_path: str | os.PathLike, root: str | None = None, limit: int | None = None,
             outputs: bool = False) -> list[dict]:
    """Copies of the run's items (the newest `limit` ones); `output` is blanked unless outputs=True."""
    items = feed(events_path, root).items
    if limit is not None:
        items = items[-limit:] if limit > 0 else []
    return [dict(it) if outputs else {**it, "output": ""} for it in items]
