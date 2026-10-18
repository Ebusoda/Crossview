"""The project registry of the 项目 page, and getting a folder ready for orch (admin only).

Projects are kept in the setting `code.projects` as [{"id", "name", "path", "added_at"}] with resolved paths.
Adding a folder follows orch's own rules (setup notes §2): refuse places that are not one project (a whole disk,
the home folder, personal folders, Crossview itself, orch's worktrees, a subfolder of another repository), then
create / git init / first commit / `orch init` as needed. Big folders (node_modules ...) and secret-looking
files never reach the version the agents work on: they go into the repository's own info/exclude before the
first `git add`. Every git call here runs without fsmonitor and with a timeout; commits skip the user's hooks.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
import tomllib
from pathlib import Path

from orch import i18n
from orch import workspace as ws
from orch.config import DEFAULT_CONFIG_TOML, Config
from orch.config_en import DEFAULT_CONFIG_TOML_EN
from orch.proc import child_env, resolve_command
from orch.scheduler import OrchError, init_project

from .db import data_dir

ROOT = Path(__file__).resolve().parents[1]          # Crossview's own repository
SETTING = "code.projects"
LAST_ID = "code.projects.last_id"                     # ids are never reused: old jobs keep pointing at theirs
NAME_MAX = 40
BIG_FILE = 50 * 1024 * 1024
WARN_FILES, MAX_FILES = 5_000, 50_000
MAX_LINES = 500_000                                  # git ls-files lines read at most (files in node_modules too)
SURVEY_SECONDS = 3
CHECK_TIMEOUT, STATE_TIMEOUT, ADD_TIMEOUT = 20, 10, 1800
STATE_TTL = 30
BROWSE_MAX = 300
HEAVY = ("node_modules", ".venv", "venv")
SYSTEM_DIRS = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
               "system volume information"}
PERSONAL = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")
ONEDRIVE_ENV = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
ONEDRIVE_SUB = ("Desktop", "Documents", "Pictures", "Downloads", "Videos", "Music",   # OneDrive folder backup
                "桌面", "文档", "图片", "下载", "视频", "音乐",                            # names on a Chinese PC
                "デスクトップ", "ドキュメント", "画像", "ダウンロード", "ビデオ", "ミュージック")   # on a Japanese PC
KNOWN_FOLDERS = ("B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "FDD39AD0-238F-46AF-ADB4-6C85480369C7",   # Desktop, Documents
                 "374DE290-123F-4565-9164-39C4925E467B", "33E28130-4E1E-4676-835A-98395C3BC3BB",   # Downloads, Pictures
                 "18989B1D-99B5-455B-841C-AB7C74E4DDFC", "4BD8D571-6D19-48D3-BE97-422220080E43")   # Videos, Music
# secret-looking files kept out of every commit Crossview makes here (the survey's exact names come on top of these)
SECRET_LINES = (".env", ".env.*", "!.env.example", "!.env.sample", "!.env.template", "!.env.dist", "*.key", "*.pem",
                "*.pfx", "*.p12", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "!id_rsa*.pub", "!id_dsa*.pub",
                "!id_ecdsa*.pub", "!id_ed25519*.pub")
MAO_IGNORED = ("playground", "probe")                 # .gitignore'd in Crossview's repo (used if git cannot tell)
EXCLUDE_HEAD = "# added by Crossview"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_prep = threading.RLock()                             # one preparation at a time: git identity is process-wide

PROBLEMS = {
    "not_absolute": "请填写完整的文件夹路径（从盘符或 / 开始）。",
    "drive_root": "不能把整个磁盘当成项目，请选里面的一个文件夹。",
    "home_folder": "这是你的个人文件夹（或者包含它），里面有很多私人文件，不能交给 AI。请选一个专门放项目的文件夹。",
    "personal_folder": "这是放私人文件的地方（桌面、文档、下载等），不能整个交给 AI。请在里面新建一个专门的项目文件夹。",
    "mao_folder": "这是 Crossview 自己的文件夹，不能当成项目。项目可以放在 Crossview 的 playground 文件夹里。",
    "worktree": "这是 Crossview 给 AI 干活用的临时文件夹，不能当成项目。",
    "inside_repo": "这个文件夹在另一个项目（git 仓库）里面：{root}。请直接添加那个文件夹。",
    "no_git": "这台电脑还没有安装 git。先安装 git，再来添加项目。",
    "git_error": "git 打不开这个文件夹：{detail}",
    "missing": "找不到这个文件夹，它的上一级文件夹也不存在。",
    "not_dir": "这是一个文件，不是文件夹。",
    "registered": "这个文件夹已经添加过了，名字是「{name}」。",
    "contains_project": "这个文件夹里面已经有项目「{name}」了，请直接用那个项目。",
    "many_files": "这个文件夹里的文件太多了（超过 {n} 个），不适合交给 AI。请选一个小一点的文件夹。",
}
WARNINGS = {
    "dirty": "有 {n} 处改动还没有保存成版本。AI 会从上一次保存的版本开始做，看不到这些改动。",
    "big_files": "这些大文件或文件夹不会放进给 AI 用的版本：{names}",
    "secrets": "这些像是密码或密钥的文件不会放进给 AI 用的版本：{names}",
    "many_files": "这个文件夹里的文件很多（{n} 个以上），第一次准备会慢一些。",
    "nested_repos": "这些子文件夹自己也是 git 仓库，AI 看不到里面的文件：{names}",
}
TIMEOUT_TEXT = "检查超时：git {s} 秒都没有回应，请稍后再试。"
BROKEN_GIT = "这个文件夹里的 .git 坏了，git 打不开它。"
SLOW_TEXT = "整理文件花的时间太长，没有完成。可以先把不需要的大文件夹移走再试。"
LINK_MISSING = "找不到这个文件夹，请检查路径。"


# -- small helpers ----------------------------------------------------------------------------------
def clean_input(raw) -> str:
    """A pasted path: surrounding spaces and one pair of quotes (Windows "Copy as path") removed, ~ expanded."""
    s = str(raw or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return os.path.expanduser(s) if s.startswith("~") else s


def _resolve(path) -> Path:
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return Path(os.path.abspath(path))


def _key(path) -> str:
    return os.path.normcase(str(_resolve(path)))


def _under(p: Path, base: Path) -> bool:
    """p is base or inside it (case-insensitive where the system is)."""
    a, b = os.path.normcase(str(p)), os.path.normcase(str(base))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def _secret(name: str) -> bool:
    n = name.lower()
    if n == ".env" or (n.startswith(".env.") and n[5:] not in ("example", "sample", "template", "dist")):
        return True
    if n.endswith((".key", ".pem", ".pfx", ".p12")):
        return True
    return n.startswith(("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")) and not n.endswith(".pub")


def _names(items: list[str], n: int = 5) -> str:
    return "、".join(items[:n]) + (f" 等 {len(items)} 个" if len(items) > n else "")


def _pattern(rel: str) -> str:
    """An info/exclude line matching exactly this file (anchored, wildcards escaped)."""
    s = re.sub(r"([\\*?\[\]!#])", r"\\\1", rel)
    return "/" + (s[:-1] + "\\ " if s.endswith(" ") else s)


# -- git ----------------------------------------------------------------------------------------------
def _env(read_only: bool) -> dict:
    return child_env({"GIT_TERMINAL_PROMPT": "0", **({"GIT_OPTIONAL_LOCKS": "0"} if read_only else {})})


def _git_dir(cwd: Path) -> Path | None:
    g = Path(cwd) / ".git"
    if g.is_dir():
        return g
    if g.is_file():
        m = re.match(r"gitdir:\s*(.+)", g.read_text(encoding="utf-8", errors="replace").strip())
        if m:
            d = Path(m.group(1).strip())
            return d if d.is_absolute() else (Path(cwd) / d).resolve()
    return None


def _index_lock(cwd: Path) -> Path | None:
    try:
        gd = _git_dir(cwd)
    except OSError:
        return None
    return gd / "index.lock" if gd else None


def _drop_lock(lock: Path | None, start: float) -> None:
    """A git call of ours that writes failed or was stopped: remove the index.lock it left (not an older one)."""
    try:
        if lock and lock.exists() and lock.stat().st_mtime >= start - 2:   # FAT keeps 2-second times
            lock.unlink()
    except OSError:
        pass


def _git(args: list[str], cwd: Path, timeout: float | None = None, read_only: bool = True):
    """git with fsmonitor off and a timeout (subprocess.TimeoutExpired); never raises for an exit code.
    A read-only call never takes the index lock (GIT_OPTIONAL_LOCKS=0), so a lock found then is another program's:
    only a writing call cleans up after itself, and only a lock that was not there before it started."""
    start = time.time()
    lock = None if read_only else _index_lock(cwd)
    if lock is not None and lock.exists():
        lock = None
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.quotepath=off", *args], cwd=str(cwd),
                           capture_output=True, timeout=timeout or CHECK_TIMEOUT, stdin=subprocess.DEVNULL,
                           env=_env(read_only), creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        _drop_lock(lock, start)
        raise
    if r.returncode != 0:
        _drop_lock(lock, start)
    return r


def _out(r) -> str:
    return r.stdout.decode("utf-8", errors="replace").strip()


def _err(r) -> str:
    return (r.stderr or r.stdout).decode("utf-8", errors="replace").strip()[:300]


def _ok(r, what: str):
    if r.returncode != 0:
        raise ws.GitError(f"git {what}: {_err(r)}")
    return r


def _stream(args: list[str], cwd: Path, take) -> None:
    """Feed each NUL-separated item git prints to take() until it returns False (then git is stopped)."""
    fired = threading.Event()
    with tempfile.TemporaryFile() as errf:
        proc = subprocess.Popen(["git", "-c", "core.fsmonitor=false", *args], cwd=str(cwd), stdout=subprocess.PIPE,
                                stderr=errf, stdin=subprocess.DEVNULL, env=_env(True), creationflags=NO_WINDOW)
        timer = threading.Timer(CHECK_TIMEOUT, lambda: (fired.set(), proc.kill()))
        timer.start()
        stopped, buf = False, b""
        try:
            while not stopped:
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    break
                *items, buf = (buf + chunk).split(b"\0")
                for it in items:
                    if not take(it.decode("utf-8", errors="replace")):
                        stopped = True
                        break
        finally:
            if stopped:
                proc.kill()
            proc.stdout.close()
            proc.wait()
            timer.cancel()
        if fired.is_set():
            raise subprocess.TimeoutExpired(args, CHECK_TIMEOUT)
        if not stopped and proc.returncode != 0:
            errf.seek(0)
            raise ws.GitError(errf.read().decode("utf-8", errors="replace").strip()[:300])


def _ignored(top: Path, p: Path) -> bool | None:
    """Does the repository at `top` ignore folder p? None = git could not tell."""
    try:
        rel = os.path.relpath(p, top)
    except ValueError:                             # another drive
        return False
    if rel.startswith("..") or os.path.isabs(rel):
        return False
    r = _git(["check-ignore", "-q", "--", rel.replace("\\", "/") + "/"], top)
    return {0: True, 1: False}.get(r.returncode)


# -- what the first commit would take -------------------------------------------------------------------
def _new_survey() -> dict:
    return {"count": 0, "more": False, "timed_out": False, "heavy": [], "big": [], "secrets": [], "nested": []}


def _file(sv: dict, rel: str, size) -> bool:
    sv["count"] += 1
    if sv["count"] > MAX_FILES:
        sv["more"] = True
        return False
    if _secret(rel.rsplit("/", 1)[-1]):
        sv["secrets"].append(rel)
    try:
        if size() > BIG_FILE:
            sv["big"].append(rel)
    except OSError:
        pass
    return True


def _survey_git(p: Path, cached: bool) -> dict:
    """Files `git add -A` would take in a repository: `git ls-files --others` (+ --cached before the first commit)."""
    sv, lines = _new_survey(), [0]

    def take(rel: str) -> bool:
        lines[0] += 1
        if lines[0] > MAX_LINES:
            sv["timed_out"] = True
            return False
        if not rel:
            return True
        if rel.endswith("/"):                     # a folder with its own .git
            sv["nested"].append(rel.rstrip("/"))
            return True
        parts = rel.split("/")
        if parts[0] in (".agents", ".task"):
            return True
        for i, part in enumerate(parts[:-1]):
            if part.lower() in HEAVY:
                d = "/".join(parts[:i + 1]) + "/"
                if d not in sv["heavy"]:
                    sv["heavy"].append(d)
                return True
        return _file(sv, rel, lambda: os.lstat(p / rel).st_size)

    _stream(["ls-files", "-z", "--others", "--exclude-standard", *(["--cached"] if cached else [])], p, take)
    return sv


def _survey_dir(p: Path) -> dict:
    """Files `git add -A` would take in a folder that is not a repository yet (stops after MAX_FILES or 3 s)."""
    sv, stack, deadline = _new_survey(), [(str(p), "")], time.monotonic() + SURVEY_SECONDS
    while stack:
        d, rel = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for i, e in enumerate(entries):
            if i % 500 == 0 and time.monotonic() > deadline:
                sv["timed_out"] = True
                return sv
            r = rel + e.name
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if not is_dir:
                if not _file(sv, r, lambda e=e: e.stat(follow_symlinks=False).st_size):
                    return sv
            elif e.name == ".git" or (not rel and e.name in (".agents", ".task")):
                continue
            elif e.name.lower() in HEAVY:
                sv["heavy"].append(r + "/")
            elif os.path.lexists(os.path.join(e.path, ".git")):
                sv["nested"].append(r)
            else:
                stack.append((e.path, r + "/"))
    return sv


def _status(p: Path) -> list[tuple[str, str]]:
    """Uncommitted changes as (XY, path), Crossview's own folders left out."""
    r = _ok(_git(["status", "--porcelain", "-z", "--untracked-files=normal"], p), "status")
    items, out = r.stdout.decode("utf-8", errors="replace").split("\0"), []
    i = 0
    while i < len(items):
        it = items[i]
        i += 1
        if len(it) < 4:
            continue
        if it[0] in "RC":                          # renamed / copied: the old name follows
            i += 1
        if not it[3:].startswith((".agents/", ".task/")):
            out.append((it[:2], it[3:]))
    return out


# -- the registry ----------------------------------------------------------------------------------------
class Projects:
    def __init__(self, app):
        self.app = app
        self.locks: dict[str, threading.Lock] = {}     # resolved path -> one orch run at a time (shared by jobs)
        self._guard = threading.Lock()
        self._states: dict[str, tuple[float, dict]] = {}
        self._migrate()

    # -- stored list -----------------------------------------------------------------------------------
    def _items(self) -> list[dict]:
        return [dict(it) for it in (self.app.db.setting(SETTING, []) or []) if isinstance(it, dict)]

    def _save(self, items: list[dict]) -> None:
        self.app.db.set_setting(SETTING, items)
        self._states.clear()

    def _next_id(self, taken: list[dict]) -> int:
        ids = [it["id"] for it in taken if isinstance(it.get("id"), int)]
        nid = max([int(self.app.db.setting(LAST_ID, 0) or 0), *ids]) + 1
        self.app.db.set_setting(LAST_ID, nid)
        return nid

    def _migrate(self) -> None:
        """Older lists had only name and path: give them ids (max+1) and resolved paths."""
        with self._guard:
            raw = self.app.db.setting(SETTING, []) or []
            items = [it for it in self._items() if str(it.get("path") or "").strip()]
            for it in items:
                if not isinstance(it.get("id"), int):
                    it["id"] = self._next_id(items)
                it["path"] = str(_resolve(str(it["path"]).strip()))
                it["name"] = str(it.get("name") or Path(it["path"]).name)[:NAME_MAX]
                it.setdefault("added_at", time.time())
            if items != raw:
                self._save(items)

    def list(self) -> list[dict]:
        return self._items()

    def get(self, pid) -> dict:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            raise ValueError("没有这个项目") from None
        for it in self._items():
            if it.get("id") == pid:
                return it
        raise ValueError("没有这个项目")

    def by_path(self, path) -> dict | None:
        k = _key(path)
        return next((it for it in self._items() if _key(it["path"]) == k), None)

    def lock_for(self, path) -> threading.Lock:
        with self._guard:
            return self.locks.setdefault(str(_resolve(path)), threading.Lock())

    def set_all(self, items) -> list[dict]:
        """The old replace-the-list call: each folder must already be an orch project; ids kept by name or path."""
        if not isinstance(items, list):
            raise ValueError("项目列表的格式不对")
        with self._guard:
            old = self._items()
            clean, names, keys = [], set(), set()
            for it in items:
                if not isinstance(it, dict):
                    continue
                name, path = str(it.get("name", "")).strip()[:NAME_MAX], str(it.get("path", "")).strip()
                if not name or not path or name in names:
                    continue
                p = _resolve(path)
                if not (p / ".agents" / "orch.toml").is_file():
                    raise ValueError(f"{path} 还没有准备好：请在项目页用「添加项目」添加它。")
                k = os.path.normcase(str(p))
                if k in keys:
                    continue
                used = {c["id"] for c in clean}
                prev = next((o for o in old if o["id"] not in used and o["name"] == name), None) or \
                    next((o for o in old if o["id"] not in used and os.path.normcase(o["path"]) == k), None)
                pid = prev["id"] if prev else self._next_id(old + clean)
                added = prev.get("added_at", time.time()) if prev else time.time()
                names.add(name)
                keys.add(k)
                clean.append({"id": pid, "name": name, "path": str(p), "added_at": added})
            self._save(clean)
        for proj in clean:
            self._adopt(proj)
        return [dict(c) for c in clean]

    def rename(self, pid, name: str) -> dict:
        with self._guard:
            proj = self.get(pid)
            name = self._free_name(name, proj["id"])
            items = self._items()
            for it in items:
                if it["id"] == proj["id"]:
                    it["name"] = name
                    proj = it
            self._save(items)
        return dict(proj)

    def remove(self, pid) -> None:
        """Unregister only; the folder and its files stay as they are."""
        proj = self.get(pid)
        jobs = getattr(self.app, "jobs", None)
        if jobs is not None:
            if hasattr(jobs, "end_missing"):
                jobs.end_missing(proj["id"])   # jobs of a folder that is gone end here: nothing is left to finish
            if jobs.has_open(proj["id"]):
                raise ValueError("这个项目还有没做完的任务，先把它做完或点「不做了」")
        with self._guard:
            self._save([it for it in self._items() if it["id"] != proj["id"]])

    def _free_name(self, name: str, own_id: int = 0) -> str:
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:NAME_MAX]
        if not name:
            raise ValueError("请给项目起个名字")
        if any(it["name"] == name and it["id"] != own_id for it in self._items()):
            raise ValueError(f"已经有一个叫「{name}」的项目了，换个名字吧")
        return name

    def _suggest(self, p: Path) -> str:
        base = (p.name or "项目")[:NAME_MAX]
        taken, name, n = {it["name"] for it in self._items()}, base, 1
        while name in taken:
            n += 1
            name = f"{base[:NAME_MAX - len(str(n)) - 1]} {n}"
        return name

    def _adopt(self, proj: dict) -> None:
        jobs = getattr(self.app, "jobs", None)
        if jobs is not None and hasattr(jobs, "adopt"):
            jobs.adopt(dict(proj))

    # -- ready or not (cheap; for every list poll) -------------------------------------------------------
    def state(self, path) -> dict:
        key, now = str(path), time.monotonic()
        hit = self._states.get(key)
        if hit and now - hit[0] < STATE_TTL:
            return dict(hit[1])
        p = Path(path)
        if not p.is_dir():
            st = {"ready": False, "problem": "找不到这个文件夹，可能被移走或删掉了。"}
        elif not (p / ".agents" / "orch.toml").is_file():
            st = {"ready": False, "problem": "这个文件夹还没有准备好（缺少 .agents/orch.toml），请重新添加。"}
        else:
            try:
                r = _git(["rev-parse", "--git-dir"], p, timeout=STATE_TIMEOUT)
                st = {"ready": r.returncode == 0, "problem": f"git 打不开这个项目：{_err(r)}" if r.returncode else ""}
            except subprocess.TimeoutExpired:
                st = {"ready": False, "problem": "git 太久没有回应，请稍后再看。"}
            except OSError as e:
                st = {"ready": False, "problem": f"git 打不开这个项目：{e}"}
        self._states[key] = (now, st)
        return dict(st)

    # -- checking a folder ---------------------------------------------------------------------------------
    def check(self, raw) -> dict:
        with _prep:
            return self._check(raw)[0]

    def _personal(self, home: Path) -> list[Path]:
        out = [home / n for n in PERSONAL] + _known_folders()
        for var in ONEDRIVE_ENV:
            if os.environ.get(var):
                od = _resolve(os.environ[var])
                out += [od] + [od / n for n in ONEDRIVE_SUB]
        return out

    def _location(self, p: Path) -> str:
        """The problem with this place, judged from the path alone ('' = none)."""
        home = _resolve(os.path.expanduser("~"))
        if p.parent == p:
            return "drive_root"
        if _under(home, p):
            return "home_folder"
        if any(_under(x, p) for x in self._personal(home)):
            return "personal_folder"
        if _under(ROOT, p) or _under(p, _resolve(data_dir())):
            return "mao_folder"
        if any(part.lower().endswith(".worktrees") for part in p.parts):
            return "worktree"
        return ""

    def _check(self, raw) -> tuple[dict, dict]:
        s = clean_input(raw)
        g = {"installed": True, "repo": False, "root": "", "has_commits": False, "branch": "", "dirty": [],
             "dirty_count": 0}
        res = {"path": s, "exists": False, "name": "", "registered": "", "git": g, "orch_ready": False, "steps": [],
               "problems": [], "warnings": [], "can_create": False}
        extra = {"exclude": [], "tracked_secrets": []}

        def problem(code: str, text: str = "", **kw) -> None:
            if code not in {x["code"] for x in res["problems"]}:
                res["problems"].append({"code": code, "text": text or PROBLEMS[code].format(**kw)})

        def warn(code: str, **kw) -> None:
            res["warnings"].append({"code": code, "text": WARNINGS[code].format(**kw)})

        if not s or not Path(s).is_absolute():
            problem("not_absolute")
            return res, extra
        p = _resolve(s)
        exists = p.exists()
        res.update(path=str(p), exists=exists, name=self._suggest(p))
        loc = self._location(p)
        if loc:                                  # the place alone rules it out; nothing else matters
            problem(loc)
            return res, extra
        reg = self.by_path(p)
        if reg:
            res["registered"] = reg["name"]
            problem("registered", name=reg["name"])
        inner = next((it for it in self._items() if _under(_resolve(it["path"]), p) and _key(it["path"]) != _key(p)),
                     None)
        if inner:
            problem("contains_project", name=inner["name"])
        if exists and not p.is_dir():
            problem("not_dir")
        if not exists and not p.parent.is_dir():
            problem("missing")
        if not resolve_command("git"):
            g["installed"] = False
            problem("no_git")
        if res["problems"]:
            return res, extra
        try:
            self._git_place(p, exists, problem, g)
            if res["problems"]:
                return res, extra
            if not exists:
                res["can_create"] = True
                res["steps"] = ["create_folder", "git_init", "first_commit", "orch_init", "register"]
                return res, extra
            self._git_state(p, g, warn, problem, extra)
        except subprocess.TimeoutExpired:
            problem("git_error", text=TIMEOUT_TEXT.format(s=CHECK_TIMEOUT))
        except (ws.GitError, OSError) as e:
            problem("git_error", detail=str(e)[:300])
        if res["problems"]:
            return res, extra
        res["orch_ready"] = (p / ".agents" / "orch.toml").is_file()
        res["steps"] = ([] if g["repo"] else ["git_init"]) + ([] if g["has_commits"] else ["first_commit"]) + \
            (["commit_changes"] if g["has_commits"] and g["dirty_count"] else []) + ["orch_init", "register"]
        return res, extra

    def _git_place(self, p: Path, exists: bool, problem, g: dict) -> None:
        """Location rules that need git, run in p (or in its parent when p does not exist yet)."""
        anchor = p if exists else p.parent
        if _under(p, ROOT):                      # inside Crossview: only folders its git ignores (playground/...)
            ign = _ignored(ROOT, p)
            if ign is None:
                ign = Path(os.path.relpath(p, ROOT)).parts[0].lower() in MAO_IGNORED
            if not ign:
                return problem("mao_folder")
        r = _git(["rev-parse", "--show-toplevel"], anchor)
        top = _resolve(_out(r)) if r.returncode == 0 and _out(r) else None
        if exists and os.path.lexists(p / ".git") and (top is None or _key(top) != _key(p)):
            return problem("git_error", detail=_err(r)) if _err(r) else problem("git_error", text=BROKEN_GIT)
        if top is None:
            return
        r = _git(["rev-parse", "--git-dir", "--git-common-dir"], anchor)
        dirs = [_resolve(anchor / d) for d in _out(r).splitlines()] if r.returncode == 0 else []
        if len(dirs) == 2 and _key(dirs[0]) != _key(dirs[1]):
            return problem("worktree")
        if _key(top) == _key(p):
            g.update(repo=True, root=str(top))
        elif not _ignored(top, p):               # an ignored subfolder is a folder of its own
            problem("inside_repo", root=str(top))

    def _git_state(self, p: Path, g: dict, warn, problem, extra: dict) -> None:
        """Commits, branch, uncommitted changes, and what a first commit would take (with warnings)."""
        first = True
        if g["repo"]:
            g["has_commits"] = _git(["rev-parse", "--verify", "-q", "HEAD"], p).returncode == 0
            r = _git(["symbolic-ref", "--short", "-q", "HEAD"], p)
            g["branch"] = _out(r) if r.returncode == 0 else "HEAD"
            first = not g["has_commits"]
        if not first:
            changes = _status(p)
            g["dirty"] = [f"{xy} {path}" for xy, path in changes[:20]]
            g["dirty_count"] = len(changes)
            if not changes:
                return
            warn("dirty", n=len(changes))
            extra["tracked_secrets"] = [path for xy, path in changes
                                        if xy != "??" and _secret(path.rstrip("/").rsplit("/", 1)[-1])]
            sv = _survey_git(p, cached=False)
        else:
            sv = _survey_git(p, cached=True) if g["repo"] else _survey_dir(p)
            if sv["more"]:
                problem("many_files", n=MAX_FILES)
            elif sv["count"] > WARN_FILES or sv["timed_out"]:
                warn("many_files", n=max(WARN_FILES, sv["count"] // 1000 * 1000))
        if sv["heavy"] or sv["big"]:
            warn("big_files", names=_names(sv["heavy"] + sv["big"]))
        secrets = sv["secrets"] + extra["tracked_secrets"]
        if secrets:
            warn("secrets", names=_names(secrets))
        if sv["nested"]:
            warn("nested_repos", names=_names(sv["nested"]))
        # nested repositories too: `git add -A` would make empty links of them, or fail on one without commits
        extra["exclude"] = [_pattern(x) for x in sv["big"] + sv["secrets"]] + [_pattern(x) + "/" for x in sv["nested"]]

    # -- adding a folder -----------------------------------------------------------------------------------
    def add(self, raw, name: str = "", create: bool = False, commit_changes: bool = False, lang: str = "zh") -> dict:
        """Check again, then create / git init / first commit / save the user's changes / orch init / register."""
        lang = "en" if str(lang or "").lower().startswith("en") else "zh"
        with _prep:
            res, extra = self._check(raw)
            if res["problems"]:
                raise ValueError(res["problems"][0]["text"])
            if not res["exists"] and not create:
                raise ValueError("这个文件夹还不存在。要新建它，请勾选「新建这个文件夹」。")
            name = self._free_name(str(name or "").strip() or res["name"])
            p, g = Path(res["path"]), res["git"]
            first, save = not g["has_commits"], bool(commit_changes and g["has_commits"] and g["dirty_count"])
            try:
                if not res["exists"]:
                    p.mkdir()
                if not g["repo"]:
                    if _git(["init", "-q", "-b", "main"], p, read_only=False).returncode != 0:   # git < 2.28
                        _ok(_git(["init", "-q"], p, read_only=False), "init")
                        _ok(_git(["symbolic-ref", "HEAD", "refs/heads/main"], p, read_only=False), "symbolic-ref")
                self._excludes(p, [f"{h}/" for h in HEAVY] + list(SECRET_LINES) + extra["exclude"]
                               if first or save else [])
                cfg = p / ".agents" / "orch.toml"
                if not cfg.exists():
                    cfg.parent.mkdir(exist_ok=True)
                    cfg.write_text(DEFAULT_CONFIG_TOML if lang == "zh" else DEFAULT_CONFIG_TOML_EN, encoding="utf-8")
                if first or save:
                    self._commit(p, first, extra["tracked_secrets"] if save else [])
                with i18n.using(lang):
                    init_project(p)
            except subprocess.TimeoutExpired:
                raise ValueError(SLOW_TEXT) from None
            except tomllib.TOMLDecodeError as e:
                raise ValueError(f"项目里的 .agents/orch.toml 写得不对：{e}") from None
            except (OrchError, ws.GitError, OSError) as e:
                raise ValueError(f"准备项目时出错了：{str(e)[:400]}") from None
            proj = self._register(name, p)
        self._adopt(proj)
        return proj

    def link(self, raw, name: str = "") -> dict:
        """Register an existing folder for discussions only (关联项目, brief carry-over, the crossview_list_briefs filter):
        no git, no .agents, no orch init; nothing is written into the folder. While the old dispatch is on, such a
        folder shows as not ready until it is removed and added again through 添加项目."""
        s = clean_input(raw)
        if not s or not Path(s).is_absolute():
            raise ValueError(PROBLEMS["not_absolute"])
        p = _resolve(s)
        if not p.exists():
            raise ValueError(LINK_MISSING)
        if not p.is_dir():
            raise ValueError(PROBLEMS["not_dir"])
        loc = self._location(p)
        if loc:
            raise ValueError(PROBLEMS[loc])
        reg = self.by_path(p)
        if reg:
            raise ValueError(PROBLEMS["registered"].format(name=reg["name"]))
        return self._register(self._free_name(str(name or "").strip() or self._suggest(p)), p)

    def _excludes(self, p: Path, lines: list[str]) -> None:
        """Crossview's lines in <git-common-dir>/info/exclude (.task/ and .agents/ always), each once."""
        common = Path(_out(_ok(_git(["rev-parse", "--git-common-dir"], p), "rev-parse")))
        exclude = (common if common.is_absolute() else p / common) / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8", errors="replace") if exclude.exists() else ""
        have = set(existing.splitlines())
        missing = list(dict.fromkeys(x for x in [*ws.EXCLUDES, *lines] if x not in have))
        if missing:
            with exclude.open("a", encoding="utf-8") as f:
                f.write(("\n" if existing and not existing.endswith("\n") else "") + EXCLUDE_HEAD + "\n")
                f.write("".join(x + "\n" for x in missing))

    def _commit(self, p: Path, first: bool, keep_out: list[str]) -> None:
        """`git add -A` + commit as orch would commit here (identity rule of orch), skipping the user's hooks.
        Secret-looking files never go in, whatever the survey saw (it stops early in big folders, and a file the
        user staged before is past info/exclude): every staged one is taken out again before the commit."""
        Config.load(p)                             # sets [git] identity of this project (process-wide; under _prep)
        who = ws.ident(p)
        _ok(_git(["add", "-A"], p, timeout=ADD_TIMEOUT, read_only=False), "add")
        staged = _ok(_git(["diff", "--cached", "--name-only", "-z", "--no-renames"], p, timeout=ADD_TIMEOUT), "diff")
        found = [x for x in staged.stdout.decode("utf-8", errors="replace").split("\0")
                 if x and _secret(x.rsplit("/", 1)[-1])]
        keep_out = list(dict.fromkeys([*keep_out, *found]))
        for i in range(0, len(keep_out), 50):        # unstaged again (works before the first commit too)
            paths = [":(literal)" + x for x in keep_out[i:i + 50]]
            _ok(_git(["reset", "-q", "--", *paths], p, read_only=False), "reset")
        if found:
            self._excludes(p, [_pattern(x) for x in found])   # and they stay out of the agents' commits as well
        if not first and _git(["diff", "--cached", "--quiet"], p).returncode == 0:
            return
        msg = ["--allow-empty", "-m", "Initial commit"] if first else ["-m", "Save work before Crossview"]
        _ok(_git(["-c", "commit.gpgsign=false", *who, "commit", "--no-verify", "-q", *msg], p, timeout=ADD_TIMEOUT,
                 read_only=False), "commit")

    def _register(self, name: str, p: Path) -> dict:
        with self._guard:
            items = self._items()
            same = next((it for it in items if _key(it["path"]) == _key(p)), None)
            if same:
                raise ValueError(PROBLEMS["registered"].format(name=same["name"]))
            proj = {"id": self._next_id(items), "name": name, "path": str(p), "added_at": time.time()}
            self._save(items + [proj])
        return dict(proj)

    # -- folder browser ------------------------------------------------------------------------------------
    def browse(self, raw) -> dict:
        s = clean_input(raw) or os.path.expanduser("~")
        if re.fullmatch(r"[A-Za-z]:", s):
            s += "\\"
        p = _resolve(s)
        if not Path(s).is_absolute() or not p.is_dir():
            raise ValueError("打不开这个文件夹")
        try:
            with os.scandir(p) as it:
                entries = list(it)
        except OSError:
            raise ValueError("打不开这个文件夹") from None
        folders = []
        for e in entries:
            if e.name.startswith((".", "$")) or (os.name == "nt" and e.name.lower() in SYSTEM_DIRS):
                continue
            try:
                if not e.is_dir() or (os.name == "nt" and e.stat().st_file_attributes & 6):   # hidden / system
                    continue
            except OSError:
                continue
            folders.append({"name": e.name, "path": str(p / e.name)})
        folders.sort(key=lambda f: f["name"].casefold())
        return {"path": str(p), "parent": None if p.parent == p else str(p.parent), "folders": folders[:BROWSE_MAX],
                "more": len(folders) > BROWSE_MAX, "roots": _roots(), "home": os.path.expanduser("~")}


_KNOWN: list[Path] | None = None


def _known_folders() -> list[Path]:
    """Where Windows really keeps Desktop, Documents ... (moved to another drive or into OneDrive, any language)."""
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = []
        if os.name == "nt":
            try:
                import ctypes
                import uuid

                shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32  # type: ignore[attr-defined]
                for fid in KNOWN_FOLDERS:
                    guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(fid).bytes_le)
                    out = ctypes.c_wchar_p()
                    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                        if out.value:
                            _KNOWN.append(_resolve(out.value))
                        ole32.CoTaskMemFree(out)
            except Exception:   # an old or odd system: the names above still apply
                pass
    return list(_KNOWN)


def _roots() -> list[dict]:
    """Drive letters on Windows (never touched: a sleeping network drive would hang), else /."""
    if os.name != "nt":
        return [{"name": "/", "path": "/"}]
    try:
        drives = os.listdrives()
    except (AttributeError, OSError):
        import ctypes
        import string

        mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        drives = [f"{c}:\\" for i, c in enumerate(string.ascii_uppercase) if mask >> i & 1]
    return [{"name": d.rstrip("\\"), "path": d} for d in drives]
