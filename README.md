# MAO

MAO runs on your own Windows PC next to Claude Cowork. GPT (through the Codex CLI) and Claude (through Claude Code)
discuss a requirement over several rounds, read-only and on your own subscriptions. You decide at each stage, and
Cowork builds the approved plan: it takes the plan from MAO through MCP tools and reports back. MAO also gives
Cowork what it lacks on this PC (office files, bank statements), runs a small web app for the family, and pushes to
your phone when something waits for you. MAO no longer orchestrates coding agents itself: its old task dispatch is an
optional legacy feature, off by default.

Python 3.11+, standard library only. Built and used on Windows. A guide in Chinese is in
[docs/README.zh-CN.md](docs/README.zh-CN.md).

## What it does

- **Discuss.** Write an idea or a requirement in the web app and optionally pick a project folder. GPT and Claude
  each analyse it on their own, then critique and revise a plan round by round. After each stage you get a summary
  (consensus, open disagreements, risks, questions for you) and choose what happens next: answer their questions,
  ask for another round or a review, change the requirement, reject or approve the plan. Everything is kept in a
  decision record.
- **Briefs for Cowork.** An approved plan becomes a brief. Cowork finds and takes it with `mao_list_briefs` and
  `mao_get_brief`, builds it itself, and reports progress, blockers, questions or completion with
  `mao_report_progress`. MAO never starts development on its own.
- **Phone alerts** through the ntfy app: a discussion waits for your decision, Cowork is blocked or has a question,
  Claude Code waits for your approval. MAO only looks and sends a short line; it never approves anything.
- **Family web app:** chat with the model you pick, pictures and video, documents, an account for each family
  member, Chinese or English, on a phone too, and from outside through Tailscale.
- **Office work on the command line** (and for Cowork): ask questions about an Excel or CSV table, draft, polish or
  fill in Word documents, follow money through bank statements, find names and numbers in a pile of files.
  Personal details are masked before anything is sent to a model, and sensitive mode uses only models based in
  China.
- **Sessions list:** the Claude Code and Codex sessions on this PC, including the ones you open by hand (folder,
  model, last use, tokens). Conversation content is never read.

## Getting started

You need Python 3.11 or newer and git; the two CLIs below need Node.js. MAO itself installs nothing else. Commands
are for PowerShell, run from the folder you cloned into (or `pip install -e .` once, then type `orch` and `maoweb`
instead of `python -m orch` and `python -m maoweb`).

1. **Download.**

   ```powershell
   git clone https://github.com/Ebusoda/Multi-Agent-Orchestrator.git
   cd Multi-Agent-Orchestrator
   ```

2. **Start the web app.** Double-click `run_web.cmd` (or run `python -m maoweb`). It opens http://127.0.0.1:8848/,
   where the first visit creates the admin account. Keep it running: it also runs the phone-alert checks.
3. **Install and sign in to the two CLIs that discuss.** Claude Code (`npm install -g @anthropic-ai/claude-code`,
   then run `claude` once) uses your Claude plan; Codex (`npm install -g @openai/codex`, then run `codex` once) uses
   your ChatGPT plan. `python -m orch doctor` shows whether both are found. Then open **Discuss** in the web app.
4. **Add MAO to Claude Desktop / Cowork.** Put this in `claude_desktop_config.json`, with `F:\MAO` changed to the
   folder you cloned into, and restart Claude Desktop:

   ```json
   {"mcpServers": {"orch": {"command": "cmd",
                            "args": ["/c", "cd /d F:\\MAO && python -m orch mcp --project F:\\MAO"]}}}
   ```

   `cmd /c cd /d` makes the MAO folder the working directory, so `python -m orch` is found: a `"cwd"` key is not
   honoured by every client, and without it Python says "No module named orch". In Cowork, say for example: "use
   MAO's mao_get_brief to take DS-0001 and build it".
5. **Phone alerts** (optional). In the web app, Settings > Phone alerts: install ntfy on your phone, subscribe to
   the topic shown there, press "Send a test alert", then turn on "Turn on phone alerts" (or run
   `python -m orch notify setup`, which also turns them on). For Claude Code's approval prompts, also run
   `python -m orch notify hook install`.

## Web app

`run_web.cmd` (or `python -m maoweb`) serves the app on this PC at http://127.0.0.1:8848/. The admin adds family
members and enters API keys in Settings. Accounts, chats, files, discussions and keys stay on this PC, in the
`.maoweb` folder next to `run_web.cmd` (or the folder in `MAO_WEB_DATA`); API keys are encrypted with Windows DPAPI
and never shown again.

- **Chat** with DeepSeek, Doubao, Qwen, GPT, Claude or Gemini through your API keys. The admin can also chat
  through the local Claude Code sign-in ("Claude (my subscription)"): it uses your Claude plan, runs with every tool
  off, and is never offered to other accounts.
- **Pictures** from a description or by editing a photo, and **video** (Doubao Seedream / Seedance,
  `ARK_API_KEY`).
- **Documents:** ask about a table, write, polish or fill in Word (with everyday templates), follow money through
  bank statements and find information in a pile of files. Personal details are masked as on the command line.
- **Discuss** (admin): the discussion page described above. A project folder can be registered right in the form
  ("Register a folder"); nothing is written into it, and the discussing models can read it but not change it.
- **Agent** (admin): the Claude Code and Codex sessions on this PC, read-only.
- **Settings** (admin): chat models and keys, picture and video models, family members, access from outside,
  **Phone alerts**, and **Legacy** (the switch for the old task dispatch).
- Chinese or English; works on a phone. `run_web.cmd` also listens on this PC's Tailscale address (`--remote`), so
  your own phone can open MAO when you are out; only this PC and your Tailscale devices are let in (`--lan` also
  allows the home network).

## MCP tools

`python -m orch mcp --project DIR [--data DIR] [--discussions DIR]` is a Model Context Protocol server over stdio
for Claude Desktop, Cowork or any MCP client (set up in step 4 above). It offers these tools:

| Tool | What it does |
| --- | --- |
| `mao_list_briefs` | plans you approved in a discussion, newest first: approved, taken or done |
| `mao_get_brief` | the brief to build: the original requirement, your changes and constraints, the approved plan with its acceptance criteria, decisions, rejected plans, risks and open questions. Taking it marks it taken, so nobody starts the same work twice |
| `mao_report_progress` | progress, blocker, question or done (done needs evidence: test commands, their results and the commit). MAO records it as Cowork's claim and only checks that a given commit exists |
| `office_sheet_ask` | answer a question about an Excel, WPS or CSV table on this PC |
| `office_doc` | draft, polish or fill in a Word document on this PC |
| `office_trace` | follow money through bank statements on this PC, without any model |
| `office_find` | find words, names or numbers in a folder of files on this PC, without any model |
| `orch_sessions` | the Claude Code and Codex sessions on this PC |

A blocker, a question or a done report also sends you a phone alert at once when alerts are on (never the report's
text). `office_trace` and `office_find` return only counts and file paths unless details are asked for. Briefs come
from the discussions folder: `--discussions DIR`, else `DIR/discussions` for `--data DIR`, else `MAO_DISCUSS_DIR`,
else `<MAO_WEB_DATA or .maoweb>/discussions`, which is where the web app keeps them.


With the legacy dispatch on, the 14 `orch_*` task tools are listed too.

## Phone alerts

MAO pushes one short line to the ntfy app (iOS and Android, no account needed) when something waits for you. It
only looks: it never clicks, answers or approves anything.

- **Discussions:** a stage summary is ready for your decision; a discussion stopped (an error or an interruption);
  or nothing has happened for 25 minutes (adjustable from 21 to 240), so it may be stuck.
- **Cowork:** a blocker, a question or a done report through `mao_report_progress`, pushed at once.
- **Cowork may have stopped** (off by default): a taken brief with no report for 6 hours (1 to 72). This is a weak
  signal and the alert says so: Cowork may have stopped, or may still be working.
- **Claude Code approvals:** Claude Code waits for a permission or for your answer to a question.
- **Claude Code finished and waiting** (off by default): it comes after every answer, so it is easy to get too many.
- No Claude Code alert is sent while you used this PC's keyboard or mouse in the last 3 minutes (Windows; adjustable,
  0 = always send).

Set it up in Settings > Phone alerts (switch, topic, test alert, which events, the alerts' language), or with
`python -m orch notify setup` and `python -m orch notify test`. `python -m orch notify status` shows the settings and
the last alerts. At most 10 alerts go out in 10 minutes; a failed one is tried again after 1, 5, 15 and 60 minutes,
for up to 2 hours (a Claude Code alert for 10 minutes).

**Privacy.** An alert says only what happened plus a short id (such as DS-0003, or the start of a session id):
never requirement text, model answers, report text, file contents, folder or project names, keys or personal data.
ntfy.sh is a third-party server, and anyone who knows the topic can read its alerts, so the topic is the only
secret: MAO generates a random one, which you can replace with a new one at any time (then subscribe again). You can
use your own ntfy server instead: set its address, plus an access token if it needs one (`notify setup --token`
reads it without showing it; it is kept with the API keys, encrypted on Windows, never in the config file and never
sent to the browser). A server address may not contain a user name or password.

**The Claude Code hook.** `python -m orch notify hook install` (or Install under Settings > Phone alerts) adds one
`Notification` hook to Claude Code's `settings.json` (in `CLAUDE_CONFIG_DIR`, else `~/.claude`). It first copies the
file to `settings.json.bak-<date>-<time>` and keeps every other hook. Installing again changes nothing;
`python -m orch notify hook uninstall` removes only MAO's entry, and `hook status` shows the state. The hook writes
nothing back to Claude Code. It is not verified for Cowork or Claude Desktop sessions. To check it, turn alerts on,
set "No Claude Code alerts while you are at this PC" to 0 (or leave the keyboard and mouse alone for 3 minutes),
then make Claude Code ask for a permission. The hook starts Python with exec-form `args`, so it needs a Claude Code
version that supports them: if nothing arrives and `notify status` lists nothing `via hook`, run `claude update`.
If a console window flashes when the hook runs, install it with `--python <the folder of python.exe>\pythonw.exe`;
installing again (also from the web page) keeps that Python, and `hook status` shows it as installed.

**Where the checks run.** Inside the web app, once a minute. Without it, Cowork reports and Claude Code prompts are
still pushed at once, but failed alerts are not retried and discussions are not watched.

## Command line

`python -m orch <command>` (or `orch <command>` after `pip install -e .`):

| Command | Purpose |
| --- | --- |
| `doctor` | check Python, git and the CLIs |
| `sessions [--days N] [--all]` | Claude Code / Codex sessions on this PC, hand-opened ones included (read-only) |
| `sheet show / ask / run FILE` | questions about an Excel / CSV table; `run` repeats a saved plan without a model |
| `doc draft / polish / fill / show` | write, edit or fill in a Word document (`--from`, `--style plain / gongwen`) |
| `trace FILES-OR-FOLDERS [--days N] [--known LIST] [--col ROLE=HEADER]` | follow money through bank statements, locally |
| `find FOLDER WORDS... [--type id/phone/card/email] [--all]` | find information in a pile of files, locally |
| `office [sensitive on / off]` | office settings (sensitive mode, your word list for masking) and how many calls and tokens office work used |
| `key set / list / remove PROVIDER`, `connect` | your own API keys (encrypted on Windows); which models you can use |
| `api check POOL` | one tiny call to check an API key |
| `mcp --project DIR [--data DIR] [--discussions DIR]` | the MCP server for Cowork and Claude Desktop |
| `notify status / setup / off / test`, `notify hook install / uninstall / status` | phone alerts and the Claude Code hook |
| `legacy [on / off / status]` | the switch for the old task dispatch |
| `lang [zh / en / ja]` | language of orch's messages (`--lang en` for one command) |

Office work uses your API keys (DeepSeek, GPT, Claude, Qwen, Gemini, Doubao): save one with
`python -m orch key set deepseek` (the input is hidden), or set the provider's environment variable
(`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DASHSCOPE_API_KEY`, `GEMINI_API_KEY`, `ARK_API_KEY`).
Old formats (.doc, .wps, .rtf, .xls, .et) are converted first by Word / Excel, WPS or LibreOffice if one is
installed; the original is never changed. Masking catches common patterns (ID, mobile, bank card and landline
numbers, e-mail, names after a label, your own words with `--mask`), not everything: check your workplace's rules
before sending work documents to an outside model, and never use it for classified material.

## Old task dispatch (legacy, off by default)

Before 0.6.0, MAO orchestrated coding agents itself: a routing ladder that started each task on the cheapest
suitable model (Claude Code, Codex, Gemini CLI, opencode with DeepSeek and others, or a direct API) and moved it to
a stronger one on objective failures; one git worktree per task, with checkpoints and handoff notes; acceptance
commands that decide success; `plan` and `decide` for planning and design debates; the web app's Projects page; and
the `orch_*` MCP task tools. Development now goes to Cowork, so all of this is off by default. Nothing was removed,
and its data is kept.

Turn it on in Settings > Legacy, with `python -m orch legacy on`, or with the environment variable
`MAO_LEGACY_DISPATCH=1` (`0` forces it off, and while the variable is set the switch cannot be changed elsewhere).
While it is off, its commands print a one-line hint and exit with code 2, the web app hides the Projects page, and
the MCP server does not offer its tools.

Its commands: `init`, `task add / list / show / retry / cancel / accept`, `run [--jobs N] [--auto-merge]`,
`status`, `plan`, `decide`, `context`, `memory`, `merge`, `agents [pause / resume / swap]`, `report`,
`route suggest / apply`, `usage rescan`, `breaker`, `handoff export`, `demo`, `handoff-test`, `probe`, and
`connect PROVIDER` (add a model to a project). Each project's settings live in its `.agents/orch.toml`;
`examples/task.example.toml` is an example task file (`task add -f`).

## Safety

- The discussing models run read-only: Codex with `--sandbox read-only`, Claude Code with only its Read, Glob and
  Grep tools. They can read a linked project folder and change nothing.
- MAO never approves, clicks or answers anything for you: phone alerts only report, the Claude Code hook only
  pushes, and Cowork builds only plans you approved.
- MAO never reads or copies the CLIs' sign-in files; the sessions list reads only times, folders and token counts.
  API keys and the ntfy token are stored encrypted on Windows.
- The web app lets in only this PC and your own Tailscale devices (plus the home network with `--lan`).
- Only while the legacy dispatch is on: agents run arbitrary commands inside their own worktree, so use it on your
  own repositories. Your own checkout is never touched, except by the Projects page's "Put it in my project folder"
  button, and only when you tap it.

## Changelog

- **0.6.0**: MAO is now built around discussion and decisions: GPT and Claude discuss a requirement round by round
  in the web app, you decide, and Claude Cowork takes the approved brief over MCP (`mao_list_briefs`,
  `mao_get_brief`, `mao_report_progress`). Phone alerts through ntfy when a discussion waits for you, Cowork is
  blocked or asks, or Claude Code waits for your approval. The old task dispatch (Projects page,
  `orch task/run/plan/decide`, the `orch_*` task tools) is off by default and can be switched back on; its data
  is kept. In the web app, 0.5.0's "assign to agents" card and Coding page are replaced by a Projects page (with
  "Hand to a project" in the chat and "Put it in my project folder"), which belongs to the old dispatch.
- **0.5.0**: the web app is public (chat, assign to agents, pictures, video, documents, coding, agents panel;
  Chinese or English). `orch trace` follows money through bank statements and `orch find` searches a pile of
  files, both on your computer only; the MCP server offers them as `office_trace` and `office_find`.
- **0.4.0**: old .doc / .xls / .wps files open through Word, Excel, WPS or LibreOffice; office tools in the MCP
  server (`office_sheet_ask`, `office_doc`); office calls and tokens are recorded and shown by `orch office` and
  `orch report` (counts only, never content).
- **0.3.9**: `orch init` writes the config file with English comments (Chinese when orch's language
  is Chinese).
- **0.3.8**: Gemini CLI runs in each task's new worktree (`--skip-trust`); the guide says it needs a Gemini API key
  or a paid plan, because the free personal sign-in is now refused.
- **0.3.7**: Gemini CLI as a coding agent (`executor = "gemini"`, pool `gemini_sub`), next to Claude Code and
  Codex; reviews and plans run it read-only. The masking summary counts only details that were actually sent.
- **0.3.6**: office work. `orch sheet` answers questions about Excel and CSV tables (the model sees only the
  column names and a few sample rows; the numbers are computed on your computer) and saves the result as
  .xlsx with a chart. `orch doc` drafts, polishes and fills in Word documents, with an official-document
  layout. Personal details are masked before sending; sensitive mode uses only models based in China.
- **0.3.5**: `orch agents`, one panel for every agent: what it is doing, usage today and this week, subscription
  quota, recent success rate and last error, with pause, resume and swap. `orch sessions` lists the Claude Code
  and Codex sessions on this computer, including ones you opened yourself (read-only: times, folder and tokens only).
  orch speaks Chinese, English and Japanese (`orch lang`, `--lang`, follows the system language).
- **0.3.4**: early escalation: a run that changes nothing, or fails acceptance exactly like the previous run, moves
  the task to the next model straight away (`[escalation] early_escalation`, on by default). README lists what you
  can do today.
- **0.3.2**: `orch key` and `orch connect`: use your own DeepSeek, GPT, Claude, Qwen, Gemini and Doubao
  accounts from the command line (keys encrypted on Windows); step-by-step getting started.
- **0.3.1**: MCP server (`orch mcp`) to list, add, run and follow tasks from chat apps such as Claude Desktop;
  merge conflicts are handed to an agent and merged only when no conflict markers remain and acceptance passes.
- **0.3.0**: first public release: routing ladder and escalation, git worktree isolation and handoffs, Context
  Manager with versioned project memory, per-call context manifests, cost report, review only where tests are
  weak, direct API for reviews, per-task budgets, pacing gate, routing suggestions.

## License

[PolyForm Noncommercial 1.0.0](LICENSE): free to use, modify and share for any noncommercial purpose.
Commercial use is not permitted.
