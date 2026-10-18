# Crossview（中文说明）

Crossview 跑在你自己的 Windows 电脑上，和 Claude Cowork 搭配使用。GPT（通过 Codex 命令行）和 Claude（通过 Claude Code）
围绕一个需求讨论好几轮，全程只读，用的是你自己的订阅。每个阶段由你拍板；批准后的方案由 Cowork 通过 MCP 工具从 Crossview
领走、自己开发，并把进度报回来。Crossview 还给 Cowork 补上它在这台电脑上做不到的事（办公文件、银行流水），带一个家用网页版，
有事等你时推送到你的手机。Crossview 自己不再给多个编程 agent 派活：旧的派活功能成了可选的旧功能，默认关闭。

只需要 Python 3.11 以上，不用安装任何第三方库。在 Windows 上开发和使用。

## 它能做什么

- **讨论**：在网页版里写下想法或需求，可以关联一个项目文件夹。GPT 和 Claude 先各自分析，再一轮轮互相挑错、修订方案。
  每个阶段结束有一份小结（共识、未解决的分歧、风险、问你的问题），由你决定下一步：回答它们的问题、再讨论一轮或交给对方评审、
  修改需求、否掉或批准方案。整个过程记在决策记录里。
- **交给 Cowork 的方案**：批准后的方案成为一份交接说明（brief）。Cowork 用 `crossview_list_briefs`、`crossview_get_brief` 找到并领取，
  自己开发，再用 `crossview_report_progress` 报告进度、阻碍、问题或完成。Crossview 自己不会开始开发。
- **手机提醒**（ntfy App）：讨论在等你决定、Cowork 遇到阻碍或有问题、Claude Code 在等你批准。Crossview 只看、只发一句话，
  从不替你批准任何东西。
- **家用网页版**：自己选模型聊天、画图和视频、文档，每个家人一个账号，中文或英文，手机上也能用，出门在外通过 Tailscale 打开。
- **命令行办公**（Cowork 也能用）：问 Excel、CSV 表格，起草、润色或按模板填写 Word，资金追踪，在一堆文件里找名字和号码。
  发给模型之前先隐藏个人信息；敏感模式只用国内模型。
- **会话列表**：这台电脑上的 Claude Code、Codex 会话，包括你自己手动开的（文件夹、模型、上次使用、token 数）。不看对话内容。

## 从下载到第一次讨论

需要 Python 3.11 以上和 git；下面两个命令行工具需要 Node.js。Crossview 自己不用再装别的东西。下面的命令用 PowerShell，
请在下载下来的文件夹里运行（或者先运行一次 `pip install -e .`，之后就可以直接输入 `orch`、`crossview-web`）。

1. **下载**：

   ```powershell
   git clone https://github.com/Ebusoda/Crossview.git
   cd Crossview
   ```

2. **打开网页版**：双击 `run_web.cmd`（或运行 `python -m maoweb`），会打开 http://127.0.0.1:8848/ ，第一次打开时创建管理员账号。
   让它一直开着：手机提醒的检查也在它里面运行。
3. **安装并登录负责讨论的两个命令行工具**：Claude Code（`npm install -g @anthropic-ai/claude-code`，然后运行一次 `claude`）
   用你的 Claude 套餐；Codex（`npm install -g @openai/codex`，然后运行一次 `codex`）用你的 ChatGPT 套餐。
   `python -m orch doctor` 会显示两个是否都找得到。然后在网页版里打开 **讨论**。
4. **把 Crossview 加到 Claude Desktop / Cowork**：在 `claude_desktop_config.json` 里加上下面这段（把 `D:\Crossview` 换成你下载到的文件夹），
   然后重启 Claude Desktop：

   ```json
   {"mcpServers": {"orch": {"command": "cmd",
                            "args": ["/c", "cd /d D:\\Crossview && python -m orch mcp --project D:\\Crossview"]}}}
   ```

   `cmd /c cd /d` 先进到 Crossview 的文件夹，`python -m orch` 才找得到：不是每个客户端都认 `"cwd"`，不进这个文件夹 Python 会报
   "No module named orch"。在 Cowork 里可以这样说：“用 Crossview 的 crossview_get_brief 领取 DS-0001 开始开发”。
5. **手机提醒**（可选）：网页版 设置 → 手机提醒：在手机上安装 ntfy，订阅页面上显示的主题，点「发一条测试提醒」，
   收到后打开「开启手机提醒」开关（或者运行 `python -m orch notify setup`，它也会打开提醒）。
   想在 Claude Code 等你批准时收到提醒，再运行 `python -m orch notify hook install`。

## 家用网页版

`run_web.cmd`（或 `python -m maoweb`）在这台电脑上打开网页版：http://127.0.0.1:8848/ 。管理员在设置里添加家人、填写 API key。
账号、对话、文件、讨论和 key 都留在这台电脑上，在 `run_web.cmd` 旁边的 `.maoweb` 文件夹里（或环境变量 `MAO_WEB_DATA` 指定的文件夹）；
API key 用 Windows 自带的加密（DPAPI）保存，填完不再显示。

- **聊天**：自己选模型（DeepSeek、豆包、千问、GPT、Claude、Gemini，用 API key）。管理员还可以用本机 Claude Code 的登录聊天
  （「Claude（我的订阅）」）：走你的 Claude 套餐，关掉所有工具，别的账号看不到。
- **画图**（文字生成或修改照片）和 **视频**（豆包 Seedream / Seedance，`ARK_API_KEY`）。
- **文档**：问表格、写 / 改 / 按模板填 Word（带常用模板）、资金追踪、在一堆文件里找信息。个人信息和命令行一样先隐藏。
- **讨论**（管理员）：上面说的讨论页。可以直接在表单里登记一个项目文件夹（「登记文件夹」）：不会往里面写任何东西，
  讨论的模型能读、改不了。
- **Agent**（管理员）：这台电脑上的 Claude Code、Codex 会话，只读。
- **设置**（管理员）：聊天模型和 key、画图视频模型、家人、外出访问、**手机提醒**、**旧功能**（旧派活功能的开关）。
- 界面中文或英文，手机上也能用。`run_web.cmd` 同时在这台电脑的 Tailscale 地址上监听（`--remote`），出门在外用自己的手机也能打开；
  只放行这台电脑和你自己的 Tailscale 设备（加 `--lan` 还放行家里的局域网）。

## MCP 工具

`python -m orch mcp --project 目录 [--data 目录] [--discussions 目录]` 是一个走 stdio 的 MCP（Model Context Protocol）服务，
给 Claude Desktop、Cowork 或其他 MCP 客户端用（设置方法见上面第 4 步）。它提供这些工具：

| 工具 | 用途 |
| --- | --- |
| `crossview_list_briefs` | 你在讨论里批准的方案，新的在前：已批准、已领取或已完成 |
| `crossview_get_brief` | 要开发的方案：原始需求、你的修改和约束、批准的方案和验收标准、决定、被否掉的方案、风险和未解决的问题。领取后标成已领取，不会有人重复开工 |
| `crossview_report_progress` | 进度、阻碍、问题或完成（完成要附证据：测试命令、结果和提交）。Crossview 只当作 Cowork 自己的说法记下来，只核对给出的提交是否存在 |
| `office_sheet_ask` | 问这台电脑上的 Excel、WPS 或 CSV 表格 |
| `office_doc` | 在这台电脑上起草、润色或按模板填写 Word |
| `office_trace` | 资金追踪，只在本机计算，不用任何模型 |
| `office_find` | 在这台电脑上的一堆文件里找词、名字或号码，不用任何模型 |
| `orch_sessions` | 这台电脑上的 Claude Code、Codex 会话 |

报告阻碍、问题或完成时，如果开了手机提醒，会马上推送一条（不带报告内容）。`office_trace` 和 `office_find` 默认只返回数量和文件位置，
要细节才返回内容。方案从讨论文件夹里读：`--discussions 目录`，否则 `--data 目录` 下的 `discussions`，否则 `MAO_DISCUSS_DIR`，
否则 `<MAO_WEB_DATA 或 .maoweb>/discussions`，也就是网页版存讨论的地方。


打开旧的派活功能时，还会列出 14 个 `orch_*` 任务工具。

## 手机提醒

有事等你时，Crossview 往 ntfy App（iOS 和 Android，不用注册账号）推一句话。它只看，从不点击、回答或批准任何东西。

- **讨论**：阶段小结好了等你决定；讨论停下了（出错或中断）；或者 25 分钟没有动静（可在 21 到 240 之间调），可能卡住了。
- **Cowork**：通过 `crossview_report_progress` 报告阻碍、问题或完成，马上推送。
- **Cowork 可能停了**（默认关）：领走的方案 6 小时（1 到 72）没有回报。这是弱信号，提醒里也这么写：Cowork 可能停了，也可能还在做。
- **Claude Code 等你批准**：Claude Code 在等权限，或等你回答它的问题。
- **Claude Code 做完一轮、停下来等你**（默认关）：每次回答后都会有，容易太多。
- 最近 3 分钟内你在这台电脑上动过键盘或鼠标，就不发 Claude Code 的提醒（Windows；可以调，0 = 总是发）。

在网页版 设置 → 手机提醒 里设置（开关、主题、测试提醒、提醒哪些事、提醒的语言），或者用 `python -m orch notify setup` 和
`python -m orch notify test`。`python -m orch notify status` 显示设置和最近的提醒。10 分钟内最多发 10 条；发送失败的在 1、5、15、60
分钟后重试，最多 2 小时（Claude Code 的提醒只重试 10 分钟）。

**隐私**：提醒里只写发生了什么和一个短编号（例如 DS-0003，或会话编号的开头），不会有需求、模型的回答、报告内容、文件内容、
文件夹或项目名、密钥或个人信息。ntfy.sh 是别人的服务器，知道主题名的人都能收到提醒，所以主题是唯一的秘密：Crossview 会生成一个随机的，
随时可以换一个新的（换了以后手机上要重新订阅）。也可以用自己的 ntfy 服务器：填上服务器地址，需要的话再填访问令牌
（`notify setup --token` 输入时不显示；它和 API key 存在一起，在 Windows 上加密，不写进配置文件，也不会发到浏览器）。
服务器地址里不能带用户名或密码。

**Claude Code 钩子**：`python -m orch notify hook install`（或在 设置 → 手机提醒 里点「安装」）往 Claude Code 的 `settings.json`
（在 `CLAUDE_CONFIG_DIR` 里，否则在 `~/.claude`）加一个 `Notification` 钩子。改之前先把原文件复制成 `settings.json.bak-<日期>-<时间>`，
别的钩子都保留。再装一次不会有改动；`python -m orch notify hook uninstall` 只移除 Crossview 自己的那一条，`hook status` 显示状态。
钩子不会往 Claude Code 里写回任何东西。在 Cowork 和 Claude Desktop 的会话里是否有效还没有验证。要试的话，先打开手机提醒，
把「你在这台电脑前时不发 Claude Code 的提醒」设成 0（或者 3 分钟内不碰键盘鼠标），再让 Claude Code 要一次权限。
钩子用 exec 形式的 `args` 启动 Python，需要支持它的 Claude Code 版本：如果没收到，`notify status` 里也没有 `via hook` 的记录，
就运行 `claude update`。如果钩子运行时闪出一个黑窗口，就用 `--python <python.exe 所在的文件夹>\pythonw.exe` 重新安装；
以后再安装（包括在网页上点「安装」）会保留这个 Python，`hook status` 也显示已安装。

**检查在哪里运行**：在网页版里，每分钟一次。网页版没开时，Cowork 的报告和 Claude Code 的提醒照样马上推送，但发送失败的不会重试，
讨论也没人盯着。

## 常用命令

`python -m orch <命令>`（`pip install -e .` 之后也可以直接用 `orch <命令>`）：

| 命令 | 用途 |
| --- | --- |
| `doctor` | 检查 Python、git 和各个命令行工具 |
| `sessions [--days N] [--all]` | 本机的 Claude Code / Codex 会话，包括你手动开的（只读） |
| `sheet show / ask / run 文件` | 问 Excel / CSV 表格；`run` 不用模型，按保存的计划重算 |
| `doc draft / polish / fill / show` | 起草、修改或按模板填写 Word（`--from` 材料，`--style plain / gongwen` 版式） |
| `trace 流水文件或文件夹 [--days N] [--known 名单] [--col 角色=列名]` | 资金追踪，只在本机计算 |
| `find 文件夹 词…… [--type 身份证/手机/银行卡/邮箱] [--all]` | 在一堆文件里找信息，只在本机查 |
| `office [sensitive on / off]` | 办公设置（敏感模式、自己要隐藏的词）和办公功能用了多少次、多少 token |
| `key set / list / remove 服务商`、`connect` | 你自己的 API key（Windows 上加密保存）；现在能用哪些模型 |
| `api check 池名` | 发一次极小的调用，检查 API key |
| `mcp --project 目录 [--data 目录] [--discussions 目录]` | 给 Cowork 和 Claude Desktop 用的 MCP 服务 |
| `notify status / setup / off / test`、`notify hook install / uninstall / status` | 手机提醒和 Claude Code 钩子 |
| `legacy [on / off / status]` | 旧派活功能的开关 |
| `lang [zh / en / ja]` | orch 提示的语言（只想这一次换，就加 `--lang en`） |

办公功能用你自己的 API key（DeepSeek、GPT、Claude、千问、Gemini、豆包）：用 `python -m orch key set deepseek` 保存（输入时不显示），
或者设置对应的环境变量（`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`DASHSCOPE_API_KEY`、`GEMINI_API_KEY`、`ARK_API_KEY`）。
老格式（.doc、.wps、.rtf、.xls、.et）会先用电脑上装的 Word / Excel、WPS 或 LibreOffice 转换，原文件不动。脱敏能识别常见格式
（身份证号、手机号、银行卡号、座机、邮箱、「联系人：」这类标签后面的姓名、你用 `--mask` 指定的词），不能保证找全：
工作文件发给外部模型前先确认单位规定，涉密文件一律不要用。

## 旧的派活功能（旧功能，默认关闭）

0.6.0 以前，Crossview 自己给编程 agent 派活：路由阶梯先把任务交给合适的最便宜的模型（Claude Code、Codex、Gemini CLI、
带 DeepSeek 等模型的 opencode，或直接 API），客观失败时换更强的；每个任务一个 git worktree，带 checkpoint 和交接说明；
验收命令决定成败；`plan` 和 `decide` 负责规划和方案辩论；网页版的「项目」页；以及 `orch_*` MCP 任务工具。
现在开发交给 Cowork，所以这些默认关闭。没有删掉任何东西，数据都保留。

打开方法：网页版 设置 → 旧功能，或 `python -m orch legacy on`，或设置环境变量 `MAO_LEGACY_DISPATCH=1`
（`0` 强制关闭；设了这个环境变量，别的地方就改不了开关）。关闭时，这些命令只打印一行提示并返回 2，网页版不显示「项目」页，
MCP 服务也不提供这些工具。

它的命令：`init`、`task add / list / show / retry / cancel / accept`、`run [--jobs N] [--auto-merge]`、`status`、`plan`、
`decide`、`context`、`memory`、`merge`、`agents [pause / resume / swap]`、`report`、`route suggest / apply`、`usage rescan`、
`breaker`、`handoff export`、`demo`、`handoff-test`、`probe`，以及 `connect 服务商`（给项目加一个模型）。每个项目的设置在它的
`.agents/orch.toml` 里；`examples/task.example.toml` 是一个任务文件的例子（`task add -f`）。

## 安全

- 讨论用的模型只读运行：Codex 用 `--sandbox read-only`，Claude Code 只开 Read、Glob、Grep 这几个读文件的工具。
  它们能读关联的项目文件夹，改不了任何东西。
- Crossview 从不替你批准、点击或回答任何东西：手机提醒只报告，Claude Code 钩子只推送，Cowork 只开发你批准的方案。
- Crossview 不读取、不复制命令行工具的登录文件；会话列表只读时间、文件夹和 token 数。API key 和 ntfy 访问令牌在 Windows 上加密保存。
- 网页版只放行这台电脑和你自己的 Tailscale 设备（加 `--lan` 还放行家里的局域网）。
- 只在打开旧的派活功能时适用：agent 会在自己的 worktree 里执行任意命令，请只用在你自己的仓库上。你自己的工作目录不会被改动，
  唯一的例外是「项目」页的「放进我的项目文件夹」按钮，而且只在你点它的时候。

## 更新记录

- **0.6.1**：项目改名为 Crossview（原名 MAO；旧的 `mao_*` 工具名仍然能用）。
  MCP 工具现在叫 `crossview_list_briefs`、`crossview_get_brief`、`crossview_report_progress`；`crossview-web` 和 `maoweb`
  一样打开网页版。`orch` 命令、`.maoweb` 数据文件夹和 `MAO_*` 环境变量的名字都不变，不用重新设置。
- **0.6.0**：Crossview 改成以讨论和决定为中心：GPT 和 Claude 在网页版里一轮轮讨论需求，由你拍板，Claude Cowork 通过 MCP
  领取批准的方案（`crossview_list_briefs`、`crossview_get_brief`、`crossview_report_progress`）。讨论等你决定、
  Cowork 遇到阻碍或有问题、Claude Code 等你批准时，通过 ntfy 推送到手机。旧的派活功能（「项目」页、`orch task/run/plan/decide`、`orch_*` 任务工具）
  默认关闭，可以重新打开；数据都保留。网页版里 0.5.0 的「分派给 Agent」和「写程序」换成了「项目」页（聊天里的「交给项目」、
  「放进我的项目文件夹」），它属于旧的派活功能。
- **0.5.0**：网页版公开（聊天、分派给 Agent、画图、视频、文档、写程序、Agent 面板，中文或英文）。`orch trace` 资金追踪、
  `orch find` 在一堆文件里找信息，都只在本机计算；MCP 服务提供 `office_trace` 和 `office_find`。
- **0.4.0**：老格式的 .doc、.xls、.wps 能通过 Word、Excel、WPS 或 LibreOffice 打开；MCP 服务加了办公工具（`office_sheet_ask`、`office_doc`）；办公功能的调用次数和 token 会记下来，`orch office` 和 `orch report` 里能看到（只记数量，不记内容）。
- **0.3.9**：`orch init` 生成的配置文件，语言是中文时注释是中文，其他语言时是英文。
- **0.3.8**：Gemini CLI 能在每个任务新建的 worktree 里运行（`--skip-trust`）；说明里写清楚它要用 API key 或付费方案，
  因为个人免费登录现在会被拒绝。
- **0.3.7**：Gemini CLI 可以当写代码的 agent（`executor = "gemini"`，池 `gemini_sub`），和 Claude Code、Codex 并列；
  审查和规划时用只读模式。脱敏统计只数真正发出去的内容。
- **0.3.6**：办公文档。`orch sheet` 用大白话问 Excel、CSV 表格（模型只看列名和几行样例，数字在你电脑上算），
  结果存成 Excel 并画图。`orch doc` 起草、润色 Word，按模板填写，可用公文版式。发出去前自动脱敏；
  敏感模式只用国内模型。
- **0.3.5**：`orch agents` 总控面板：每个 agent 正在做什么、今天和这周的用量、订阅额度、最近成功率和最近一次出错，
  可以暂停、恢复、换人。`orch sessions` 列出本机的 Claude Code、Codex 会话，包括你自己开的（只读时间、文件夹和 token 数）。
  orch 的提示支持中文、英文、日文（`orch lang`、`--lang`，默认跟随系统语言）。
- **0.3.4**：提前升级：一次运行什么都没改，或者验收报错和上一次完全一样，就马上换下一个模型（`[escalation] early_escalation`，默认打开）。
  README 写清楚了现在能用它做什么。
- **0.3.2**：`orch key` 和 `orch connect`：在命令行里用你自己的 DeepSeek、GPT、Claude、千问、Gemini、豆包账号
  （Windows 上 key 加密保存）；写清楚从下载到第一个任务的步骤。
- **0.3.1**：MCP 服务（`orch mcp`），可以在 Claude Desktop 等聊天应用里查看、添加、运行和跟进任务；
  合并冲突交给 agent 解决，没有冲突标记、验收通过才合并。
- **0.3.0**：首次公开发布：路由阶梯与升级、git worktree 隔离与交接、上下文管理器和版本化的项目记忆、
  每次调用的上下文清单、成本报表、只在测试薄弱处审查、审查走直接 API、单任务预算、节奏闸门、路由建议。

## 许可

[PolyForm Noncommercial 1.0.0](../LICENSE)：可以免费使用、修改、分享，但不能用于商业目的。
