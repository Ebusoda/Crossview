"use strict";
// English for the web page. The page is written in Chinese; in English mode every piece of interface text is
// swapped for its English as it appears (static page, things app.js draws, messages from the server).
// What people wrote or the AI answered (chat messages, documents, titles) is left alone: see SKIP.
// Choice: localStorage "mao.lang" ("zh" / "en"); first visit follows the browser language.

const I18N = (() => {
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch { return null; } };
  const saved = store("mao.lang");
  const lang = saved === "zh" || saved === "en" ? saved : (/^zh/i.test(navigator.language || "") ? "zh" : "en");

  const EN = {
    "分派给 Agent": "Assign to agents",
    "让 AI 分派并监督": "Let AI assign and supervise",
    "AI 会结合当前讨论拆分任务，交给项目中配置的 Agent。执行会使用这些 Agent 的订阅额度或 API 余额。": "AI will split this discussion into tasks for the agents configured in your project. Execution uses their subscription limits or API balance.",
    "在哪个项目里执行": "Project to work on",
    "这次要完成什么": "Goal for this assignment",
    "确认讨论后的最终目标，可在这里补充或修改": "Confirm the final goal; add or edit details here",
    "最多同时执行几个任务": "Maximum concurrent tasks",
    "AI 会分类任务、安排先后顺序。MAO 自动跟踪、验收，失败时按设置重试或换人；高风险成果仍按项目规则等待你确认合并。": "AI classifies tasks and orders dependencies. MAO tracks and checks results, retrying or handing off failures according to settings. High-risk changes still require merge confirmation under project rules.",
    "先预览计划": "Preview the plan",
    "开始分派并监督": "Start assignment and supervision",
    "AI 正在拆分任务": "AI is planning tasks",
    "Agent 正在执行": "Agents are working",
    "正在暂停": "Pausing",
    "计划已生成，等待执行": "Plan ready to execute",
    "全部完成": "All tasks complete",
    "需要处理": "Needs attention",
    "执行已中断": "Execution interrupted",
    "未完成": "Incomplete",
    "已通过验收，等待合并": "Checks passed; waiting for merge", "验收已通过": "checks passed",
    "已合并": "Merged",
    "遇到阻碍": "Blocked",
    "前端": "Frontend",
    "后端": "Backend",
    "测试": "Testing",
    "调研": "Research",
    "代码修改": "Code changes",
    "请先完成当前讨论，再分派任务": "Finish this discussion before assigning tasks",
    "请先在设置的写程序页面登记一个项目": "Register a project under Coding in Settings first",
    "已交给 AI 分派，进度会显示在当前对话里": "Assigned to AI. Progress will appear in this conversation.",
    "任务分派与监督": "Assignment and supervision",
    "执行全部任务": "Execute all tasks",
    "继续或重试": "Continue or retry",
    "确认合并并继续": "Confirm merge and continue",
    "查看分派和检查过程": "View assignment and checks",
    "订阅已连接": "Subscription connected",
    "尚未连接": "Not connected",
    "检查登录": "Check login",
    "正在检查…": "Checking\u2026",
    "设为默认聊天模型": "Use as default chat model",
    "已设为默认聊天模型": "Default chat model updated",
    "Claude（我的订阅）": "Claude (my subscription)",
    "使用你的 Claude 订阅额度。回答可能有错，重要的事请再核实。": "Uses your Claude subscription limits. Answers can be wrong; check anything important.",
    "里检查 Claude 订阅登录，或填一个 API key。": " to check your Claude subscription login or add an API key.",
    "已连接本机 Claude 订阅": "Connected to the local Claude subscription",
    "使用本机 Claude Code 的订阅登录，共享订阅额度；仅管理员账号可用。": "Uses the local Claude Code subscription login and shared subscription limits; available to the admin only.",
    "未找到 Claude Code，请在本机安装后重新打开 MAO。": "Claude Code was not found. Install it on this PC, then reopen MAO.",
    "请先在本机运行 claude auth login，用你的 Claude 订阅账号登录。": "Run claude auth login on this PC and sign in with your Claude subscription account.",
    "Claude 登录状态检查失败，请在本机运行 claude auth status。": "Login check failed. Run claude auth status on this PC.",
    "Claude 订阅仅供管理员本人使用": "The Claude subscription is available to the admin only",
    "Claude 订阅使用本机登录，不需要填写 API key": "Claude subscription uses the local login; no API key is needed",
    "当前已有一个 Claude 订阅聊天正在回复，请等它完成后再发送。": "A Claude subscription chat is already responding. Wait for it to finish.",
    "Claude 订阅额度暂时用完，请等待额度恢复。": "Claude subscription limit reached. Wait for it to reset.",
    // ---- page frame, chat --------------------------------------------------------------
    "收起": "Close", "功能": "Sections", "聊天": "Chat", "画图": "Pictures", "视频": "Video", "文档": "Documents",
    "写程序": "Coding", "新对话": "New chat", "最近": "Recent", "对话列表": "Conversations", "设置": "Settings",
    "修改密码": "Change password", "退出登录": "Sign out", "菜单": "Menu", "MAO · 全家的 AI 助手": "MAO · the family's AI helper",
    "有什么可以帮你？": "How can I help?", "输入消息": "Message", "选择模型": "Choose a model", "发送": "Send", "停止": "Stop",
    "帮我写一段给家人的生日祝福，温暖一点，不要太长。": "Write a short, warm birthday message for someone in my family.",
    "写生日祝福": "Birthday message", "帮我把下面这段话翻译成日语：": "Translate the following into Japanese:",
    "翻译成日语": "Into Japanese", "红烧肉怎么做？请一步一步说，写清楚用量。": "How do I make a simple stir-fry? Step by step, with amounts.",
    "这道菜怎么做": "A recipe", "画一张图": "Draw a picture", "做一段视频": "Make a video",
    "夜深了": "Good evening", "早上好": "Good morning", "中午好": "Good afternoon", "下午好": "Good afternoon", "晚上好": "Good evening",
    "还没有可用的模型": "No model available yet", "回答可能有错，重要的事请再核实。": "Answers can be wrong; check anything important.",
    "还没有可用的模型：请在": "No model available yet: please add an API key under", "里填一个 API key。": ".",
    "还没有可用的模型，请让管理员在设置里填 API key。": "No model available yet; ask the admin to add an API key in Settings.",
    "还没有对话": "No conversations yet", "复制": "Copy", "已复制": "Copied", "复制失败，请手动选择文字": "Could not copy; select the text by hand",
    "删除": "Delete", "（回答中断）": "(answer interrupted)",
    // ---- sign in, password -------------------------------------------------------------
    "欢迎回来": "Welcome back", "登录后开始使用": "Sign in to start", "用户名": "User name", "怎么称呼你": "What should we call you",
    "例如：妈妈": "for example: Mum", "密码": "Password", "登录": "Sign in", "第一次使用": "First time here",
    "先创建管理员账号（之后可以给家人加账号）": "Create the admin account first (you can add family members later)",
    "创建并进入": "Create and enter", "关闭": "Close", "原密码": "Current password", "新密码（至少 8 位）": "New password (at least 8 characters)",
    "取消": "Cancel", "保存": "Save", "密码已修改": "Password changed", "请先登录": "Please sign in first",
    "连不上 MAO：请检查网络。在外面用时，手机上要打开 Tailscale，家里的电脑要开着 MAO。":
      "Cannot reach MAO: check the network. Away from home, Tailscale must be on on your phone and MAO running on the home PC.",
    "打不开：": "Cannot open: ",
    // ---- settings ------------------------------------------------------------------------
    "画图视频": "Pictures & video", "家人": "Family", "外出": "Away from home",
    "已设置（来自环境变量）": "Set (from an environment variable)", "已设置": "Set", "未设置": "Not set",
    "API key 只保存在这台电脑上（Windows 加密），不会显示出来，也不会上传。填好一个服务商，就可以在聊天里选它的模型。":
      "API keys stay on this PC (encrypted by Windows); they are never shown or uploaded. Once a provider has a key, its models can be chosen in Chat.",
    "测试连接": "Test connection", "可选的模型": "Models to offer", "添加": "Add",
    "还没有模型，点「测试连接」可以看到这个 key 能用哪些": "No models yet; \"Test connection\" shows which ones this key can use",
    "已保存": "Saved", "删除这个 API key？": "Delete this API key?", "正在连接…": "Connecting…", "连接成功。": "Connected.",
    "每个人有自己的账号和对话记录，互相看不到。只有管理员能改设置。":
      "Everyone has their own account and conversations, which others cannot see. Only the admin can change settings.",
    "添加家庭成员": "Add a family member", "可以派写程序的任务": "May give coding tasks", "重设密码": "Reset password",
    "密码已重设": "Password reset", "已关闭写程序权限": "Coding turned off for this person", "已添加": "Added", "管理员": "admin",
    "画图和视频都用豆包（火山方舟）：先在「聊天」标签里填好豆包的 API key，再在这里列出要用的模型。 模型名在方舟控制台的「模型广场」里查，需要先在控制台开通。":
      "Pictures and video use Doubao (Volcano Engine Ark): add the Doubao API key on the Chat tab, then list the models here. Model names are in the Ark console's model square; enable them there first.",
    "画图模型（Seedream）": "Picture models (Seedream)", "视频模型（Seedance）": "Video models (Seedance)", "还没有模型": "No models yet",
    "在这里登记可以从网页派任务的项目（这台电脑上的文件夹）。AI 会在这台电脑上运行命令，所以只有管理员和你在「家人」里打开了「写程序」的人可以用。 项目要先在命令行里初始化一次：":
      "List the projects (folders on this PC) that may get tasks from the web page. The AI runs commands on this PC, so only the admin and people with Coding turned on under Family can use it. Initialise a project once on the command line first:",
    "添加项目": "Add a project", "正在检查…": "Checking…",
    "外出时用你自己的手机或平板打开 MAO。走的是 Tailscale（只有你登录过的设备能连上的私人网络），不对外开放端口，别人打不开。":
      "Open MAO from your own phone or tablet when away. It goes through Tailscale (a private network only your signed-in devices can join); no port is opened to the internet, so nobody else can open it.",
    "已经可以在外面打开": "Ready to open from outside", "已开启": "On", "手机连上 Tailscale 以后，在浏览器里打开：": "With Tailscale on, open this in the phone's browser:",
    "建议在手机浏览器里「添加到主屏幕」，以后点图标就能打开。家里人要用，也需要在他们的手机上装 Tailscale 并登录你的账号（或者你在 Tailscale 里邀请他们）。":
      "Tip: \"Add to Home Screen\" in the phone's browser makes it one tap away. Family members need Tailscale on their phones too, signed in to your account (or invited in Tailscale).",
    "复制失败，请手动抄下地址": "Could not copy; write the address down by hand", "还差一步": "One more step", "未开启": "Off",
    "重新打开，再回到这一页。": "to restart it, then come back to this page.", "还没有开启": "Not set up yet",
    "在这台电脑上安装 Tailscale（": "Install Tailscale on this PC (", "），用一个账号登录。": ") and sign in with an account.",
    "在你的手机上也安装 Tailscale（App Store / 应用商店搜 Tailscale），": "Install Tailscale on your phone too (search for Tailscale in the app store),",
    "用同一个账号": "with the same account", "登录，打开连接开关。": "and switch it on.",
    "关掉 MAO 的黑色窗口，双击": "Close MAO's black window, double-click", "重新打开。": "to restart it.",
    "回到这一页，就能看到手机上要打开的地址。": "Come back to this page to see the address to open on the phone.",
    // ---- pictures, video -------------------------------------------------------------------
    "写下想要的画面，AI 帮你画出来；也可以上传一张照片，告诉它想怎么改。": "Describe a picture and the AI draws it; or upload a photo and say how to change it.",
    "修改这张照片": "Edit this photo", "写下想怎么改，例如「背景换成海边」「变成水彩画」": "Say how, for example \"put it on a beach\" or \"make it a watercolour\"",
    "不修改照片了": "Do not edit the photo", "例如：水墨风格的江南小镇，清晨，有薄雾": "for example: a misty riverside town at dawn, ink-painting style",
    "想要的画面": "The picture you want", "上传照片": "Upload a photo", "尺寸": "Size", "画图模型": "Picture model", "开始画": "Draw",
    "我的作品": "My pictures", "写下想要的视频内容，AI 做成一段 5 或 10 秒的短片；也可以给一张图作为开头画面。":
      "Describe a video and the AI makes a 5 or 10 second clip; you can give a picture as the first frame.",
    "用这张图作为开头画面": "Start from this picture", "再写下画面接下来怎么动": "then say how it should move",
    "不用这张图了": "Do not use this picture", "例如：海浪慢慢拍打礁石，镜头缓缓推近，夕阳": "for example: waves slowly hitting rocks at sunset, the camera moving in",
    "想要的视频内容": "The video you want", "开头画面": "First frame", "从我的画里选": "Pick from my pictures", "画面比例": "Aspect ratio",
    "时长": "Length", "5 秒": "5 s", "10 秒": "10 s", "视频模型": "Video model", "开始制作": "Make it",
    "一段视频大约要等 1 到 5 分钟。可以先离开，做好了会出现在下面。": "A video takes about 1 to 5 minutes. You can leave; it appears below when ready.",
    "我的视频": "My videos", "方形": "Square", "竖版 3:4": "Portrait 3:4", "横版 4:3": "Landscape 4:3", "手机竖屏 9:16": "Phone 9:16",
    "横屏 16:9": "Wide 16:9", "高清 2K": "2K", "竖屏 9:16": "Portrait 9:16", "方形 1:1": "Square 1:1",
    "画图和视频要用豆包（火山方舟）的 API key。请在": "Pictures and video need a Doubao (Volcano Engine Ark) API key. Add it under",
    "设置 · 聊天": "Settings · Chat", "里填写「豆包」那一栏。": "in the Doubao box.",
    "画图和视频还没有开通，请让管理员在设置里填写豆包的 API key。": "Pictures and video are not set up yet; ask the admin to add the Doubao API key.",
    "还没有启用画图模型，请在": "No picture model enabled yet; add one under", "还没有启用视频模型，请在": "No video model enabled yet; add one under",
    "设置 · 画图视频": "Settings · Pictures & video", "里添加。": ".", "还没有可用的模型，请让管理员在设置里添加。": "No model available yet; ask the admin to add one.",
    "刚刚": "just now", "没有成功": "Did not work", "从图片": "From a picture", "改图": "Edited", "修改": "Edit", "做成视频": "Make a video",
    "再试一次": "Try again", "保存到手机 / 电脑": "Save to phone / PC", "修改这张": "Edit this one", "开始修改": "Edit",
    "这张照片打不开，请换一张 JPG 或 PNG 照片": "This photo cannot be opened; try a JPG or PNG", "正在画…": "Drawing…",
    "正在提交…": "Sending…", "删除这段视频？删除后找不回来。": "Delete this video? It cannot be undone.",
    "删除这张图？删除后找不回来。": "Delete this picture? It cannot be undone.", "正在加载…": "Loading…",
    "还没有图片。可以先去「画图」画一张，或者点「开头画面」上传照片。": "No pictures yet. Draw one under Pictures, or upload a photo with \"First frame\".",
    "选一张图": "Pick a picture", "正在制作，做好了会自动出现": "Making it; it appears here when ready",
    "还没有作品。写一句话，画第一张吧。": "No pictures yet. Write a line and draw the first one.", "还没有视频。": "No videos yet.",
    "查看大图": "View",
    // ---- coding ------------------------------------------------------------------------
    "把编程任务交给 AI 团队。它们在这台电脑上写代码、跑检查，通过了才合并进项目。":
      "Give programming tasks to the AI team. They write code and run checks on this PC; only work that passes is merged.",
    "项目": "Project", "难度": "Difficulty", "简单": "Easy", "一般": "Medium", "复杂": "Hard", "要做什么": "What to do",
    "一句话说清楚，例如：给统计结果加一个导出 Excel 的按钮": "One clear sentence, for example: add an \"export to Excel\" button to the statistics",
    "详细说明（可以不填）": "Details (optional)", "要求、例子、注意事项……写得越清楚，做得越准": "Requirements, examples, things to watch… the clearer, the better",
    "验收命令（可以不填）": "Acceptance commands (optional)", "每行一条，全部通过才算完成。不填就用项目里设好的检查。":
      "One per line; the task is done only when all pass. Empty = the project's own checks.",
    "交给 AI": "Hand to the AI", "任务": "Tasks", "排队中": "Queued", "进行中": "Running", "完成": "Done", "没有完成": "Not done",
    "等待开始": "waiting", "AI 正在写": "AI writing", "检查通过": "checks passed", "已合并进项目": "merged", "失败": "failed",
    "卡住了，需要人看一下": "stuck, needs a person", "已取消": "cancelled", "管理员还没有允许你派写程序的任务。": "The admin has not allowed you to give coding tasks yet.",
    "还没有登记项目。请在": "No project listed yet. Add one under", "设置 · 写程序": "Settings · Coding", "里登记一个 orch 项目。": ".",
    "管理员还没有登记可以写程序的项目。": "The admin has not listed a project for coding yet.", "还没有任务。": "No tasks yet.",
    "已经交给 AI，进度会显示在下面": "Handed to the AI; progress shows below", "查看过程记录": "Show the log",
    // ---- documents ---------------------------------------------------------------------
    "问 Excel 表格、写通知总结、改文章、按单位模板填 Word。姓名、电话、身份证号这类信息会先换成代号再发给 AI，结果里再换回来。资金追踪和找信息只在这台电脑上算，不发给 AI。":
      "Ask questions about Excel tables, write notices and summaries, edit writing, fill in your workplace's Word templates. Names, phone and ID numbers become codes before anything goes to the AI, and are put back in the result. Money tracing and file search run only on this PC and send nothing to the AI.",
    "做什么": "Task", "问表格": "Ask a table", "写文档": "Write", "改文档": "Edit", "填模板": "Fill a template",
    "资金追踪": "Trace money", "找信息": "Find in files", "选择银行流水（可以多选）": "Choose bank statements (several at once)",
    "选择要查的文件（可以多选）": "Choose the files to search (several at once)", "也可以找所有的：": "Or find every:",
    "身份证号": "ID number", "手机号": "Mobile number", "银行卡号": "Bank card number", "邮箱": "E-mail",
    "到账最多隔几天": "Days a transfer may take", "1 天内到账": "Arrives within 1 day", "3 天内到账": "Arrives within 3 days",
    "7 天内到账": "Arrives within 7 days", "15 天内到账": "Arrives within 15 days",
    "只列出所有词都出现的文件": "Only files that contain every word", "每个词都要有": "Every word",
    "（可以不填）已经知道的人或账号，用逗号隔开，就不算「未知去向」。例如：王五，6217001234123412345":
      "(optional) People or accounts you already know, separated by commas; they are not counted as unknown. For example: Wang Wu, 6217001234123412345",
    "要找什么？名字、号码或词，用空格或逗号隔开。例如：张三 6222021234567890123":
      "What should be found? Names, numbers or words, separated by spaces or commas. For example: Zhang San 6222021234567890123",
    "请先选择银行流水": "Choose the bank statements first", "请先选择要查的文件": "Choose the files to search first",
    "全部在这台电脑上算，没有发给任何 AI": "All computed on this PC; nothing was sent to any AI", "资金流向图": "Money flow chart",
    "选择表格": "Choose a table", "还没有选文件": "No file chosen yet", "常用模板（点一个，填几个空就行）": "Templates (pick one and fill a few boxes)",
    "要求": "Request", "润色": "Tidy up", "压缩到 500 字": "Shorten to 500 words", "更正式": "More formal", "改错别字": "Fix typos",
    "参考材料": "Reference files", "版式": "Layout", "普通版式": "Plain layout", "公文版式": "Official document layout",
    "只用国内的模型：DeepSeek、千问、豆包": "Only models based in China: DeepSeek, Qwen, Doubao", "敏感模式": "Sensitive mode",
    "开始": "Start", "另外要隐藏的词": "More words to hide",
    "人名、单位名等，用逗号隔开。身份证号、手机号、银行卡号、邮箱，以及「联系人：」这类后面的名字会自动隐藏。":
      "Names, organisations and so on, separated by commas. ID, phone and bank card numbers, e-mail addresses and names after labels like \"联系人：\" are hidden automatically.",
    "例如：张三，某某局": "for example: Zhang San, Some Bureau",
    "文件只存在这台电脑上。问表格时只发列名和前 5 行，数字在电脑上算；写、改、填会发你的要求和文档内容。发之前都会先隐藏个人信息。资金追踪和找信息完全不发给 AI。涉密文件不要用。":
      "Files stay on this PC. For a table only the column names and the first 5 rows are sent, and the numbers are computed here; writing, editing and filling send your request and the document. Personal details are hidden first. Money tracing and file search send nothing to the AI. Never use classified files.",
    "我的文档": "My documents",
    "想知道什么？例如：每个部门这个季度花了多少，从多到少，画个柱状图": "What do you want to know? For example: how much each department spent this quarter, largest first, as a bar chart",
    "要写什么？例如：写一份国庆值班安排的通知，10 月 1 日到 7 日，每天两人": "What should it write? For example: a notice with the holiday duty roster, Oct 1 to 7, two people a day",
    "选择要改的文档": "Choose the document", "怎么改？不写就是润色。例如：压缩到 800 字，语气正式一些": "How should it change? Empty = tidy up. For example: shorten to 800 words, more formal",
    "选择 Word 模板": "Choose the Word template",
    "要填什么？例如：十月月报，报告人王五，本月完成了三项工作……（模板里要填的地方写成 {{标题}}、{{日期}}）":
      "What goes in? For example: October report, by Wang Wu, three projects done… (in the template, write the places to fill as {{title}}, {{date}})",
    "正在做": "Working", "其他要求（可以不填），例如：语气亲切一点、控制在 300 字以内": "Anything else (optional), for example: a warmer tone, under 300 words",
    "文件太大了（最多 10 MB）": "The file is too big (10 MB at most)", "这个文件打不开": "This file cannot be opened",
    "里填一个 API key（DeepSeek 或千问就行）。": "(DeepSeek or Qwen is enough).",
    "还没有记录。上传一个表格问一句，或者让它写一份通知试试。": "Nothing yet. Upload a table and ask something, or let it write a notice.",
    "（没填）": "(empty)", "下载 Word": "Download Word", "下载 Excel": "Download Excel", "下载图表": "Download chart",
    "删除这条记录和它生成的文件？": "Delete this entry and the files it made?", "请至少填一项": "Fill in at least one box",
    "已经开始，做好了会出现在下面": "Started; the result appears below when ready",
    "请先选择表格": "Choose a table first", "请先选择要改的文档": "Choose the document first",
    "请先选择 Word 模板": "Choose the Word template first",
    "脱敏：没找到需要隐藏的个人信息": "Masking: no personal details found", "没有脱敏（--no-mask）": "Not masked",
    // template picker (names, hints, boxes)
    "请假条": "Leave request", "向单位或学校请假": "Ask your workplace or school for leave", "请假人": "Who",
    "例如：王五": "for example: Wang Wu", "交给谁": "To", "例如：办公室 / 三年级二班 李老师": "for example: the office / Ms Li, class 3-2",
    "请假类型": "Kind of leave", "事假 / 病假 / 年假": "personal / sick / annual", "起止时间": "From – to",
    "例如：10 月 15 日至 17 日，共 3 天": "for example: Oct 15 to 17, 3 days", "事由": "Reason", "例如：陪家人看病": "for example: taking a relative to the doctor",
    "通知": "Notice", "开会、放假、值班、活动安排": "Meetings, holidays, duty rosters, events", "发文单位": "Issued by",
    "例如：办公室": "for example: the office", "通知什么事": "About", "例如：国庆节放假和值班安排": "for example: the National Day holiday and duty roster",
    "时间": "When", "例如：10 月 1 日至 7 日": "for example: Oct 1 to 7", "地点": "Where", "可以不填": "optional", "对象": "For",
    "例如：各部门、全体员工": "for example: all departments, all staff", "例如：每天两人值班，保持电话畅通": "for example: two people on duty a day, phones on",
    "工作总结": "Work summary", "月度、季度、年度总结": "Monthly, quarterly or yearly", "时间段": "Period", "例如：2026 年第三季度": "for example: Q3 2026",
    "主要做了什么": "Main work", "一条一行": "one per line", "成绩和数字": "Results and figures",
    "例如：完成项目 3 个，节约经费 12 万元": "for example: 3 projects done, 120,000 yuan saved", "存在的问题": "Problems", "下一步打算": "Next steps",
    "会议纪要": "Meeting minutes", "把开会的内容整理成纪要": "Turn a meeting into minutes", "会议名称": "Meeting",
    "例如：十月工作例会": "for example: October staff meeting", "时间地点": "When and where", "例如：10 月 13 日上午，3 楼会议室": "for example: Oct 13 morning, 3rd-floor meeting room",
    "参会人员": "Present", "例如：张三、李四等 8 人": "for example: Zhang San, Li Si and 6 others", "讨论了什么、定了什么": "Discussed and decided",
    "要办的事（谁、什么时候）": "Actions (who, when)", "请示": "Request for approval", "向上级请求批准某件事": "Ask a superior to approve something",
    "以谁的名义": "On behalf of", "例如：后勤科": "for example: the logistics section", "报给谁": "To", "例如：局领导": "for example: the bureau leaders",
    "请示什么": "Asking for", "例如：购置办公电脑 5 台": "for example: buying 5 office computers", "理由": "Why",
    "例如：现有电脑使用超过 8 年，经常故障": "for example: the current ones are over 8 years old and often break",
    "具体内容（数量、金额等）": "Details (numbers, amounts)", "例如：预算 3 万元": "for example: a budget of 30,000 yuan",
    "情况说明": "Explanation", "说明一件事的经过和原因": "Explain what happened and why", "例如：财务室": "for example: the finance office",
    "什么事": "What", "例如：9 月报表延迟提交": "for example: the September report was late", "经过": "What happened", "按时间写": "in time order",
    "原因": "Cause", "已经怎么处理、以后怎么做": "What was done, what will change", "感谢信": "Thank-you letter",
    "感谢老师、医生、邻居、同事": "For teachers, doctors, neighbours, colleagues", "写给谁": "To",
    "例如：市人民医院心内科全体医护人员": "for example: all staff of the city hospital's cardiology ward", "为什么感谢": "What for",
    "具体的事，越具体越好": "the more specific, the better", "落款": "Signed", "例如：患者家属 王五": "for example: Wang Wu, a patient's relative",
    "祝福语": "Greeting", "生日、节日、婚礼、升学": "Birthdays, holidays, weddings, new schools", "送给谁": "For",
    "例如：妈妈 / 老同学": "for example: Mum / an old classmate", "什么场合": "Occasion", "例如：六十岁生日": "for example: a 60th birthday",
    "语气和长短": "Tone and length", "例如：温暖一点，三四句话": "for example: warm, three or four sentences",
    // ---- agents ------------------------------------------------------------------------
    "每个 AI 助手在做什么、用了多少、最近成功率；可以暂停、恢复，或者把排队的任务换给另一个。":
      "What each AI agent is doing, how much it used, its recent success rate; pause, resume, or hand a queued task to another one.",
    "空闲": "Idle", "工作中": "Working", "已暂停": "Paused", "暂时停用": "Resting", "暂停": "Pause", "恢复": "Resume",
    "里登记一个 orch 项目，这里就能看到它的 agent。": "to see its agents here.", "你在这台电脑上自己开的会话": "Sessions you opened on this PC",
    "今天还没有。只读时间、文件夹和用量，不看对话内容。": "None today. Only times, folders and usage are read, never the conversation.",
    "只做过审查": "reviews only", "还没跑过": "no runs yet", "今天": "Today", "7 天": "7 days", "成功率": "Success", "额度": "Quota",
    "需要看一下的任务": "Tasks that need a look", "换给它": "Hand over", "正在用": "in use", "订阅": "subscription", "按量付费": "pay as you go",
    "5 小时": "5 h", "换给": "Hand to",
    "豆包（火山方舟）": "Doubao (Volcano Engine Ark)", "通义千问（阿里云百炼）": "Qwen (Alibaba Cloud Bailian)",
    "填新的 key 可以替换": "Paste a new key to replace it", "粘贴 API key": "Paste the API key", "添加模型": "Add a model", "移除": "Remove",
    "登录用的名字，例如 mama": "Sign-in name, for example mum", "称呼，例如 妈妈": "What to call them, for example Mum",
    "初始密码（至少 8 位）": "First password (at least 8 characters)", "名字，例如 我的网站": "Name, for example my-site",
    "项目名字": "Project name", "项目文件夹": "Project folder",
    // ---- server messages ---------------------------------------------------------------
    "用户名或密码不对": "Wrong user name or password", "密码错误次数太多，请 10 分钟后再试": "Too many wrong passwords; try again in 10 minutes",
    "已经设置过了": "Already set up", "用户名 1–32 个字（字母、数字、汉字），密码至少 8 位": "User name: 1–32 letters, digits or Chinese characters; password: at least 8 characters",
    "原密码不对": "The current password is wrong", "新密码至少 8 位": "The new password needs at least 8 characters",
    "没有这个对话": "No such conversation", "消息是空的": "The message is empty",
    "API key 不对或已失效，请管理员在设置里检查": "The API key is wrong or expired; ask the admin to check Settings",
    "调用太频繁或余额不足，请稍后再试": "Too many calls or no balance left; try again later", "模型太久没有回答，请再试一次": "The model took too long; try again",
    "调用模型失败": "Calling the model failed", "请写下想要的画面": "Describe the picture", "请写下想要的视频内容": "Describe the video",
    "管理员还没有允许你派写程序的任务": "The admin has not allowed you to give coding tasks", "没有这个记录，或者还没做完": "No such entry, or it is not finished",
    "未知的服务商": "Unknown provider", "还没有填 API key": "No API key yet", "API key 不对": "Wrong API key", "这个用户名已经有了": "That user name is taken",
    "密码至少 8 位": "At least 8 characters", "不能删除自己": "You cannot delete yourself", "只有管理员可以操作": "Only the admin can do this",
    "请求太大": "The request is too big", "没有这个项目": "No such project", "不认识的操作": "Unknown action", "请写清楚要做什么": "Say what to do",
    "难度只能是 S / M / L": "Difficulty is S, M or L", "网页版重启时这个任务还没做完，请再提交一次": "The web app restarted before this finished; please send it again",
    "只能上传 Excel（.xlsx、.xls）、CSV、Word（.docx、.doc）、WPS、PDF 或文本文件（.txt、.md）": "Only Excel (.xlsx, .xls), CSV, Word (.docx, .doc), WPS, PDF or text files (.txt, .md)",
    "请先上传银行流水": "Upload the bank statements first", "请先上传要查的文件": "Upload the files to search first",
    "请写下要找什么，或者选一种号码": "Say what to find, or pick a kind of number",
    "这一项要用表格、Word、PDF 或文本文件": "This needs tables, Word, PDF or text files",
    "文件内容不对，请重新选择": "The file content is wrong; choose it again", "文件是空的": "The file is empty", "请先上传文件": "Upload a file first",
    "找不到上传的文件，请重新上传": "The uploaded file is gone; upload it again",
    "敏感模式要用国内模型：请管理员在设置里填 DeepSeek、千问或豆包的 API key": "Sensitive mode needs a model based in China: ask the admin to add a DeepSeek, Qwen or Doubao key",
    "还没有可用的模型：请管理员在设置里填一个 API key": "No model available yet: ask the admin to add an API key",
    "请写下想问什么": "Write your question", "请写下要写什么": "Say what to write", "请写下要填的内容": "Say what to fill in",
    "没有这个模板": "No such template", "画图和视频要用豆包（火山方舟）的 API key，请管理员在设置里填写": "Pictures and video need a Doubao API key; ask the admin to add it",
    "图片太大了（最多 10 MB）": "The picture is too big (10 MB at most)", "只支持 PNG、JPG、WebP 图片": "Only PNG, JPG and WebP pictures",
    "不支持的尺寸": "Size not supported", "找不到要修改的图片": "The picture to edit is gone", "不支持的画面比例或时长": "Ratio or length not supported",
    "找不到作为首帧的图片": "The first-frame picture is gone", "生成失败": "Generation failed", "等了 30 分钟还没做好，请再试一次": "Still not done after 30 minutes; try again",
    "豆包的 API key 不对或没有开通这个模型": "The Doubao key is wrong or the model is not enabled", "等太久没有回应，请再试一次": "No answer for too long; try again",
    "描述或图片里可能有不允许生成的内容，换个说法再试试": "The description or picture may contain something not allowed; try different wording",
  };

  // texts with values: [pattern, (match) => English]
  const tr = (s) => EN[s] || s;
  const PAT = [
    [/^出错了（(\d+)）$/, (m) => `Something went wrong (${m[1]})`],
    [/^删除「(.*)」？删除后找不回来。$/, (m) => `Delete "${m[1]}"? It cannot be undone.`],
    [/^给 (.+) 设一个新密码（至少 8 位）$/, (m) => `New password for ${m[1]} (at least 8 characters)`],
    [/^(.+) 现在可以写程序了$/, (m) => `${m[1]} can now give coding tasks`],
    [/^删除 (.+) 的账号和全部对话？$/, (m) => `Delete ${m[1]}'s account and all conversations?`],
    [/^(\d+) 分钟前$/, (m) => `${m[1]} min ago`], [/^(\d+) 小时前$/, (m) => `${m[1]} h ago`],
    [/^(\d+) 月 (\d+) 日$/, (m) => `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m[1] - 1]} ${m[2]}`],
    [/^这台电脑已经装好 Tailscale（地址 (.+)），但 MAO 这次没有在这个地址上打开。 请关掉 MAO 的黑色窗口，双击$/,
      (m) => `Tailscale is installed on this PC (address ${m[1]}), but MAO did not open on that address this time. Close MAO's black window, double-click`],
    [/^材料：(.+)$/, (m) => `Material: ${m[1]}`],
    [/^共 (\d+) 行，完整结果在 Excel 里$/, (m) => `${m[1]} rows in all; the full result is in the Excel file`],
    [/^这些没有填：(.+)，下载后在 Word 里补上。$/, (m) => `Left empty: ${m[1]}. Add them in Word after downloading.`],
    [/^看内容（约 (\d+) 字）$/, (m) => `Show the text (about ${m[1]} words)`],
    [/^(\d+) 个正在用$/, (m) => `${m[1]} in use`],
    [/^今天 (.+) token$/, (m) => `today ${m[1]} tokens`],
    [/^(\d+)%（最近 (\d+) 次）$/, (m) => `${m[1]}% (last ${m[2]})`],
    [/^(已暂停|暂时停用)，到 (.+)$/, (m) => `${tr(m[1])} until ${m[2]}`],
    [/^(.+) 已用 (\d+)%$/, (m) => `${tr(m[1])} ${m[2]}% used`],
    [/^(\d+) 次$/, (m) => `${m[1]} run${m[1] === "1" ? "" : "s"}`],
    [/^排队中，下一个：(.+)$/, (m) => `queued, next: ${m[1]}`],
    [/^暂停 (.+)？正在做的不会被打断，只是不再派新任务，直到你恢复。$/,
      (m) => `Pause ${m[1]}? Running work is not interrupted; it just gets no new tasks until you resume it.`],
    [/^(问表格|写文档|改文档|填模板|资金追踪|找信息)：([\s\S]*)$/, (m) => `${tr(m[1])}: ${m[2]}`],
    [/^已选 (\d+) 个文件$/, (m) => `${m[1]} file${m[1] === "1" ? "" : "s"} chosen`],
    [/^(\d+) 个文件$/, (m) => `${m[1]} files`],
    [/^(\d+) 天内到账$/, (m) => `arrives within ${m[1]} day${m[1] === "1" ? "" : "s"}`],
    [/^(早上好|中午好|下午好|晚上好|夜深了)，(.+)$/, (m) => `${tr(m[1])}, ${m[2]}`],
    [/^正在做：(.+)$/, (m) => `Working on: ${m[1]}`],
    [/^最近一次出错：(.+?)（([^（）]+)）$/, (m) => `Last error: ${m[1]} (${one(m[2]) ?? m[2]})`],
    [/^最近一次出错：(.+)$/, (m) => `Last error: ${m[1]}`],
    [/^(.+?) 申请(.*)$/, (m) => `Get a key at ${m[1]}` + m[2]
      .replace(/；模型名在方舟控制台的「模型广场 \/ 在线推理」里查/, "; model names are in the Ark console (model square / online inference)")
      .replace(/；可用的模型以「测试连接」列出的为准/, "; \"Test connection\" lists the models you can use")
      .replace(/（环境变量 (\S+) 优先）/, " (the environment variable $1 wins)")],
    [/^(.+) API key$/, (m) => EN[m[1]] ? `${EN[m[1]]} API key` : null],
    [/^添加模型名，例如 (.+)$/, (m) => `Add a model name, for example ${m[1]}`],
    [/^文件夹，例如 (.+)$/, (m) => `Folder, for example ${m[1]}`],
    [/^经手的模型：(.+)$/, (m) => `Models that worked on it: ${m[1]}`],
    [/^连接成功。这个 key 能用的模型：$/, () => "Connected. Models this key can use:"],
    [/^（点一下加入）$/, () => "(tap to add)"],
    [/^脱敏：(.+)（发出去的是占位符，结果里已换回原文）$/, (m) => `Masked: ${m[1].split("，").map(part).join(", ")} (codes were sent; the real values are back in the result)`],
    [/^请先(.+)$/, (m) => EN[m[1]] ? `${EN[m[1]]} first` : null],
    [/^约 (\d+) 字$/, (m) => `about ${m[1]} words`],
    [/^模型 (.+) 没有在设置里启用$/, (m) => `Model ${m[1]} is not enabled in Settings`],
    [/^(.+) 还没有设置 API key$/, (m) => `${m[1]} has no API key yet`],
    [/^连接失败：(.+)$/, (m) => `Could not connect: ${m[1]}`],
    [/^生成失败：(.+)$/, (m) => `Generation failed: ${m[1]}`],
    [/^视频做好了，但下载失败：(.+)$/, (m) => `The video was made but could not be downloaded: ${m[1]}`],
    [/^最多 (\d+) 个任务同时执行$/, (m) => `up to ${m[1]} tasks at once`],
    [/^已完成 (\d+)\/(\d+)$/, (m) => `done ${m[1]}/${m[2]}`],
    [/^先完成：(.+)$/, (m) => `after ${m[1]}`],
    [/^候选执行者：(.+)$/, (m) => `Candidate agents: ${m[1]}`],
  ];
  const MASK_KIND = { 姓名: "names", 手机号: "mobile numbers", 身份证号: "ID numbers", 银行卡号: "card numbers", 座机号: "landlines",
                      邮箱: "e-mails", 你指定的词: "your words" };
  function part(x) { const m = x.match(/^(.+) (\d+)$/); return m && MASK_KIND[m[1]] ? `${MASK_KIND[m[1]]} ${m[2]}` : x; }

  function one(s) {
    if (EN[s] !== undefined) return EN[s];
    for (const [re, fn] of PAT) { const m = s.match(re); if (m) { const r = fn(m); if (r !== null) return r; } }
    const n = s.match(/^(.*\S) (\d+)$/);                       // "已合并进项目 2" -> "merged 2"
    if (n && EN[n[1]] !== undefined) return `${EN[n[1]]} ${n[2]}`;
    return null;
  }
  function en(text) {
    const norm = text.replace(/\s+/g, " ").trim();
    if (!norm || !/[㐀-鿿]/.test(norm)) return null;
    let r = one(norm);
    if (r === null && norm.includes(" · ")) {                 // "支出.csv · 公文版式 · 4 分钟前"
      const parts = norm.split(" · ").map((p) => one(p) ?? p);
      if (parts.join(" · ") !== norm) r = parts.join(" · ");
    }
    if (r === null) return null;
    const lead = text.match(/^\s*/)[0], tail = text.match(/\s*$/)[0];
    return lead + r + tail;
  }

  // what people and the AI wrote stays as it is
  const SKIP = ".msg, .conv a, .doc-preview, .table-wrap, .doc-fields dd, .doc-title, figcaption p, #viewer-prompt, " +
               ".job-head h3, .t-title, .p-display, .chip, [data-no-tr], textarea, pre, code";
  const ATTRS = ["placeholder", "title", "aria-label"];

  function skip(node) { const el = node.nodeType === 1 ? node : node.parentElement; return !el || !!el.closest(SKIP); }
  // text inside SKIP stays as it is; labels on buttons there (aria-label, title) are still translated
  function walk(root, attrsOnly = false) {
    if (lang !== "en") return;
    if (root.nodeType === 3) {
      if (!attrsOnly && !skip(root)) { const r = en(root.nodeValue); if (r !== null && r !== root.nodeValue) root.nodeValue = r; }
      return;
    }
    if (root.nodeType !== 1 && root.nodeType !== 9) return;
    if (root.nodeType === 1) {
      for (const a of ATTRS) {
        const v = root.getAttribute(a);
        if (v) { const r = en(v); if (r !== null && r !== v) root.setAttribute(a, r); }
      }
      // a document-kind title keeps the request, only its label is translated
      if (!attrsOnly && root.matches(".job-head h3") && root.firstChild && root.firstChild.nodeType === 3) {
        const r = en(root.firstChild.nodeValue); if (r !== null && r !== root.firstChild.nodeValue) root.firstChild.nodeValue = r;
      }
      if (skip(root)) attrsOnly = true;
    }
    for (const c of root.childNodes) walk(c, attrsOnly);
  }

  function start() {
    document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
    if (lang !== "en") return;
    walk(document.body);
    new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === "childList") m.addedNodes.forEach((n) => walk(n));
        else if (m.type === "characterData") walk(m.target);
        else if (m.type === "attributes") walk(m.target);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    for (const name of ["confirm", "prompt", "alert"]) {
      const orig = window[name].bind(window);
      window[name] = (msg, ...rest) => orig(en(String(msg)) ?? msg, ...rest);
    }
  }

  return { lang, en, start, set: (l) => { store("mao.lang", l); location.reload(); }, EN };
})();
I18N.start();
