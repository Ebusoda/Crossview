"""「放进我的项目文件夹」: bringing what orch merged on its integration branch into the user's own checkout.

orch never touches the user's folder (orch/workspace.py): finished items are merged on its integration branch, in a
worktree of its own. This is the one place MAO changes the user's folder, and only when the user clicks.

state() says what would change and whether it can be done now. It is cached by the repository's own files (HEAD, the
index, the refs, the markers of an operation in progress), so a page poll stats a few files instead of running git;
an edit to a tracked file shows up after STATE_TTL at the latest, and land() checks everything again anyway.

land() runs one landing per folder at a time, under the project's lock (no job worker starts meanwhile) and orch's
own lock file (no orch from the command line merges meanwhile: orch may merge, run the checks and undo that merge,
so the integration branch is never landed while orch runs, nor an item whose check after its merge never
finished). It resolves the integration branch to one sha, which must be the one the page listed (else nothing
happens and the page gets the new list), then fast-forwards, or makes a merge commit with orch's commit identity. Conflicts are found beforehand with
`git merge-tree` when git has it (2.38+), so the folder is not touched at all then. Whatever goes wrong during the
merge, the folder is put back exactly as it was: `git merge --abort` while a merge is in progress, and for a
checkout git left half-written (on Windows a file open in Excel cannot be replaced) only the files whose content is
provably what this merge wrote (content, kind, exec bit) are put back; nothing else is reset. Untracked and ignored files are never touched:
a merge that would overwrite one is refused (and git is told --no-overwrite-ignore as well).
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

from orch import workspace as ws
from orch.config import Config
from orch.proc import pid_alive

from . import projects as pm
from . import timeline
from .jobs import _ro

STATE_TTL = 20          # seconds a state is reused while the repository's files stay the same
LIST_MAX = 50           # changed files sent to the page
NAMES = 5               # files named in a refusal
MERGE_TIMEOUT = 300
LOCK_WAIT = 3           # seconds to wait for the project's lock (a job worker holds it for its whole run)
CHUNK = 100             # paths per git call (Windows command lines are short)
SCHEMA = """
CREATE TABLE IF NOT EXISTS landings (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, path TEXT NOT NULL, user_id INTEGER NOT NULL,
 at REAL NOT NULL, branch TEXT, integ TEXT, from_sha TEXT, to_sha TEXT, integ_sha TEXT, files INTEGER, kind TEXT
);
CREATE INDEX IF NOT EXISTS landings_project ON landings(project_id, id);
"""
TEXT = {
    "folder_gone": "找不到你的项目文件夹，可能被移走或删掉了。",
    "no_repo": "你的项目文件夹还没有保存过版本，没法放进去。",
    "detached": "你的项目文件夹现在不在平常用的版本上（比如正在看以前的版本）。为了不弄乱你的文件，这次先不放。",
    "orch_branch": "你的项目文件夹现在切到了 MAO 自己用的版本上。为了不弄乱你的文件，这次先不放。",
    "in_progress": "你的项目文件夹里有一个操作还没做完（比如合并到一半）。为了不弄乱你的文件，这次先不放。",
    "dirty": "你的项目文件夹里有还没保存的改动：{names}。为了不弄乱你的文件，这次先不放。",
    "untracked": "你的项目文件夹里已经有同名的文件：{names}。放进去会把它们盖掉，所以这次先不放。",
    "conflict": "这些文件在你的项目文件夹里也改过，和这次的改动合不到一起：{names}。为了不弄乱你的文件，这次先不放。",
    "in_use": "这些文件正被别的程序打开着（比如 Excel）：{names}。先关掉它们，再放一次。",
    "running": "MAO 正在做这个项目的任务，做完才能放。",
    "unchecked": "MAO 上次停下来的时候，有改动还没检查完，所以先不放。接着做完这件事（或者选「不做了」）以后就能放了。",
}
NOTHING = "没有新的改动要放进去。"
CHANGED = "刚才又有改动做好了，要放进去的文件变了。请再看一下，没问题就再点「确定」。"
STILL_THERE = "MAO 想删掉的这些文件还在：{names}。可能有别的程序正在用它们，可以关掉后自己删掉。"
FAILED = "没有放成，你的项目文件夹没有变。"
FAILED_IN_USE = "没有放成：有文件正被别的程序打开着（比如 Excel）。你的项目文件夹没有变，关掉它们再放一次。"
FAILED_SLOW = "没有放成：等了太久还没有做完。你的项目文件夹没有变，请稍后再试。"
LOOK_SLOW = "现在看不了你的项目文件夹（等了太久没有回应），请稍后再看。"
LOOK_FAILED = "现在看不了你的项目文件夹里有什么改动，请稍后再看。"
NOT_READY = "这个文件夹还没有准备好（缺少 .agents/orch.toml），请重新添加。"
LEFT = "没有放成，而且这些文件没能放回原样：{names}。先关掉正在用它们的程序，再请管理员看一下。"
OPS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "index.lock")
OWN_FILES = ("HEAD", "index", *OPS)                       # each worktree has its own
REF_FILES = ("packed-refs", "reftable/tables.list")      # shared by the worktrees of a repository
MERGE_STATE = ("MERGE_HEAD", "MERGE_MSG", "MERGE_MODE", "AUTO_MERGE")
CHANGE = {"A": "new", "D": "deleted"}       # everything else: changed
RANK = {"deleted": 0, "changed": 1, "new": 2}           # the confirm list: the user's own files first
LINK, GITLINK = "120000", "160000"
SQUASH = re.compile(r"(T-\d+): ")                       # orch's merge of an item: "<task id>: <title>"
BUSY_WORDS = ("unable to unlink", "unable to create", "permission denied", "invalid argument", "device or resource busy")


class _Fail(Exception):
    """A refusal with its friendly text (land() turns it into a ValueError: a 400 for the page)."""


def _none(oid: str) -> str | None:
    return None if not oid or set(oid) == {"0"} else oid


def _path(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _shown(path: str) -> str:
    return path.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _lit(paths: list[str]) -> list[str]:
    return [":(literal)" + x for x in paths]


def _chunks(items: list, n: int = CHUNK):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _stat(p: Path):
    try:
        st = os.stat(p)
    except (OSError, ValueError):
        return None
    return st.st_mtime_ns, st.st_size


def _git(args: list[str], cwd: Path, timeout: float | None = None, write: bool = False):
    return pm._git(args, cwd, timeout=timeout, read_only=not write)


def _ok(r, what: str):
    return pm._ok(r, what)


def _raw_diff(out: bytes) -> list[dict]:
    """`git diff --raw -z --no-renames`: one entry per path, blob ids of both sides (None = no file there)."""
    parts, entries, i = out.split(b"\0"), [], 0
    while i < len(parts):
        meta = parts[i].decode("ascii", "replace")
        i += 1
        if not meta.startswith(":"):
            continue
        f = meta[1:].split()
        n = 2 if f and f[-1][:1] in ("R", "C") else 1
        names = parts[i:i + n]
        i += n
        if len(f) < 5 or not names:
            continue
        entries.append({"path": _path(names[-1]), "status": f[4][:1], "h_mode": f[0], "r_mode": f[1],
                        "h": _none(f[2]) if f[0] != "000000" else None, "r": _none(f[3]) if f[1] != "000000" else None})
    return entries


def _dirty(p: Path, timeout: float | None = None) -> list[str]:
    """Tracked files with changes, staged or not (MAO's own folders left out)."""
    r = _ok(_git(["status", "--porcelain", "-z", "--untracked-files=no"], p, timeout=timeout), "status")
    items, out, i = r.stdout.split(b"\0"), [], 0
    while i < len(items):
        it = items[i]
        i += 1
        if len(it) < 4:
            continue
        if b"R" in it[:2] or b"C" in it[:2]:   # renamed / copied: the old name follows
            i += 1
        name = _path(it[3:])
        if not name.startswith((".agents/", ".task/")) and it[:2] not in (b"??", b"!!"):
            out.append(name)
    return out


def _other_case(q: Path) -> bool:
    """q answers only as another case of a name in its folder (a folder that ignores case)."""
    try:
        return q.name not in os.listdir(q.parent)
    except OSError:
        return False


def _blocked(p: Path, rel: str, gone: set[str], folded: frozenset[str] = frozenset()) -> bool:
    """Something untracked (or ignored) is where the merge would create this file: the file, a file in place of
    one of its folders, or a folder holding more than the tracked files the merge removes (gone). folded: gone in
    case-folded form when the folder ignores case (Windows, macOS): a name that answers only as another case of a
    tracked file this landing removes is that file (a rename that changes only the case), not a clash."""
    def removed(sub: str, q: Path) -> bool:
        return sub in gone or (sub.casefold() in folded and _other_case(q))
    parts = rel.split("/")
    for i in range(1, len(parts)):
        q = p.joinpath(*parts[:i])
        if os.path.lexists(q) and not os.path.isdir(q) and not removed("/".join(parts[:i]), q):
            return True
    q = p.joinpath(*parts)
    if not os.path.lexists(q):
        return False
    if os.path.islink(q) or not os.path.isdir(q):
        return not removed(rel, q)
    for d, dirs, files in os.walk(q):
        base = Path(d).relative_to(p).as_posix()
        if any(f"{base}/{f}" not in gone for f in files) or any(os.path.islink(os.path.join(d, x)) for x in dirs):
            return True
    return False


def _in_use(path: Path) -> bool:
    """Windows: another program (Excel, Word) holds the file open, so git could neither replace nor delete it.
    Elsewhere a file can always be replaced."""
    if os.name != "nt":
        return False
    try:
        with open(path, "r+b"):
            return False
    except FileNotFoundError:
        return False
    except PermissionError:
        return os.access(path, os.W_OK)    # a read-only file is no problem: git replaces it all the same
    except OSError:
        return True


def _names(items: list[str]) -> str:
    return pm._names([_shown(x) for x in items], NAMES)


def _fail(code: str, names: list[str] | None = None) -> _Fail:
    return _Fail(TEXT[code].format(names=_names(names or [])))


def _ignorecase(p: Path) -> bool:
    """git treats this folder as one that ignores case (it sets core.ignorecase when it finds one)."""
    v = pm._out(_git(["config", "--bool", "core.ignorecase"], p))
    return v == "true" if v in ("true", "false") else (os.name == "nt" or sys.platform == "darwin")


def unchecked(p: Path, head: str, sha: str) -> list[str]:
    """Items merged on the integration branch whose check after the merge never finished: orch undoes a merge only
    once that check says no, so a worker stopped meanwhile (on Windows a stop is always a kill) leaves it there with
    the item not merged. Their task ids (none when orch's records cannot be read: not ours to judge)."""
    out = _ok(_git(["log", "-z", "--max-count=2000", "--format=%s", f"{head}..{sha}"], p), "log").stdout
    ids = {m.group(1) for s in out.split(b"\0") if (m := SQUASH.match(_path(s).strip()))}
    if not ids:
        return []
    return sorted(r["id"] for r in _ro(p, "SELECT id, status FROM tasks") if r["id"] in ids and r["status"] != "merged")


def merge_tree(p: Path, head: str, sha: str) -> tuple[str | None, list[str], bool]:
    """(tree of the merge result, conflicted files, supported): the merge worked out without touching the
    folder, its index or any branch (git 2.38+; only new objects are written)."""
    r = _git(["merge-tree", "--write-tree", "-z", "--name-only", "--no-messages", head, sha], p, timeout=60)
    if r.returncode not in (0, 1):
        return None, [], False                 # an older git: no prediction
    parts = r.stdout.split(b"\0")
    tree = parts[0].decode("ascii", "replace").strip()
    if not tree:
        return None, [], False
    if r.returncode == 0:
        return tree, [], True
    return None, list(dict.fromkeys(_path(x) for x in parts[1:] if x)), True


def inspect(p: Path, integ: str, deep: bool = False) -> dict:
    """What landing the integration branch in this folder would change, and the first reason it cannot be done
    now ("" = it can). deep: also the files another program holds open (only right before landing)."""
    ins = {"integ": integ, "exists": False, "sha": "", "head": "", "ref": "", "current": "", "ahead": 0,
           "ff": False, "plan": [], "reason": "", "names": [], "fold": False}

    def stop(reason: str, names: list[str] | None = None) -> dict:
        ins.update(reason=reason, names=list(names or []))
        return ins

    try:
        if not p.is_dir():
            return stop("folder_gone")
        gd = pm._git_dir(p)
    except OSError:
        return stop("folder_gone")
    if gd is None:
        return stop("no_repo")
    r = _git(["symbolic-ref", "-q", "HEAD"], p)
    if r.returncode not in (0, 1):
        return stop("no_repo")
    ins["ref"] = pm._out(r) if r.returncode == 0 else ""
    ins["current"] = ins["ref"].removeprefix("refs/heads/")
    r = _git(["rev-parse", "-q", "--verify", "HEAD^{commit}"], p)
    if r.returncode != 0:
        return stop("no_repo")
    ins["head"] = pm._out(r)
    r = _git(["rev-parse", "-q", "--verify", f"refs/heads/{integ}^{{commit}}"], p)
    if r.returncode != 0:
        return stop("nothing")
    ins.update(exists=True, sha=pm._out(r))
    counts = pm._out(_ok(_git(["rev-list", "--left-right", "--count", f"{ins['head']}...{ins['sha']}"], p),
                         "rev-list")).split()
    if len(counts) != 2 or not all(x.isdigit() for x in counts):
        raise ws.GitError(f"git rev-list: {' '.join(counts)[:100]}")
    mine, theirs = int(counts[0]), int(counts[1])
    ins.update(ahead=theirs, ff=mine == 0)
    if not theirs:
        return stop("nothing")
    conflicts: list[str] = []
    target, only = ins["sha"], None
    if not ins["ff"]:
        tree, conflicts, _known = merge_tree(p, ins["head"], ins["sha"])
        if tree:
            target = tree
        else:   # conflicts, or an older git: the files the integration side changed, with its version of them
            only = {e["path"] for e in _raw_diff(_ok(_git(
                ["diff", "--raw", "-z", "--no-renames", "--no-abbrev", f"{ins['head']}...{ins['sha']}"], p),
                "diff").stdout)}
    out = _ok(_git(["diff", "--raw", "-z", "--no-renames", "--no-abbrev", ins["head"], target], p), "diff").stdout
    plan = [e for e in _raw_diff(out) if only is None or e["path"] in only]
    ins["plan"] = plan
    if not plan and not conflicts:
        return stop("nothing")
    if unchecked(p, ins["head"], ins["sha"]):
        return stop("unchecked")
    if not ins["ref"]:
        return stop("detached")
    if ins["current"].startswith("orch/") or ins["current"] == integ:
        return stop("orch_branch")
    if any(os.path.lexists(gd / name) for name in OPS):
        return stop("in_progress")
    dirty = _dirty(p)
    if dirty:
        return stop("dirty", dirty)
    gone = {e["path"] for e in plan if e["h"] is not None and e["r"] is None}
    new = [e["path"] for e in plan if e["h"] is None and e["r"] is not None]
    ins["fold"] = bool(new) and _ignorecase(p)
    folded = frozenset(x.casefold() for x in gone) if ins["fold"] else frozenset()
    blocked = [x for x in new if _blocked(p, x, gone, folded)]
    if blocked:
        return stop("untracked", blocked)
    if conflicts:
        return stop("conflict", conflicts)
    if deep:
        busy = [e["path"] for e in plan if e["h"] is not None and _in_use(p / e["path"])]
        if busy:
            return stop("in_use", busy)
    return ins


def _common(gd: Path) -> Path:
    """Where the branches are: a linked worktree keeps only its own HEAD and index (its commondir file says where)."""
    try:
        c = (gd / "commondir").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return gd
    return Path(c) if Path(c).is_absolute() else (gd / c).resolve()


def _key(p: Path, integ: str) -> tuple:
    """Changes whenever a branch, the index or an operation in progress changes (file stats only, no git)."""
    try:
        gd = pm._git_dir(p)
    except OSError:
        gd = None
    if gd is None:
        return ("no git", p.is_dir())
    try:
        head = (gd / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        head = ""
    common = _common(gd)
    refs = [*REF_FILES, f"refs/heads/{integ}"] + ([head[5:].strip()] if head.startswith("ref: ") else [])
    return (head, *((n, _stat(gd / n)) for n in OWN_FILES), *((n, _stat(common / n)) for n in refs))


@contextmanager
def orch_lock(p: Path):
    """orch's own lock file (.agents/orch.lock, a pid), taken the way orch takes it: no orch from the command line
    (and no job worker) can start while it is ours. Yields False when a live orch holds it."""
    lock = p / ".agents" / "orch.lock"
    mine = str(os.getpid())
    try:
        holder = int(lock.read_text().strip() or 0)
    except (OSError, ValueError):
        holder = 0
    if holder and holder != os.getpid() and pid_alive(holder):
        yield False
        return
    try:
        lock.write_text(mine)
        ours = lock.read_text().strip() == mine     # nobody took it over in between
    except OSError:
        ours = False
    try:
        yield ours
    finally:
        try:
            if lock.read_text().strip() == mine:
                lock.unlink()
        except OSError:
            pass


def identity(p: Path) -> list[str]:
    """orch's own commit identity for this project (orch.workspace.ident: [git] in orch.toml, else the
    repository's or the global git config, else orch). Process-wide in orch, so taken under projects' lock."""
    with pm._prep:
        try:
            Config.load(p)
        except ValueError:                             # orch.toml unreadable: as if [git] were empty
            ws.set_identity("", "")
        return ws.ident(p)


def _blob(data: bytes, like: str | None) -> str:
    """git's id of a blob holding data (sha1, or sha256 in a repository that uses it: told by an id's length)."""
    h = hashlib.sha256() if like and len(like) == 64 else hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def _flags(p: Path) -> dict[str, bool]:
    """Whether git keeps the exec bit (core.filemode) and real links (core.symlinks) in this folder."""
    out = {}
    for name in ("filemode", "symlinks"):
        out[name] = pm._out(_git(["config", "--bool", f"core.{name}"], p, timeout=MERGE_TIMEOUT)) != "false"
    return out


def _disk(p: Path, plan: list[dict]) -> dict[str, tuple]:
    """What each path of a landing holds now: ("none",), ("dir",), ("link", blob id) or ("file", blob ids, exec);
    a file's blob both with and without the repository's filters (line endings, LFS): git may have written either."""
    out, files = {}, []
    for e in plan:
        q, parts = p / e["path"], e["path"].split("/")
        try:
            if any(os.path.islink(p.joinpath(*parts[:i])) for i in range(1, len(parts))):
                raise NotADirectoryError(e["path"])  # through a link: somewhere else, never ours to touch
            st = os.lstat(q)
        except (OSError, ValueError):                 # also a file standing where one of its folders was
            out[e["path"]] = ("none",)
            continue
        if stat.S_ISLNK(st.st_mode):
            try:
                out[e["path"]] = ("link", _blob(os.fsencode(os.readlink(q)), e["h"] or e["r"]))
            except OSError:
                out[e["path"]] = ("link", None)
        elif stat.S_ISDIR(st.st_mode):
            out[e["path"]] = ("dir",)
        else:
            out[e["path"]] = ("file", set(), bool(st.st_mode & stat.S_IXUSR))
            files.append(e["path"])
    for part in _chunks(files):
        for opts in ([], ["--no-filters"]):
            try:
                r = _git(["hash-object", *opts, "--", *part], p, timeout=MERGE_TIMEOUT)
            except subprocess.TimeoutExpired:
                continue
            if r.returncode == 0:
                for path, blob in zip(part, pm._out(r).split()):
                    out[path][1].add(blob)
    return out


def _holds(d: tuple, blob: str | None, mode: str, fl: dict[str, bool]) -> bool:
    """The path holds exactly this version (blob None: nothing at all), its kind and exec bit too."""
    if blob is None:
        return d[0] == "none"
    if d[0] == "link":
        return mode == LINK and d[1] == blob
    if d[0] != "file" or blob not in d[1]:
        return False
    if mode == LINK:
        return not fl["symlinks"]                     # git wrote the link as a plain file holding its target
    return not fl["filemode"] or d[2] == (mode == "100755")


class Changed(ValueError):
    """The integration branch moved on after the page listed what would change: nothing was done. land: the
    state as it is now, for the page to show the new list."""

    def __init__(self, text: str, land: dict | None):
        super().__init__(text)
        self.land = land


class Landing:
    def __init__(self, app):
        self.app = app
        app.db.db.executescript(SCHEMA)
        self._cache: dict[str, tuple[tuple, float, dict]] = {}
        self._one: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # -- what the pages show ---------------------------------------------------------------------------
    def running(self, p: Path) -> bool:
        """A job of this project is at work (or about to start), or an orch started elsewhere holds its lock:
        the integration branch may be changing."""
        jobs = getattr(self.app, "jobs", None)
        if jobs is not None and jobs.running(str(p)):
            return True
        try:
            holder = int((p / ".agents" / "orch.lock").read_text().strip() or 0)
        except (OSError, ValueError):
            return False
        return holder != os.getpid() and pid_alive(holder)

    def _inspect(self, p: Path, integ: str) -> dict:
        k, now = _key(p, integ), time.monotonic()
        hit = self._cache.get(str(p))
        if hit and hit[0] == k and now - hit[1] < STATE_TTL:
            return hit[2]
        try:
            ins = inspect(p, integ)
        except subprocess.TimeoutExpired:
            ins = {"reason": "slow"}
        except (ws.GitError, OSError, ValueError) as e:
            ins = {"reason": "error", "detail": str(e)[:300]}
        self._cache[str(p)] = (k, now, ins)
        return ins

    def last(self, pid: int, since: float | None = None, user=None) -> dict | None:
        """The newest landing of the project; with since: the first one at or after it (for a job's page: the one
        that brought that job's work in). mine: the viewer (user) made it."""
        sql = ("SELECT l.at, l.files, l.user_id, l.from_sha, l.to_sha, u.display, u.name FROM landings l "
               "LEFT JOIN users u ON u.id = l.user_id WHERE l.project_id=?")
        rows = self.app.db.q(sql + " ORDER BY l.id DESC LIMIT 1", (pid,)) if since is None else \
            self.app.db.q(sql + " AND l.at >= ? ORDER BY l.id LIMIT 1", (pid, since))
        if not rows:
            return None
        r = rows[0]
        return {"at": r["at"], "files": r["files"], "by": r["display"] or r["name"] or "",
                "mine": user is not None and r["user_id"] == user["id"],
                "from": (r["from_sha"] or "")[:12], "to": (r["to_sha"] or "")[:12]}

    def state(self, project: dict, since: float | None = None, user=None) -> dict:
        """The project's landing state as the pages take it (cheap enough for every poll). since: a job's end, for
        the landing that brought its work in (see last)."""
        p = Path(project["path"])
        integ = timeline.integration_branch(p)
        ins = self._inspect(p, integ)
        reason = ins.get("reason") or ""
        plan = ins.get("plan") or []
        files = sorted(({"path": _shown(e["path"]), "change": CHANGE.get(e["status"], "changed")} for e in plan),
                       key=lambda f: RANK[f["change"]])      # deleted, changed, new: what a list cut short keeps
        counts = {k: sum(f["change"] == k for f in files) for k in RANK}
        out = {"branch": integ, "exists": bool(ins.get("exists")), "sha": (ins.get("sha") or "")[:12],
               "ahead": ins.get("ahead") or 0, "file_count": len(plan), "current": ins.get("current") or "",
               "folder": str(p), "files": files[:LIST_MAX], "counts": counts,
               "running": False, "can_land": False, "reason": reason, "text": "", "detail": "",
               "last": self.last(project["id"], since, user)}
        if reason in ("nothing", "folder_gone", "no_repo", "slow", "error"):
            out["text"] = {"nothing": "", "slow": LOOK_SLOW, "error": LOOK_FAILED}.get(reason) or TEXT.get(reason, "")
            out["detail"] = ins.get("detail", "")
            if reason != "nothing":
                out.update(files=[], file_count=0, ahead=0, counts=dict.fromkeys(RANK, 0))
            return out
        out["running"] = self.running(p)
        if out["running"]:
            out["reason"] = "running"
        if out["reason"]:
            out["text"] = TEXT[out["reason"]].format(names=_names(ins.get("names") or []))
        out["can_land"] = not out["reason"]
        return out

    def forget(self, path) -> None:
        self._cache.pop(str(Path(path)), None)

    # -- the button ------------------------------------------------------------------------------------
    def land(self, user, pid, sha: str = "") -> dict:
        """POST /api/projects/land. sha: the integration branch as the page listed it (what the user agreed to).
        Returns {ok, files, kind, land, note}; a refusal is a ValueError with friendly text (and the folder exactly
        as it was), Changed when the branch moved on since the page looked."""
        project = self.app.projects.get(pid)
        p = Path(project["path"])
        with self._guard:
            one = self._one.setdefault(os.path.normcase(str(p)), threading.Lock())
        with one:                                      # two clicks at once: the second finds nothing new
            lock = self.app.projects.lock_for(p)
            if not lock.acquire(timeout=LOCK_WAIT):    # a job worker holds it for as long as it runs
                raise ValueError(TEXT["running"])
            try:
                if not p.is_dir():
                    raise _fail("folder_gone")
                if not (p / ".agents").is_dir():
                    raise _Fail(NOT_READY)
                if self.running(p):
                    raise _fail("running")
                with orch_lock(p) as ours:
                    if not ours:
                        raise _fail("running")
                    return self._land(user, project, p, sha)
            except _Fail as e:
                raise ValueError(str(e)) from None
            except subprocess.TimeoutExpired:
                raise ValueError(FAILED_SLOW) from None
            except (ws.GitError, OSError) as e:
                print(f"MAO: landing in {p} failed: {e}", flush=True)
                raise ValueError(FAILED) from None
            finally:
                lock.release()
                self.forget(p)

    def _land(self, user, project: dict, p: Path, want: str = "") -> dict:
        integ = timeline.integration_branch(p)
        ins = inspect(p, integ, deep=True)            # everything again, now that nothing else can start
        if ins["reason"] == "nothing":
            raise _Fail(NOTHING)
        if ins["reason"]:
            raise _fail(ins["reason"], ins["names"])
        sha, before, ref, plan = ins["sha"], ins["head"], ins["ref"], ins["plan"]   # the integration branch: once
        want = str(want or "").strip().lower()
        if want and not (len(want) >= 7 and sha.startswith(want)):    # not what the page listed
            self.forget(p)
            raise Changed(CHANGED, self.state(project, user=user))
        fresh = set()                                  # folders this merge would create for its new files
        for e in plan:
            if e["h"] is None:
                parts = e["path"].split("/")[:-1]
                fresh |= {"/".join(parts[:i]) for i in range(1, len(parts) + 1)}
        fresh = {d for d in fresh if not os.path.lexists(p / d)}
        who = identity(p)
        if ins["ff"]:
            args = ["merge", "--ff-only", sha]
        else:
            args = ["merge", "--no-ff", "--no-edit", "--no-verify", "-m", f"Merge {integ} (MAO's finished work)", sha]
        try:
            r = self._merge(p, who, args)
        except (subprocess.TimeoutExpired, OSError):
            r = None
        clash: list[str] = []
        new, landed = "", False
        try:                                           # from here on the folder may be half-written: any trouble
            new = pm._out(_git(["rev-parse", "-q", "--verify", "HEAD^{commit}"], p))   # ends in put_back
            if r is not None and r.returncode == 0 and new and new != before:
                landed = True
            elif r is not None and new != before and self._ours(p, new, before, sha, ins["ff"]) and not _dirty(p):
                landed = True                          # it did land; git only complained afterwards
            else:
                gd = pm._git_dir(p)
                if gd is not None and (gd / "MERGE_HEAD").exists():   # stopped at a conflict: which files, first
                    out = _git(["diff", "--name-only", "-z", "--diff-filter=U"], p).stdout
                    clash = [_path(x) for x in out.split(b"\0") if x]
        except Exception:   # noqa: BLE001  (whatever it was: the folder is put back below)
            traceback.print_exc()
        if landed:
            return self._landed(user, project, p, ins, new)
        said = "" if r is None else (r.stdout + b"\n" + r.stderr).decode("utf-8", "replace")
        print(f"MAO: landing in {p} did not go through: {said.strip()[-600:] or 'timeout'}", flush=True)
        left = self.put_back(p, ref, before, sha, plan, sorted(fresh), ins["ff"])
        if left:
            raise _Fail(LEFT.format(names=_names(left)))
        if r is None:
            raise _Fail(FAILED_SLOW)
        low = said.lower()
        named = [ln.strip() for ln in said.splitlines() if ln.startswith("\t") and ln.strip()]
        if clash or "conflict" in low:
            raise _fail("conflict", clash or named)
        if "untracked working tree files would be overwritten" in low:
            raise _fail("untracked", named)
        if "would be overwritten" in low:
            raise _fail("dirty", named)
        raise _Fail(FAILED_IN_USE if any(w in low for w in BUSY_WORDS) else FAILED)

    @staticmethod
    def _merge(p: Path, who: list[str], args: list[str]):
        """The one command that changes the user's folder (tests replace it to fail halfway)."""
        safe = ["-c", "commit.gpgsign=false", "-c", "merge.autoStash=false"]
        return _git([*who, *safe, args[0], "--no-overwrite-ignore", "--no-verify-signatures", "--no-stat",
                     "--no-progress", *args[1:]], p, timeout=MERGE_TIMEOUT, write=True)

    def _ours(self, p: Path, cur: str, before: str, sha: str, ff: bool) -> bool:
        """HEAD is at the commit this landing makes: the integration sha (fast-forward), or a merge of it."""
        if not cur:
            return False
        if ff:
            return cur == sha
        r = _git(["rev-list", "--parents", "-n", "1", cur], p)
        return r.returncode == 0 and pm._out(r).split()[1:] == [before, sha]

    @staticmethod
    def _still_there(p: Path, ins: dict) -> list[str]:
        """Files the landing deletes that are still in the folder: git only warns when it cannot delete one (on
        Windows, a file another program holds open without letting anybody delete it)."""
        kept = {e["path"].casefold() for e in ins["plan"] if e["r"] is not None} if ins.get("fold") else set()
        out = []
        for e in ins["plan"]:
            q = p / e["path"]
            if e["r"] is None and os.path.lexists(q) and (os.path.islink(q) or not os.path.isdir(q)) \
                    and e["path"].casefold() not in kept:   # not the new name of a case-only rename
                out.append(e["path"])
        return out

    def _landed(self, user, project: dict, p: Path, ins: dict, new: str) -> dict:
        try:
            still = self._still_there(p, ins)
        except OSError:
            still = []
        files = len(ins["plan"]) - len(still)
        try:   # the files are in; a record that cannot be written must not say otherwise
            self.app.db.x("INSERT INTO landings(project_id, path, user_id, at, branch, integ, from_sha, to_sha, "
                          "integ_sha, files, kind) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (project["id"], str(p), user["id"], time.time(), ins["current"], ins["integ"], ins["head"],
                           new, ins["sha"], files, "ff" if ins["ff"] else "merge"))
        except sqlite3.Error:
            traceback.print_exc()
        self.forget(p)
        try:
            land = self.state(project, user=user)
        except Exception:   # the page asks again on its next poll
            traceback.print_exc()
            land = None
        return {"ok": True, "files": files, "kind": "ff" if ins["ff"] else "merge", "land": land,
                "note": STILL_THERE.format(names=_names(still)) if still else ""}

    # -- after a merge that did not go through ---------------------------------------------------------
    def put_back(self, p: Path, ref: str, before: str, sha: str, plan: list[dict], fresh: list[str],
                 ff: bool) -> list[str]:
        """The folder as it was before this landing. Only what this merge wrote is undone: an index entry or a path
        is put back only when it holds exactly the merge's version (its content, kind and exec bit), so nothing
        anybody changed meanwhile is lost (no reset --hard, no clean). Writing the files back gets the merge's
        time. Then the folder is looked at again: returns the files that are not as they were (empty: HEAD, the
        index and every file of the landing exactly as before)."""
        paths = [e["path"] for e in plan]

        def git(args: list[str], write: bool = False):
            return _git(args, p, timeout=MERGE_TIMEOUT, write=write)
        try:
            gd = pm._git_dir(p)
            if gd is None:
                return paths
            if (gd / "MERGE_HEAD").exists():
                git(["merge", "--abort"], write=True)
            cur = pm._out(git(["rev-parse", "-q", "--verify", "HEAD^{commit}"]))
            if pm._out(git(["symbolic-ref", "-q", "HEAD"])) != ref:
                return paths                          # someone switched branches meanwhile: not ours to judge
            if cur != before:
                if not self._ours(p, cur, before, sha, ff):
                    return paths
                _ok(git(["update-ref", "-m", "MAO: undo an unfinished landing", ref, before, cur], write=True),
                    "update-ref")
        except Exception:   # noqa: BLE001  (could not even look: never claim it is as it was)
            traceback.print_exc()
            return paths
        left = paths
        for pause in (0, 1):                          # a file open in another program may be free a moment later
            time.sleep(pause)
            try:
                self._restore(p, before, plan)
            except Exception:   # noqa: BLE001  (what could go back did; the look below tells the rest)
                traceback.print_exc()
            try:
                left = self._left(p, plan)
            except Exception:   # noqa: BLE001
                traceback.print_exc()
                return paths
            if not left:
                break
        try:
            if (gd / "MERGE_HEAD").exists():          # the abort itself stopped at such a file
                if left:
                    return left
                git(["merge", "--abort"], write=True)
                for name in MERGE_STATE:              # all of it ours: no merge was going on before the landing
                    if (gd / name).exists():
                        (gd / name).unlink()
                left = self._left(p, plan)
        except Exception:   # noqa: BLE001
            traceback.print_exc()
            return left or paths
        for d in sorted(fresh, key=lambda x: -x.count("/")):   # the folders it made for new files, if empty now
            try:
                (p / d).rmdir()
            except OSError:
                pass
        return left

    def _left(self, p: Path, plan: list[dict]) -> list[str]:
        """The landing's files that differ from HEAD (staged or not, a conflict left behind), and its new files that
        are still there (a folder there is the user's own, put back where the merge would have made a file)."""
        changed = set(_dirty(p, MERGE_TIMEOUT))
        out = set()
        for e in plan:
            q = p / e["path"]
            if e["path"] in changed or (e["h"] is None and os.path.lexists(q)
                                        and (os.path.islink(q) or not os.path.isdir(q))):
                out.add(e["path"])
        return sorted(out)

    def _restore(self, p: Path, before: str, plan: list[dict]) -> None:
        paths = [e["path"] for e in plan]
        idx: dict[str, str] = {}
        unmerged: set[str] = set()
        for part in _chunks(paths):
            out = _ok(_git(["ls-files", "-s", "-z", "--", *_lit(part)], p, timeout=MERGE_TIMEOUT), "ls-files").stdout
            for item in out.split(b"\0"):
                if b"\t" in item:
                    meta, name = item.split(b"\t", 1)
                    mode, blob, stage = meta.decode("ascii", "replace").split()[:3]
                    if stage == "0":
                        idx[_path(name)] = blob
                    else:                             # a conflict this merge left (no merge was going on before)
                        unmerged.add(_path(name))
        disk, fl = _disk(p, plan), _flags(p)
        reset, restore, remove, emptied = [], [], [], []
        for e in plan:
            path, h, rv = e["path"], e["h"], e["r"]
            i = idx.get(path)
            if path in unmerged:
                reset.append(path)
            elif i not in (h, rv):
                continue                              # staged by somebody meanwhile: theirs
            elif i != h:
                reset.append(path)
            if GITLINK in (e["h_mode"], e["r_mode"]):
                continue                              # a submodule: only its entry in the index
            d = disk[path]
            if _holds(d, h, e["h_mode"], fl):
                continue                              # as it was
            if h is None:
                if d[0] != "dir" and _holds(d, rv, e["r_mode"], fl):
                    remove.append(path)
            elif d[0] == "none":
                restore.append(path)                  # the merge removed (or could not rewrite) it
            elif d[0] == "dir":
                emptied.append(path)                  # the merge made a folder where the user's file was
            elif _holds(d, rv, e["r_mode"], fl):
                restore.append(path)                  # the merge's version, a link or an exec bit included
        self._each(p, ["reset", "-q", before], reset)
        for path in remove:
            try:
                os.unlink(p / path)
            except OSError:
                pass
        for path in sorted(emptied, key=lambda x: -x.count("/")):
            for d, _dirs, _files in os.walk(p / path, topdown=False):
                try:
                    os.rmdir(d)                       # only empty folders: what the merge made in it went above
                except OSError:
                    pass
            if not os.path.lexists(p / path):
                restore.append(path)
        self._each(p, ["checkout", before], restore)

    @staticmethod
    def _each(p: Path, args: list[str], paths: list[str]) -> None:
        """git <args> -- <paths>, a chunk at a time, each with the merge's time; a chunk that fails or runs out of
        time is done again one path at a time (one file in use or slow to write must not keep the others back),
        for at most twice that time."""
        def run(part: list[str]) -> bool:
            try:
                return _git([*args, "--", *_lit(part)], p, timeout=MERGE_TIMEOUT, write=True).returncode == 0
            except subprocess.TimeoutExpired:
                return False
        for part in _chunks(paths):
            if run(part) or len(part) == 1:
                continue
            until = time.monotonic() + 2 * MERGE_TIMEOUT
            for one in part:
                if time.monotonic() > until:
                    break
                run([one])
