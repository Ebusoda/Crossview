"""The default orch.toml with English comments, written by `orch init` when orch's language is not Chinese.

Same settings as DEFAULT_CONFIG_TOML in config.py (tests/test_config_en.py checks that both parse to the same
values); only the comments differ.
"""

DEFAULT_CONFIG_TOML_EN = """\
# Crossview settings
# No restart needed: changes apply from the next `orch run`.

[project]
base_branch = ""                       # empty = the branch that was current at `orch init`
integration_branch = "orch/integration"
worktrees_dir = ""                     # empty = <project folder>.worktrees (next to the project, not inside it)

[escalation]
verify_failures = 2                    # move to the next pool after this many failed acceptance checks in a row
early_escalation = true                # move on sooner: the run changed no file, or failed exactly like the last one
max_runs_per_task = 4                  # how many times an agent may actually work on one task (crashes, limits not counted)
run_timeout_minutes = 45               # time limit for one run
breaker_threshold = 2                  # crash-type failures in a row before a pool is paused (limits / login errors pause at once)
breaker_cooldown_minutes = 30          # how long a paused pool rests (for limits, the reset time is used when known)
verify_timeout_minutes = 15

[parallel]
max_jobs = 2                           # tasks `orch run` works on at the same time (--jobs overrides); 1 = one after another
# Runs per pool at the same time come from the pool's max_concurrent (default 1). When the first pool of a route is busy,
# the task waits for it instead of jumping to a dearer pool. Tasks touching the same files (overlapping scope, or no scope)
# never run at the same time.

[review]
# After acceptance passes and before merging, a model from another company reviews the change read-only
# (DeepSeek's work is reviewed by Codex, Codex's by Claude).
enabled = true
pools = ["codex_sub", "claude_sub"]    # reviewer candidates: the first available one from a different company
max_fix_rounds = 1                     # after this many send-backs without approval, stop and hand it to you (blocked)
# Deterministic checks (the acceptance commands) come first; an LLM review is only used where they are weakest:
require_risk = ["high", "critical"]    # tasks with these risk levels
when_no_verify = true                  # and tasks without acceptance commands
skip_risk = ["low"]                    # never review these risk levels (wins over the lines above)
# A single task can override this with `task add --review always / never`.

[pacing]
# Pace gate: from how fast a subscription window (Claude's 5 hours / 7 days) is being used, predict the use at reset.
# Prediction <= soft: every task may use the pool; <= hard: only L tasks or risk high / critical; above: only critical.
# A task held back goes to the next pool of its route, or stops for you if there is none. Early in a window the speed is
# unreliable, so at least 10% of the window counts as elapsed.
enabled = true
soft = 0.8
hard = 1.0
min_elapsed = 0.1

[budget]
# The most one task (all its runs and reviews) may spend; above it the task stops (blocked) for you. 0 = no limit.
max_usd_per_task = 0.50                # real dollars of pay-as-you-go API pools only (opencode pools, or billing = "api")
max_tokens_per_task = 0                # input + output tokens over all pools (subscriptions have no dollar figure)

[merge]
# After merging into the integration branch, run these project-wide commands (for example the whole test suite) as well
# as the task's own acceptance commands. If any fails, the merge is undone. Empty = only the task's own acceptance.
baseline = []                          # for example ["python -m unittest -q"]
resolve_conflicts = true               # on a merge conflict, merge integration into the task branch and let the agent resolve it;
                                       # merged only when no conflict markers remain and acceptance passes

[git]
# Whose name orch's own commits carry (checkpoints, merges, project memory, decision records).
# Empty = this project's git settings (git config user.name / user.email); only without those: orch <orch@localhost>.
name = ""                              # for example "Your Name"
email = ""                             # for example "you@example.com"

[approval]
manual_merge_risk = ["high", "critical"]   # tasks with these risk levels wait for `orch merge`, even with run --auto-merge

[decide]
# orch decide "question": two companies propose -> review each other -> a judge writes a decision record (ADR), at most 5 calls.
pools = ["claude_sub", "codex_sub"]    # proposing pools (the first two from different companies)
judge = ["claude_sub", "codex_sub"]    # the judge: the first available one
docs_dir = "docs/decisions"            # ADRs are committed to this folder on the integration branch
language = ""                          # empty = the language of the question; or for example "English"

[context]
# Context Manager: each call gets only the minimum context the task needs, never the history.
# The budget counts only what orch adds (PROMPT / TASK / HANDOFF / REVIEW / VERIFY / CONTEXT.md), by task size:
# S -> small, M -> normal, L -> large; a takeover gets at least normal. Over budget, low-scoring memory entries become
# pointers, then are dropped.
enabled = true                         # false = do not read the project memory in docs/project/ (amounts are still measured)
budgets = { small = 1500, normal = 3000, large = 6000, deep = 12000 }
# Session rotation: after a failed check or a review send-back, the same session would normally continue. Long sessions
# make every call dearer, so a new session with a handoff starts when either is true:
resume_max_tokens = 100000             # the last call's input tokens reached this (a pool may set its own resume_max_tokens)
max_resumes_per_session = 2            # the session has already been continued this many times
# class_by_type = { research = "large" }   # budget class per task type (optional)

[routing]
# task type.difficulty = pools to try in order; the first is used first, and a failure moves down the list.
code_change.S = ["deepseek", "codex_sub"]
code_change.M = ["deepseek", "codex_sub", "claude_sub"]
code_change.L = ["codex_sub", "claude_sub"]
default = ["codex_sub", "claude_sub"]
plan = ["claude_sub", "codex_sub"]     # orch plan: splitting a goal into tasks (read-only runs)
# Roles are ordinary routing entries too. When written here they win; otherwise [review] pools, [decide] pools / judge apply.
# deepseek_api is the direct API: it gets only the spec and the diff, without a CLI's overhead, and is skipped when
# DEEPSEEK_API_KEY is not set. It cannot review DeepSeek's own work (same company), so codex_sub does that.
review = ["deepseek_api", "codex_sub", "claude_sub"]   # reviewer candidates: the first available one from a different company
decide = ["claude_sub", "codex_sub"]   # orch decide proposers (the first two from different companies; they read code, so no direct API)
judge = ["deepseek_api", "claude_sub", "codex_sub"]    # the judge only reads the two proposals: a third company, the cheapest

# ---- pools: a CLI (or API) + a model ----
[pools.claude_sub]
executor = "claude_code"
model = ""                             # empty = Claude Code's default model (or sonnet / opus)
reserve = 0.10                         # once a 5-hour or 7-day window is 90% used, only risk=critical tasks
resume_max_tokens = 100000             # session rotation threshold: about half the model's context window

[pools.codex_sub]
executor = "codex"
model = ""
reserve = 0.10                         # Codex does not report its usage in its events yet, so this has no effect for now
resume_max_tokens = 200000

[pools.gemini_sub]
# Gemini CLI. Since October 2026 the free personal Google sign-in no longer works with Gemini CLI (it is refused with
# IneligibleTierError): use a Google AI Studio API key (environment variable GEMINI_API_KEY) or a paid Google plan.
# Not in any route by default: once it works, add "gemini_sub" to the [routing] lines where you want it.
executor = "gemini"
model = ""                             # empty = gemini's own default model

[pools.deepseek]
executor = "opencode"
model = "deepseek/deepseek-flash"      # as listed by `opencode models`
max_concurrent = 2                     # pay-as-you-go APIs can run several at once; subscriptions keep 1 to spare the quota
resume_max_tokens = 64000

[pools.deepseek_api]
# Direct API (OpenAI-compatible, standard library urllib only). Only for steps that need no tools, such as reviews and
# judging; it never gets coding tasks.
executor = "api"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"       # the key is read from this environment variable (or `orch key set`), never from this file
model = "deepseek-flash"               # the same model as opencode's deepseek/deepseek-flash; see `orch api check`
# Dollars per million tokens; 0 = count tokens only. Below: deepseek-flash's official peak prices (off-peak is half),
# from https://api-docs.deepseek.com/quick_start/pricing/ on 2026-10-08; peak prices keep the estimate on the safe side.
price_in_per_m = 0.30                  # input, not cached
price_cache_hit_per_m = 0.006          # input, cached
price_out_per_m = 1.20
max_concurrent = 2

# ---- CLI settings ----
# The prompt is not passed on the command line: orch writes .task/PROMPT.md and the CLI reads it (or gets it on stdin),
# which avoids quoting problems with .cmd wrappers on Windows.
[executors.claude_code]
command = "claude"
# --strict-mcp-config: do not load your account's connectors / MCP servers (they add many tool definitions and use quota)
args = ["--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits", "--strict-mcp-config"]
# The only built-in tools the agent gets (no scheduled tasks, web search, remote triggers and so on)
tools = ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "PowerShell"]
# On Windows Claude Code runs commands with Git Bash (Bash) or PowerShell; both are allowed.
allowed_tools = ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "PowerShell"]
prompt_via_stdin = true                # the prompt arrives on stdin: one model call fewer than reading the file

[executors.codex]
command = "codex"
# windows.sandbox=unelevated: on Windows Codex normally runs commands as a separate low-rights user, which cannot see
# Python / Node installed in your user folder. unelevated runs them as you with restricted rights; writing outside the
# working folder is still blocked, but the isolation is weaker. For stronger isolation remove these two items and install
# Python for all users (under C:/Program Files). No effect on macOS / Linux.
args = ["--json", "--sandbox", "workspace-write", "-c", "windows.sandbox=unelevated"]
prompt_via_stdin = true                # the prompt goes to Codex as a <stdin> block (supported); measured: 15 calls -> 5

[executors.gemini]
command = "gemini"
# --approval-mode yolo: approve its actions (editing files, running commands) without asking. It only works in the task's
# own worktree, like Claude Code's acceptEdits and Codex's workspace-write; read-only planning and reviews switch to plan mode.
# --skip-trust: every task runs in a new worktree, and gemini exits at once in a folder it does not trust (gemini-cli 0.63.0).
args = ["--output-format", "stream-json", "--approval-mode", "yolo", "--skip-trust"]

[executors.opencode]
command = "opencode"
args = ["--format", "json"]
# If `orch probe` shows opencode waiting for a permission, "--auto" approves whatever is not explicitly forbidden (risky).

# Verified with real tasks on Windows (orch probe): claude 2.1.292 / codex-cli 0.160.1 / opencode 1.18.35.
"""
