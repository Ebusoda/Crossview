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
    "正在暂停": "Pausing",
    "未完成": "Incomplete",
    "验收已通过": "checks passed",
    "已合并": "Merged",
    "前端": "Frontend",
    "后端": "Backend",
    "测试": "Testing",
    "调研": "Research",
    "代码修改": "Code changes",
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
    "未找到 Claude Code，请在本机安装后重新打开 Crossview。": "Claude Code was not found. Install it on this PC, then reopen Crossview.",
    "请先在本机运行 claude auth login，用你的 Claude 订阅账号登录。": "Run claude auth login on this PC and sign in with your Claude subscription account.",
    "Claude 登录状态检查失败，请在本机运行 claude auth status。": "Login check failed. Run claude auth status on this PC.",
    "Claude 订阅仅供管理员本人使用": "The Claude subscription is available to the admin only",
    "Claude 订阅使用本机登录，不需要填写 API key": "Claude subscription uses the local login; no API key is needed",
    "当前已有一个 Claude 订阅聊天正在回复，请等它完成后再发送。": "A Claude subscription chat is already responding. Wait for it to finish.",
    "Claude 订阅额度暂时用完，请等待额度恢复。": "Claude subscription limit reached. Wait for it to reset.",
    // ---- page frame, chat --------------------------------------------------------------
    "收起": "Close", "功能": "Sections", "聊天": "Chat", "画图": "Pictures", "视频": "Video", "文档": "Documents",
    "写程序": "Coding", "新对话": "New chat", "最近": "Recent", "对话列表": "Conversations", "设置": "Settings",
    "修改密码": "Change password", "退出登录": "Sign out", "菜单": "Menu", "Crossview · 全家的 AI 助手": "Crossview · the family's AI helper",
    "有什么可以帮你？": "How can I help?", "输入消息": "Message", "选择模型": "Choose a model", "发送": "Send", "停止": "Stop",
    "帮我写一段给家人的生日祝福，温暖一点，不要太长。": "Write a short, warm birthday message for someone in my family.",
    "写生日祝福": "Birthday message", "帮我把下面这段话翻译成日语：": "Translate the following into Japanese:",
    "翻译成日语": "Into Japanese", "红烧肉怎么做？请一步一步说，写清楚用量。": "How do I make a simple stir-fry? Step by step, with amounts.",
    "这道菜怎么做": "A recipe", "画一张图": "Draw a picture", "做一段视频": "Make a video",
    "夜深了": "Good evening", "早上好": "Good morning", "中午好": "Good afternoon", "下午好": "Good afternoon", "晚上好": "Good evening",
    "还没有可用的模型": "No model available yet", "回答可能有错，重要的事请再核实。": "Answers can be wrong; check anything important.",
    "还没有可用的模型：请在": "No model available yet: please add an API key under",
    "还没有可用的模型，请让管理员在设置里填 API key。": "No model available yet; ask the admin to add an API key in Settings.",
    "还没有对话": "No conversations yet", "复制": "Copy", "已复制": "Copied", "复制失败，请手动选择文字": "Could not copy; select the text by hand",
    "删除": "Delete", "（回答中断）": "(answer interrupted)",
    // ---- sign in, password -------------------------------------------------------------
    "欢迎回来": "Welcome back", "登录后开始使用": "Sign in to start", "用户名": "User name", "怎么称呼你": "What should we call you",
    "例如：妈妈": "for example: Mum", "密码": "Password", "登录": "Sign in", "第一次使用": "First time here",
    "先创建管理员账号（之后可以给家人加账号）": "Create the admin account first (you can add family members later)",
    "创建并进入": "Create and enter", "关闭": "Close", "原密码": "Current password", "新密码（至少 8 位）": "New password (at least 8 characters)",
    "取消": "Cancel", "保存": "Save", "密码已修改": "Password changed", "请先登录": "Please sign in first",
    "连不上 Crossview：请检查网络。在外面用时，手机上要打开 Tailscale，家里的电脑要开着 Crossview。":
      "Cannot reach Crossview: check the network. Away from home, Tailscale must be on on your phone and Crossview running on the home PC.",
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
    "添加家庭成员": "Add a family member", "重设密码": "Reset password",
    "密码已重设": "Password reset", "已添加": "Added", "管理员": "admin",
    "画图和视频都用豆包（火山方舟）：先在「聊天」标签里填好豆包的 API key，再在这里列出要用的模型。 模型名在方舟控制台的「模型广场」里查，需要先在控制台开通。":
      "Pictures and video use Doubao (Volcano Engine Ark): add the Doubao API key on the Chat tab, then list the models here. Model names are in the Ark console's model square; enable them there first.",
    "画图模型（Seedream）": "Picture models (Seedream)", "视频模型（Seedance）": "Video models (Seedance)", "还没有模型": "No models yet",
    "添加项目": "Add a project", "正在检查…": "Checking…",
    "外出时用你自己的手机或平板打开 Crossview。走的是 Tailscale（只有你登录过的设备能连上的私人网络），不对外开放端口，别人打不开。":
      "Open Crossview from your own phone or tablet when away. It goes through Tailscale (a private network only your signed-in devices can join); no port is opened to the internet, so nobody else can open it.",
    "已经可以在外面打开": "Ready to open from outside", "已开启": "On", "手机连上 Tailscale 以后，在浏览器里打开：": "With Tailscale on, open this in the phone's browser:",
    "建议在手机浏览器里「添加到主屏幕」，以后点图标就能打开。家里人要用，也需要在他们的手机上装 Tailscale 并登录你的账号（或者你在 Tailscale 里邀请他们）。":
      "Tip: \"Add to Home Screen\" in the phone's browser makes it one tap away. Family members need Tailscale on their phones too, signed in to your account (or invited in Tailscale).",
    "复制失败，请手动抄下地址": "Could not copy; write the address down by hand", "还差一步": "One more step", "未开启": "Off",
    "重新打开，再回到这一页。": "to restart it, then come back to this page.", "还没有开启": "Not set up yet",
    "在这台电脑上安装 Tailscale（": "Install Tailscale on this PC (", "），用一个账号登录。": ") and sign in with an account.",
    "在你的手机上也安装 Tailscale（App Store / 应用商店搜 Tailscale），": "Install Tailscale on your phone too (search for Tailscale in the app store),",
    "用同一个账号": "with the same account", "登录，打开连接开关。": "and switch it on.",
    "关掉 Crossview 的黑色窗口，双击": "Close Crossview's black window, double-click", "重新打开。": "to restart it.",
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
    "项目": "Projects", "难度": "Difficulty", "一般": "Medium", "要做什么": "What to do",
    "交给 AI": "Hand to the AI", "任务": "Tasks", "排队中": "Queued", "进行中": "Running", "完成": "Done", "没有完成": "Not done",
    "等待开始": "waiting", "AI 正在写": "AI writing", "检查通过": "checks passed", "已合并进项目": "merged", "失败": "failed",
    "卡住了，需要人看一下": "stuck, needs a person", "已取消": "cancelled",
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
    "空闲": "Idle", "工作中": "Working", "已暂停": "Paused", "暂时停用": "Resting", "暂停": "Pause", "恢复": "Resume",
    "你在这台电脑上自己开的会话": "Sessions you opened on this PC",
    "今天还没有。只读时间、文件夹和用量，不看对话内容。": "None today. Only times, folders and usage are read, never the conversation.",
    "只做过审查": "reviews only", "还没跑过": "no runs yet", "今天": "Today", "7 天": "7 days", "成功率": "Success", "额度": "Quota",
    "需要看一下的任务": "Tasks that need a look", "换给它": "Hand over", "正在用": "in use", "订阅": "subscription", "按量付费": "pay as you go",
    "5 小时": "5 h", "换给": "Hand to",
    "豆包（火山方舟）": "Doubao (Volcano Engine Ark)", "通义千问（阿里云百炼）": "Qwen (Alibaba Cloud Bailian)",
    "填新的 key 可以替换": "Paste a new key to replace it", "粘贴 API key": "Paste the API key", "添加模型": "Add a model", "移除": "Remove",
    "登录用的名字，例如 mama": "Sign-in name, for example mum", "称呼，例如 妈妈": "What to call them, for example Mum",
    "初始密码（至少 8 位）": "First password (at least 8 characters)",
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
    "网页版重启时这个任务还没做完，请再提交一次": "The web app restarted before this finished; please send it again",
    "只能上传 Excel（.xlsx、.xls）、CSV、Word（.docx、.doc）、WPS、PDF 或文本文件（.txt、.md）": "Only Excel (.xlsx, .xls), CSV, Word (.docx, .doc), WPS, PDF or text files (.txt, .md)",
    "请先上传银行流水": "Upload the bank statements first", "请先上传要查的文件": "Upload the files to search first",
    "请写下要找什么，或者选一种号码": "Say what to find, or pick a kind of number",
    "这一项要用表格、Word、PDF 或文本文件": "This needs tables, Word, PDF or text files",
    "这一项要用Excel 或 CSV 表格": "This needs an Excel or CSV table", "这一项要用Word 或文本文件": "This needs a Word or text file",
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
    // --- server texts ---
    "执行失败": "Something failed", "这个文件夹里的 .git 坏了，git 打不开它。": "The .git folder in here is broken; git cannot open it.",
    "找不到这次的方案，请重新商量。": "This plan cannot be found; plan again.", "这份方案已经失效，请重新商量。": "This plan is no longer valid; plan again.",
    "方案里没有要做的事。": "The plan has nothing to do.",
    // --- end of server texts ---
    // --- projects page (C-projects) ---
    "刚开始": "just started", "交给项目": "Hand to a project", "AI 团队先商量出方案给你看，你同意了才动手。": "The AI team first works out a plan for you to see; they start only once you agree.",
    "交给哪个项目": "Which project", "这次要做什么？": "What should be done this time?",
    "写清楚要做什么，例如：给计算器加上减法，并补上测试": "Say clearly what to do, for example: add subtraction to the calculator, with tests",
    "带上这次聊天里的讨论": "Include the discussion from this chat", "开始商量": "Start planning",
    "可以让 AI 在项目里写程序": "May let the AI write code in projects", "已关闭项目权限": "Projects turned off for this person",
    "还没有项目。请在": "No project yet. Add one on the", "页添加一个，这里就能看到它的 agent。": "page to see its agents here.",
    "把程序交给 AI 团队做。每一步在做什么都看得到，关键的地方会停下来等你。":
      "Hand programming work to the AI team. You can see every step, and it stops for you at the important points.",
    "等你": "your turn", "等着": "waiting", "等你决定": "Waiting for you", "停下来了": "Stopped", "用不了": "Unavailable", "新任务": "New task", "已准备好": "Ready",
    "商量": "Plan", "动手": "Build", "检查": "Check", "合并": "Merge",
    "看看": "Open", "去看看": "Take a look", "看过程": "Watch", "上次做的：": "Last task: ", "还没有交代过任务。": "No task given yet.",
    "空着": "is free", "有人正在用这个项目，做完后才能开始新任务。": "Someone else is using this project; a new task can start when they are done.",
    "选这台电脑上的一个文件夹就行。还没准备好的文件夹，Crossview 会自己准备好，不用再开命令行。":
      "Just pick a folder on this PC. If it is not ready yet, Crossview prepares it; no command line needed.",
    "还没有项目。添加一个文件夹，就可以把程序交给 AI 团队做。": "No project yet. Add a folder and the AI team can write programs in it.",
    "管理员还没有添加项目。添加以后，这里就能把程序交给 AI 团队做。":
      "The admin has not added a project yet. Once there is one, you can hand programming work to the AI team here.",
    "方案出来了，等你看一下：": "The plan is ready for you to look at: ", "检查通过，等你点「合并」：": "Checked and passed; waiting for you to merge: ",
    "遇到问题，要你看一下：": "Ran into a problem; please take a look: ", "停下来了，等你决定怎么办：": "Stopped; you decide what happens next: ",
    "方案出来了，等着同意：": "The plan is ready, waiting for approval: ", "检查通过，等着合并：": "Checked and passed, waiting to be merged: ",
    "遇到问题，等着处理：": "Ran into a problem, waiting to be handled: ", "停下来了：": "Stopped: ", "正在暂停：": "Pausing: ",
    "在看项目、出方案": "is reading the project and drafting a plan", "正在准备商量方案": "Getting ready to plan",
    "Crossview 正在验收：": "Crossview is running the checks: ", "Crossview 正在合并：": "Crossview is merging: ", "等下一个 AI 空出来：": "Waiting for the next free AI: ",
    "马上开始：": "Starting soon: ", "正在检查：": "is reviewing: ", "正在检查这一项：": "Being checked: ", "正在写：": "is writing: ",
    "正在动手：": "Building: ", "刚才：": "Just now: ", "不到 1 分钟": "under a minute",
    "正在看": "is reading", "正在看目录": "is listing a folder", "正在找": "is searching", "正在改": "is editing", "正在写": "is writing",
    "正在删": "is deleting", "正在运行": "is running", "正在查资料": "is looking things up", "正在列计划": "is making a to-do list",
    "正在派帮手": "is sending a helper", "正在用工具": "is using a tool",
    "看了": "read", "看了目录": "listed", "找了": "searched", "改了": "edited", "写了": "wrote", "删了": "deleted", "运行了": "ran",
    "查了资料": "looked up", "列了计划": "made a to-do list", "派了帮手": "sent a helper", "用了工具": "used a tool",
    "出了方案": "drafted a plan", "检查通过了": "approved it", "要求改一下": "asked for changes", "做完了，验收通过": "finished; checks passed",
    "没做成": "did not finish", "合并好了": "merged it", "提了一个问题": "asked a question",
    "暂停了，点「继续做」接着来：": "Paused; tap \"Continue\" to carry on: ", "暂停了：": "Paused: ",
    "正在停下来：": "Stopping: ", "正在停下来": "Stopping",
    // one project
    "项目设置": "Project settings", "改名字": "Rename", "从列表里移除": "Remove from the list", "这个项目的 AI": "This project's AI",
    "这个项目能用的 AI": "AI available to this project", "还没有能用的 AI。请检查项目的 .agents/orch.toml。": "No AI available yet. Check the project's .agents/orch.toml.",
    "没装好": "Not installed", "按量": "pay per use", "谁来做：Crossview 安排": "Who does it: Crossview decides",
    "负责出方案": "drafts the plan", "负责写代码": "writes the code", "负责检查": "reviews the work",
    "只跑验收命令": "acceptance commands only", "暂时没有能用的 AI": "no AI available right now",
    "检查的总是另一家公司的 AI，不让它自己查自己。": "The reviewer is always an AI from another company, so no AI checks its own work.",
    "现在没有别家的 AI 能检查，只会跑验收命令。": "No AI from another company can review right now; only the acceptance commands will run.",
    "什么时候停下来给你看": "When to stop and show you", "每做完一步都停（推荐）": "Stop after every step (recommended)",
    "商量好计划、每一项检查完，都先给你看，你点了才做下一步": "After the plan and after each item is checked, it shows you first and goes on only when you say so",
    "只在开始前看计划": "Only show me the plan first", "计划通过后自动做完、自动合并，出问题才叫你": "Once you approve the plan, it finishes and merges by itself and calls you only on problems",
    "商量的时候只看文件，不会改动项目。": "While planning they only read files; nothing in the project changes.",
    "例如：给计算器加上减法，并补上测试。要能算负数和小数。": "For example: add subtraction to the calculator, with tests. It must handle negative numbers and decimals.",
    "这件做完（或者点「不做了」）以后，才能交代新任务。": "A new task can start once this one is finished or dropped.",
    "做过的": "Done before", "还没有做过任务。": "No tasks yet.", "放弃了": "Dropped", "合并了一部分": "Partly merged", "没有做成": "Not finished", "要你看一下": "Needs you",
    "停在了一半": "Stopped halfway", "等你看方案": "Plan waiting for you", "现在用不了": "unavailable now",
    "这个项目现在用不了。": "This project cannot be used right now.", "这个项目现在打不开。": "This project cannot be opened right now.",
    "可以检查一下文件夹，或者从列表里移除后重新添加。": "Check the folder, or remove it from the list and add it again.",
    "给这个项目起个新名字": "A new name for this project", "已改名": "Renamed", "已从列表里移除": "Removed from the list",
    // adding a project
    "选这台电脑上的一个文件夹。还没准备好的文件夹，Crossview 会自己准备好（保存版本、写好设置），不用再开命令行。":
      "Pick a folder on this PC. If it is not ready yet, Crossview prepares it (versions, settings); no command line needed.",
    "文件夹": "Folder", "浏览": "Browse", "上一级": "Up", "我的文件夹": "My folder", "这里面没有文件夹。": "No folders in here.",
    "文件夹太多，只列出了前 300 个。": "Too many folders; only the first 300 are listed.", "用这个文件夹": "Use this folder",
    "这个文件夹不能添加：": "This folder cannot be added:", "这个文件夹还不存在。": "This folder does not exist yet.",
    "可以添加。Crossview 会这样准备它：": "It can be added. Crossview will prepare it like this:", "可以添加，它已经准备好了。": "It can be added; it is ready already.",
    "这个文件夹现在不能直接添加。": "This folder cannot be added as it is.", "详细记录": "Details",
    "先把我还没保存的改动存一个版本": "Save my unsaved changes as a version first", "正在准备…": "Preparing…",
    "新建这个文件夹": "Create this folder", "让它能保存版本（git）": "Let it keep versions (git)", "把现在的文件存成第一个版本": "Save the current files as the first version",
    "把你还没保存的改动存成一个版本": "Save your unsaved changes as a version", "写好 Crossview 的设置（.agents 文件夹）": "Write Crossview's settings (the .agents folder)",
    "加到项目列表里": "Add it to the project list",
    "请填写完整的文件夹路径（从盘符或 / 开始）。": "Enter the full folder path (starting with a drive letter or /).",
    "不能把整个磁盘当成项目，请选里面的一个文件夹。": "A whole disk cannot be a project; pick a folder on it.",
    "这是你的个人文件夹（或者包含它），里面有私人文件，不能整个交给 AI。请选一个专门放项目的文件夹。":
      "This is your personal folder (or contains it). It holds private files and cannot be handed to the AI. Pick a folder just for the project.",
    "这是你的个人文件夹（或者包含它），里面有很多私人文件，不能交给 AI。请选一个专门放项目的文件夹。":
      "This is your personal folder (or contains it). It holds many private files and cannot be handed to the AI. Pick a folder just for the project.",
    "这是放私人文件的地方（桌面、文档、下载等），不能整个交给 AI。请在里面新建一个专门的项目文件夹。":
      "This is where private files live (Desktop, Documents, Downloads…); it cannot be handed to the AI as a whole. Make a folder just for the project inside it.",
    "这是 Crossview 自己的文件夹，不能当成项目。项目可以放在 Crossview 的 playground 文件夹里。": "This is Crossview's own folder and cannot be a project. Projects can go in Crossview's playground folder.",
    "这是 Crossview 给 AI 干活用的临时文件夹，不能当成项目。": "This is a temporary folder where Crossview's AI works; it cannot be a project.",
    "这个文件夹在另一个项目（git 仓库）里面。请直接添加外面那个文件夹：": "This folder is inside another project (a git repository). Add the outer folder instead:",
    "这台电脑还没有安装 git。先安装 git，再来添加项目。": "Git is not installed on this PC. Install git first, then add the project.",
    "git 打不开这个文件夹。": "Git cannot open this folder.", "找不到这个文件夹，它的上一级文件夹也不存在。": "This folder was not found, and neither was the folder above it.",
    "这是一个文件，不是文件夹。": "This is a file, not a folder.", "这个文件夹已经添加过了：": "This folder has already been added as:",
    "这个文件夹里面已经有一个项目了，请直接用那个项目：": "There is already a project inside this folder; use that one:",
    "这个文件夹里的文件太多了（超过 5 万个），不适合交给 AI。请选一个小一点的文件夹。": "This folder has too many files (over 50,000) for the AI. Pick a smaller folder.",
    "有些改动还没有保存成版本。AI 会从上一次保存的版本开始做，看不到这些改动。":
      "Some changes are not saved as a version yet. The AI starts from the last saved version and will not see them.",
    "这些大文件或文件夹不会放进给 AI 用的版本：": "These big files or folders stay out of the version the AI works on:",
    "这些像是密码或密钥的文件不会放进给 AI 用的版本：": "These files look like passwords or keys and stay out of the version the AI works on:",
    "文件很多，第一次准备会慢一些。": "There are many files, so the first preparation takes a while.",
    "这些子文件夹自己也是 git 仓库，AI 看不到里面的文件：": "These subfolders are git repositories of their own; the AI cannot see the files in them:",
    // the chat hand-off
    "做完了：": "Done: ", "已经不做了：": "Dropped: ", "等你决定，点开看看。": "Waiting for your decision; open it to see.",
    "商量好了会在这里和项目页提醒你。点开看它们在说什么。": "You will be told here and on the project page when the plan is ready. Open it to see what they are saying.",
    "需要你决定时会在这里和项目页提醒你。点开看它们在做什么。": "You will be told here and on the project page when a decision is needed. Open it to see what they are doing.",
    "等 AI 说完，再交给项目": "Wait for the AI to finish answering, then hand it over",
    "先在这里聊一聊要做什么，再交给项目": "Talk about what should be done here first, then hand it to a project",
    "还没有项目。请先在「项目」页添加一个。": "No project yet. Add one on the Projects page first.", "管理员还没有添加项目。": "The admin has not added a project yet.",
    "还有一件没做完": "a task is not finished", "别人正在用": "someone else is using it", "有人在用": "In use",
    "现在每个项目都在做别的事。等它们做完，或者先在项目页点「不做了」。": "Every project is busy with something else. Wait until it is done, or drop that task on the project page first.",
    "已交给项目。商量好了会在这里提醒你。": "Handed to the project. You will be told here when the plan is ready.",
    // server messages (projects, jobs)
    "项目列表的格式不对": "The project list is not in the right format", "请给项目起个名字": "Give the project a name",
    "这个项目还有没做完的任务，先把它做完或点「不做了」": "This project has an unfinished task; finish or drop it first",
    "找不到这个文件夹，可能被移走或删掉了。": "The folder cannot be found; it may have been moved or deleted.",
    "这个文件夹还没有准备好（缺少 .agents/orch.toml），请重新添加。": "This folder is not ready (.agents/orch.toml is missing); add it again.",
    "git 太久没有回应，请稍后再看。": "Git took too long to answer; look again later.",
    "这个文件夹还不存在。要新建它，请勾选「新建这个文件夹」。": "This folder does not exist yet. To create it, tick \"Create this folder\".",
    "打不开这个文件夹": "This folder cannot be opened",
    "整理文件花的时间太长，没有完成。可以先把不需要的大文件夹移走再试。": "Sorting the files took too long and did not finish. Move big folders that are not needed elsewhere, then try again.",
    "当前状态不能执行这个操作": "This cannot be done right now", "没有这个任务": "No such task",
    "要做的事写得太长了，最多 8000 字": "The task is too long (8,000 characters at most)", "同时做的项数必须是 1 到 4": "Items at once must be 1 to 4",
    "这个项目现在用不了": "This project cannot be used right now", "写得太长了，最多 4000 字": "Too long (4,000 characters at most)",
    "这个项目已经从列表里移除了": "This project has been removed from the list", "请写下要怎么改": "Say what should change",
    "这一项不属于这个任务": "This item does not belong to this task", "这一项现在不能这样操作": "This item cannot do that right now",
    "找不到这个项目的文件夹": "The project's folder cannot be found", "读不到这个项目的记录": "The project's records cannot be read",
    "读不到这个项目的设置": "The project's settings cannot be read", "没有这条记录": "No such record",
    "网页服务已重启。请检查任务进度，再决定是否继续；不会自动重复执行。": "The web app restarted. Check the progress, then decide whether to go on; nothing is repeated automatically.",
    "后台进程意外停止了。请看一下进度，再决定是否继续。": "The background process stopped unexpectedly. Look at the progress, then decide whether to go on.",
    // --- end of projects page (C-projects) ---
    // --- job view (C-job) ---
    "现在": "Now", "计划": "The plan", "用量": "Usage", "实时": "Live", "继续": "Continue", "正在暂停…": "Pausing…", "不做了": "Drop task",
    "进度": "Progress", "这次要做的": "Items", "这次任务的情况": "About this task", "改动": "Changes", "回到项目": "Back to the project",
    "第 1 步：商量方案": "Step 1: Plan", "第 2 步：动手": "Step 2: Build", "第 3 步：检查": "Step 3: Check", "第 4 步：合并": "Step 4: Merge",
    "全部做完了": "All done", "已经不做了": "Dropped", "已经暂停": "Paused", "自动合并": "auto merge", "合并前给你看": "you approve",
    "Crossview 正在验收：运行测试": "Crossview is running the checks", "Crossview 正在合并": "Crossview is merging", "等下一个 AI 空出来": "Waiting for the next free AI",
    "正在写代码": "is writing code", "正在修改": "is making changes", "正在检查改动": "is reviewing the changes",
    "正在解决合并冲突": "is resolving a merge conflict", "正在工作": "is working", "命令：": "Command: ",
    "订阅，不另外花钱": "subscription, no extra cost", "按量计费": "pay per use",
    "Crossview 在收尾": "Crossview is wrapping up", "现在：": "Now: ", "还没用 token": "No tokens used yet",
    // activity rows
    "看": "Read", "列出": "List", "找": "Search", "改": "Edit", "写": "Write", "删": "Delete", "运行": "Run", "查资料": "Look up",
    "列计划": "To-do list", "派帮手": "Helper", "用工具": "Tool", "出错": "Error", "额度用完": "Out of quota", "看任务说明": "Read the task brief",
    "记下进度": "Update the progress notes", "写检查意见": "Write the review", "写任务笔记": "Write task notes",
    "进行中…": "Running…", "二进制": "binary", "看原始记录（命令和输出）": "Raw log (commands and output)",
    "最后的回复": "Final reply", "出错信息": "Error message", "Crossview 验收的输出": "Output of Crossview's checks", "检查报告": "Review report",
    "没有记录。": "Nothing recorded.", "正在看改动…": "Looking at the changes…", "开始了…": "Started…",
    "没读懂它的检查结果。": "Could not read its review.", "这一轮没有正常结束。": "This round did not end normally.",
    // roles and states
    "出方案": "Plan", "写代码": "Code", "修改中": "Fixing", "解决冲突": "Conflict", "写代码中": "Coding", "检查中": "Reviewing",
    "解决冲突中": "Resolving a conflict", "做完了": "Done", "等着上场": "Up next", "验收中": "Running checks", "合并中": "Merging",
    "等 AI 空出来": "Waiting for a free AI", "做好了，等合并": "Done, waiting to merge", "卡住了": "Stuck",
    "要改": "Must fix", "小问题": "Minor", "通过": "Passed", "没通过": "Failed", "Crossview 自己又跑了一遍验收": "Crossview ran the checks again itself",
    "Crossview 合并好了": "Crossview merged it", "有一条记录。": "A note was recorded.", "看全文": "Show all", "带上了聊天里的讨论": "with the chat discussion",
    "你": "You", "商量方案": "Planning", "没有商量完": "Planning did not finish", "来写": "writes it", "写的": "wrote it", "来检查": "reviews it", "会改": "Will change",
    "验收通过": "checks passed", "验收没通过": "checks failed", "展开": "Show",
    "没有做出计划。": "No plan was made.", "还在商量，计划出来后会列在这里。": "Still planning; the items show here once there is a plan.",
    "读不到这件事的全部记录。": "Could not read all of this task's records.",
    // what the user decided
    "同意了，照这个做": "Approved: go ahead", "我要改方案": "Asked for a new plan", "再改改": "Revise", "这一项不要了": "Drop this item",
    "照样合并": "Merge anyway", "暂停了": "Paused", "继续做": "Continue", "不要了": "Drop it", "做了决定": "Made a decision",
    "改成：只在开始前看计划": "Changed to: only show me the plan first", "改成：每做完一步都停": "Changed to: stop after every step",
    "等这一步做完就合并": "Merges when this step is done", "等这一步做完就去改": "Revises when this step is done",
    "等这一步做完就不要它": "Dropped when this step is done", "等这一步做完就再试一次": "Tries again when this step is done",
    "等这一步做完就照样合并": "Merges anyway when this step is done", "等这一步做完就执行": "Runs when this step is done",
    "好的，开始动手": "OK, building now", "已经发给它们，会重新出方案": "Sent; they will make a new plan", "好的，正在合并": "OK, merging",
    "已经发给它们": "Sent to them", "好的，再试一次": "OK, trying again", "好的，照样合并": "OK, merging anyway", "这件事不做了": "This task was dropped",
    "好的，接着做": "OK, continuing", "改好了，下一步开始时生效": "Changed; takes effect when the next step starts",
    "记下了，等这一步做完就执行": "Noted; it runs when this step is done", "好的": "OK", "请写下你的回答": "Write your answer",
    // notes in the conversation
    "这次没有别家的 AI 检查，只跑了验收命令。": "No AI from another company reviewed this; only the acceptance commands ran.",
    "这一项卡住了，停了下来。": "This item got stuck and stopped.", "这一项没做成。": "This item did not get done.",
    "Crossview 重启过，这一步停在了一半。": "Crossview restarted; this step stopped halfway.", "合并时出了问题，已经撤销。": "Merging went wrong and was undone.",
    "换了另一个 AI 接着做。": "Another AI took over.", "这一项不做了。": "This item was dropped.", "商量方案没有成功。": "Planning did not work out.",
    "Crossview 内部出了点问题。": "Something went wrong inside Crossview.", "你的操作没能执行。": "Your action could not be carried out.",
    "Crossview 重启过，之前排着的操作没有执行。": "Crossview restarted; the action that was waiting did not run.",
    "它没能做完这一轮。": "It could not finish this round.", "它的额度用完了，这一轮停了下来。": "It ran out of quota; this round stopped.",
    "它没有登录（或者 key 不对），这一轮没能开始。": "It is not signed in (or the key is wrong); this round could not start.",
    "做得太久了，Crossview 让它停了下来。": "It took too long; Crossview stopped it.", "它意外退出了。": "It quit unexpectedly.",
    "这台电脑上找不到它。": "It is not installed on this PC.", "这一轮被打断了。": "This round was interrupted.",
    // the cards where the user decides
    "它们已经停下来了": "They have stopped", "其他的还在做": "The rest are still going", "继续后会执行：": "After you continue: ",
    "照这个做": "Go ahead", "我要改": "Change it", "点「我要改」写一句意见，它会带着你的意见重新出方案。": "Tap \"Change it\" to write a note; it makes a new plan with your note.",
    "怎么算做完：运行": "Done when: run", "，全部通过。": ", and all pass.", "出方案的 AI 提醒：": "The planner notes:",
    "没有改动文件。": "No files changed.", "看全部改动": "See all changes", "还改了：": "Also changed:", "用订阅": "on subscription", "没有花钱": "No cost",
    "合并时出了问题，已经撤销": "Merging went wrong and was undone", "让它修一下": "Let it fix this", "再合并一次": "Merge again",
    "合并，做下一项": "Merge, then the next item", "点「再改改」写一句意见，它改完会再检查一遍。": "Tap \"Revise\" to write a note; after the change it is reviewed again.",
    "这次没有别家的 AI 检查": "No AI from another company reviewed it",
    "，做完再停下来给你看。": ", then it stops to show you.", "这是最后一项，合并后就全部做完了。": "This is the last item; merging it finishes the task.",
    "测试结果": "Tests", "检查结果": "Review", "用时": "Time", "花费": "Cost", "回答它": "Answer it", "它问：": "It asks:", "重新商量": "Plan again",
    "检查一直没通过，停下来了": "The review kept failing, so it stopped", "它有个问题要问你": "It has a question for you",
    "现在没有能用的 AI": "No AI is available right now", "试了好几次还没做成": "Several tries, still not done", "花费到上限了": "The cost limit was reached",
    "没有安排谁来做这类事": "Nobody is set to do this kind of work", "Crossview 出了点问题": "Something went wrong in Crossview", "停下来了，要你看一下": "Stopped; please take a look",
    "检查的 AI 改了几轮还是不同意。可以写一句意见让它再改，也可以照样合并或者不要这一项。":
      "The reviewing AI still does not agree after several rounds. Write a note so it changes it again, or merge anyway, or drop this item.",
    "它停下来等你回答。点「回答它」写下你的回答，它会接着做。": "It stopped to wait for your answer. Tap \"Answer it\" to write your answer and it carries on.",
    "能做这件事的 AI 现在都用不了（可能额度用完了，或者没登录）。过一会儿再试一次。":
      "None of the AIs that can do this is available now (out of quota or not signed in). Try again in a while.",
    "做了好几轮都没有通过验收。可以写一句意见再改，或者不要这一项。": "Several rounds did not pass the checks. Write a note so it tries again, or drop this item.",
    "这一项花的钱到了设定的上限。": "This item reached its cost limit.", "项目设置里没有安排谁来做这类事。": "The project settings name nobody for this kind of work.",
    "Crossview 在安排这一项时出了错。可以再试一次。": "Crossview ran into an error setting up this item. You can try again.", "这一项停了下来。": "This item stopped.",
    "商量没成功": "Planning did not work out", "Crossview 重启过，这件事停在了一半": "Crossview restarted; this task stopped halfway",
    "出了问题，停下来了": "Something went wrong, so it stopped", "剩下的几项做不了了": "The remaining items cannot be done",
    "出方案的 AI 没能给出能用的方案。": "The planning AI could not come up with a usable plan.", "它们停在了这里。": "They stopped here.",
    "可以接着做，或者重新商量。": "You can continue, or plan again.", "点「继续做」接着来，或者不做了。": "Tap \"Continue\" to carry on, or drop the task.",
    "可以重新商量，或者不做了。": "You can plan again, or drop the task.",
    "没有可以接着做的了。点「不做了」结束这件事。": "Nothing is left to continue. Tap \"Drop task\" to end it.",
    "Crossview 在做这件事时出了错。": "Crossview ran into an error on this task.",
    "剩下的项要用到前面没做成（或者不要了）的那一项，做不了了。": "The remaining items need an earlier item that was not done (or was dropped), so they cannot be done.",
    "它的改动和项目里别的改动冲突了，合并不进去。点「让它修一下」，它会把冲突解决掉。":
      "Its changes clash with other changes in the project and cannot be merged. Tap \"Let it fix this\" and it resolves the clash.",
    "合并到一起以后，验收没通过。点「让它修一下」，它会接着修好。": "After merging, the checks failed. Tap \"Let it fix this\" and it fixes them.",
    "合并的时候出错了。可以再合并一次，或者让它修一下。": "Merging ran into an error. Merge again, or let it fix this.",
    // 放进我的项目文件夹
    "放进我的项目文件夹": "Put it in my project folder", "确定": "OK", "先不放": "Not now", "正在放…": "Putting it in…",
    "新文件": "New file", "会更新": "Will be updated", "会删掉": "Will be removed",
    "你的项目文件夹里会有这些变化：": "These changes will be made in your project folder:",
    "文件夹：": "Folder: ", "除了上面这些，你自己的其他文件不会动。": "Apart from the files above, none of your own files are touched.",
    "除了这次的改动，你自己的其他文件不会动。": "Apart from these changes, none of your own files are touched.",
    "已经在你的项目文件夹里了": "Already in your project folder", "分支：": "Branch: ",
    "找不到你的项目文件夹，可能被移走或删掉了。": "Your project folder cannot be found; it may have been moved or deleted.",
    "你的项目文件夹还没有保存过版本，没法放进去。": "Your project folder has no saved versions yet, so nothing can be put in.",
    "你的项目文件夹现在不在平常用的版本上（比如正在看以前的版本）。为了不弄乱你的文件，这次先不放。":
      "Your project folder is not on its usual version right now (for example, you are looking at an older version). To keep your files safe, nothing is put in this time.",
    "你的项目文件夹现在切到了 Crossview 自己用的版本上。为了不弄乱你的文件，这次先不放。":
      "Your project folder is switched to Crossview's own version right now. To keep your files safe, nothing is put in this time.",
    "你的项目文件夹里有一个操作还没做完（比如合并到一半）。为了不弄乱你的文件，这次先不放。":
      "Something in your project folder is not finished (for example, a merge stopped halfway). To keep your files safe, nothing is put in this time.",
    "Crossview 正在做这个项目的任务，做完才能放。": "Crossview is working on a task in this project; you can put the changes in when it is done.",
    "没有新的改动要放进去。": "There are no new changes to put in.",
    "Crossview 上次停下来的时候，有改动还没检查完，所以先不放。接着做完这件事（或者选「不做了」）以后就能放了。":
      "When Crossview last stopped, some changes had not been checked yet, so nothing is put in for now. You can put them in once the task is finished (or dropped with \"Drop task\").",
    "刚才又有改动做好了，要放进去的文件变了。请再看一下，没问题就再点「确定」。":
      "More changes were finished just now, so the files to put in have changed. Please look again and tap \"OK\" if it looks right.",
    "没有放成，你的项目文件夹没有变。": "That did not work; your project folder has not changed.",
    "没有放成：有文件正被别的程序打开着（比如 Excel）。你的项目文件夹没有变，关掉它们再放一次。":
      "That did not work: a file is open in another program (for example Excel). Your project folder has not changed; close it and try again.",
    "没有放成：等了太久还没有做完。你的项目文件夹没有变，请稍后再试。":
      "That did not work: it took too long. Your project folder has not changed; try again later.",
    "现在看不了你的项目文件夹（等了太久没有回应），请稍后再看。": "Your project folder cannot be looked at right now (no answer for too long); check again later.",
    "现在看不了你的项目文件夹里有什么改动，请稍后再看。": "The changes in your project folder cannot be looked at right now; check again later.",
    "已经合并的改动会留着，其他的都不要了。": "Merged changes stay; everything else was thrown away.",
    "什么都没有合并，项目没有变。": "Nothing was merged; the project did not change.",
    "发给它们": "Send to them", "写给它们的话": "Your note to them", "比如：测试也要包括负数": "For example: the tests should cover negative numbers too",
    "它会带着你的意见重新出方案": "It makes a new plan with your note", "比如：出错时的提示写清楚一点": "For example: make the error message clearer",
    "它会照你的意见改，改完再检查一遍": "It changes it as you say, and it is reviewed again", "写下你的回答": "Write your answer",
    "它会带着你的回答接着做": "It carries on with your answer", "可以补充一句意见，也可以不写": "You can add a note, or leave it empty",
    "它会重新出方案": "It makes a new plan",
    "不要这一项了？它做的改动不会合并进项目。": "Drop this item? Its changes will not be merged into the project.",
    "这件事不做了？正在做的会停下来，还没合并的改动都不要了。已经合并的会留着。":
      "Drop this task? Running work stops and unmerged changes are thrown away. Merged changes stay.",
    "暂停？正在做的这一步会停下来，点「继续」后接着做。": "Pause? The running step stops; tap \"Continue\" to carry on.",
    // 停下来的时机 and the side column
    "停下来的时机：": "When to stop: ", "每做完一步": "after every step", "改一下": "Change", "下一步开始时生效": "Takes effect when the next step starts",
    "这次的团队": "The team", "改了哪些文件": "Changed files", "看改动": "See changes", "还没有改动。": "No changes yet.",
    "改动太多，只显示了前面一部分。": "Too many changes; only the first part is shown.", "没有改动。": "No changes.",
    "正在停下来…": "Stopping…", "找不到项目文件夹了": "The project folder is gone",
    "项目文件夹被移走或者删掉了，这件事没法接着做。": "The project folder was moved or deleted, so this task cannot go on.",
    // --- end of job view (C-job) ---
    // --- 讨论 (discuss.js; server messages of orch/discuss*.py and maoweb/discuss.py) ---
    "讨论": "Discuss",
    "GPT 和 Claude 先各自分析，再互相挑错，你来拍板。批准后由 Cowork 从 Crossview 领取方案去开发。":
      "GPT and Claude each analyse it first, then pick holes in each other's plans; you decide. Once you approve, Cowork takes the plan from Crossview and builds it.",
    "马上开始": "Starting", "正在讨论": "Discussing", "停在了中途": "Stopped halfway", "出错停下了": "Stopped on an error",
    "已批准": "Approved", "已停止": "Stopped",
    "独立分析": "Independent analysis", "评审": "Review", "交换意见": "Exchange", "修订方案": "Revision", "继续构思": "More ideas",
    "GPT 先说": "GPT first", "Claude 先说": "Claude first", "GPT 默认": "GPT default", "Claude 默认": "Claude default",
    "等 Cowork 领取": "Waiting for Cowork to take it", "Cowork 在做": "Cowork is on it", "Cowork 报告完成": "Cowork reported it done",
    "后台进程出错了": "The background process failed",
    "网页服务已重启。讨论停在了中途，点「继续」接着讨论；不会自动重复。":
      "The web service restarted. The discussion stopped halfway; tap \"Continue\" to carry on. Nothing is repeated by itself.",
    "后台进程意外停止了。点「继续」接着讨论。": "The background process stopped unexpectedly. Tap \"Continue\" to carry on.",
    "超时": "timed out", "登录失效": "signed out", "命令不可用": "command unavailable", "出错了": "something went wrong", "没完成": "did not finish",
    "模型有问题要问你：先回答，再让它们接着讨论。": "The models have a question for you: answer it first, then let them carry on.",
    "两边的分歧需要你来定：改需求或加约束，说清楚要哪一种。":
      "You need to settle a disagreement: change the requirement or add a constraint that says which way to go.",
    "已经到上限了：批准、改需求或停止。": "The limit is reached: approve, change the requirement, or stop.",
    "有模型试着用了工具：先看看它的原始意见，再决定怎么做。": "A model tried to use a tool: look at its raw answer before you decide.",
    "方案是在你改需求之前写的：再来一轮，让它们按新的需求修订。":
      "The plan was written before you changed the requirement: run one more round so they revise it.",
    "新方案又用回了你否掉的做法：再来一轮，让它们避开。": "The new plan brings back an approach you rejected: run one more round so they avoid it.",
    "有一方的回答读不出结构：再来一轮。": "One answer could not be read: run one more round.",
    "还有重要分歧：再来一轮，让它们接着讨论。": "There are still major disagreements: run one more round.",
    "方案已经稳定，没有重要分歧：可以批准，交给 Cowork。": "The plan is stable with no major disagreements: you can approve it and hand it to Cowork.",
    "请 Claude 再评审一遍，看看有没有漏掉的地方。": "Ask Claude to review it once more for anything missed.",
    "你否掉了方案：再来一轮，让它们换个做法。": "You rejected the plan: run one more round for a different approach.",
    "你回答了问题：再来一轮，让它们按你的回答修订方案。": "You answered the question: run one more round so they revise the plan with your answer.",
    "你改了需求：再来一轮，让它们按新的需求修订方案。": "You changed the requirement: run one more round so they revise the plan.",
    "又用回了你否掉的方案": "Brings back a plan you rejected", "有一方的回答读不出结构": "One answer could not be read",
    "到了轮数或调用次数的上限": "The round or call limit is reached", "有模型试着用了工具（讨论是只读的）": "A model tried to use a tool (discussions are read-only)",
    "当前方案是在需求变更之前写的": "The current plan was written before the requirement changed",
    "有回答超出了字数上限": "An answer went over the word limit", "有说是事实的内容找不到来源": "A claimed fact has no source that could be found",
    "有回答的格式不对，修过一次": "An answer had the wrong format and was repaired once", "提到了你否掉的方案": "Mentions a plan you rejected",
    "两个模型都同意，不代表方案一定对": "Both models agreeing does not make the plan right", "有意见没有给理由": "A point came without a reason",
    "这次的提示很长，用的额度会多一些": "This prompt is long and uses more quota",
    "有回答没看到最新的需求变更": "An answer did not see the latest requirement change", "按设置自动批准了": "Approved automatically, as set",
    "订阅 CLI 没有这个选项": "The subscription CLI has no such option",
    "订阅 CLI 没有这个选项；用每轮字数上限代替": "The subscription CLI has no such option; use the word limit per turn instead",
    "这台电脑上的 claude 版本不支持 --effort，更新 Claude Code 后可用": "The claude on this PC does not support --effort; update Claude Code to use it",
    "没找到这个命令": "Command not found", "订阅调用没有按量价格，只显示 token": "Subscription calls have no per-use price; only tokens are shown",
    "≈$ 是 Claude Code 自己估算的 API 价格，订阅不另收费": "≈$ is Claude Code's own estimate at API prices; the subscription costs nothing extra",
    "这个文件夹离 Crossview 自己的数据太近，模型会读到别的讨论，所以没有开始":
      "This folder is too close to Crossview's own data, so the models could read other discussions; it did not start",
    "这个项目的文件夹不见了": "This project's folder is gone",
    "codex 的设置文件读不出来，没法确认已经关掉它的 MCP 工具，所以没有开始":
      "codex's settings file cannot be read, so its MCP tools cannot be switched off for sure; it did not start",
    "codex 设置里有一个 Crossview 关不掉的 MCP 工具，所以没有开始": "codex's settings have an MCP tool Crossview cannot switch off; it did not start",
    "继续让 GPT 构思": "Let GPT keep developing it", "交给 Claude 评审": "Hand to Claude for review", "再来一轮": "One more round",
    "改需求或加约束": "Change the requirement or add a constraint", "批准，交给 Cowork": "Approve and hand to Cowork",
    "否掉当前方案…": "Reject the current plan…",
    "停止这场讨论？停止后不能再继续，记录会留着。": "Stop this discussion? It cannot be continued afterwards; the record stays.",
    "批准这个方案，交给 Cowork 开发？": "Approve this plan and hand it to Cowork to build?",
    "否掉当前方案？它们会避开这个方案重新想。": "Reject the current plan? They will think again and avoid it.",
    "好的，接着讨论": "OK, carrying on", "已经交给 GPT": "Handed to GPT", "已经交给 Claude 评审": "Handed to Claude for review",
    "好的，再来一轮": "OK, one more round", "已记下": "Noted", "已记下你的回答": "Your answer is noted",
    "已批准，等 Cowork 领取": "Approved; waiting for Cowork to take it", "已否掉这个方案": "The plan was rejected",
    "请写下要改的需求或约束": "Write the change or constraint", "请写下否掉的原因": "Write why you reject it",
    "写下要改的需求或新的约束": "Write the changed requirement or the new constraint",
    "要改需求或加约束，写在这里": "To change the requirement or add a constraint, write it here",
    "讨论已暂停。点「继续」接着讨论。": "The discussion is paused. Tap \"Continue\" to carry on.",
    "讨论停在了中途。点「继续」接着讨论。": "The discussion stopped halfway. Tap \"Continue\" to carry on.",
    "这一步出错了。点「继续」重做这一步；也可以先改需求。": "This step failed. Tap \"Continue\" to run it again, or change the requirement first.",
    "需求变更": "Requirement change", "新约束": "New constraint", "回答": "Answer", "否掉了当前方案": "Rejected the current plan",
    "暂停了讨论": "paused the discussion", "接着讨论": "resumed the discussion", "停止了讨论": "stopped the discussion",
    "重要": "major", "次要": "minor", "没有回应": "no reply", "同意": "agrees", "不同意": "disagrees",
    "展开 GPT 的原始意见": "Open GPT's raw answer", "展开 Claude 的原始意见": "Open Claude's raw answer",
    "事实（Crossview 记录）": "Fact (recorded by Crossview)", "假设": "Assumption", "模型建议": "Model suggestion", "你的决定": "Your decision",
    "需求变更与约束": "Requirement changes and constraints", "已批准决策": "Approved decisions", "被否方案（及原因）": "Rejected plans (and why)",
    "待解决问题": "Open questions", "事实与假设": "Facts and assumptions", "模型评审记录": "Model review record", "Cowork 反馈": "Cowork feedback",
    "同一项目以前的讨论": "Earlier discussions of this project",
    "当前": "current", "已替换": "replaced", "被否": "rejected", "进展": "Progress", "受阻": "Blocked", "问题": "Question",
    "每个阶段后暂停等我": "Pause after each stage and wait for me", "先各自独立分析": "Analyse independently first",
    "最后合并成一个方案": "Merge into one plan at the end", "结束时等我批准": "Wait for my approval at the end",
    "同意的地方": "Agrees with", "不同意的地方": "Disagrees with", "风险": "Risks", "问你的问题": "Questions for you", "说是事实的": "Claimed facts",
    "不关联项目": "No project", "新讨论": "New discussion",
    "还没有讨论。写下想法，让 GPT 和 Claude 先讨论。": "No discussions yet. Write down an idea and let GPT and Claude discuss it first.",
    "GPT · codex（ChatGPT 订阅）": "GPT · codex (ChatGPT subscription)", "Claude · claude（Max 订阅）": "Claude · claude (Max subscription)",
    "最近 7 天：": "Last 7 days: ", "模型": "Model", "CLI 默认": "CLI default", "推理强度": "Reasoning effort", "每轮字数上限": "Word limit per turn",
    "Crossview 在提示里要求，并标出超出的回答": "Crossview asks for it in the prompt and flags answers that go over",
    "温度": "Temperature", "最大输出 token": "Max output tokens", "费用": "Cost", "空 = CLI 默认": "empty = CLI default",
    "要讨论什么": "What to discuss", "关联项目": "Project", "谁先发言": "Who speaks first", "第一个发言的一方也负责修订方案": "The first speaker also revises the plan",
    "写下想法或需求：要做什么、给谁用、有什么限制。GPT 和 Claude 会先各自分析，再互相挑错。":
      "Write the idea or requirement: what to build, for whom, any limits. GPT and Claude analyse it on their own first, then pick holes in each other's plans.",
    "讨论方式": "How to discuss", "自动轮数": "Automatic rounds", "到了就停下来等你": "Then it stops and waits for you",
    "第三方裁判": "Third-party judge", "这一版没有": "Not in this version",
    "模型只读：看得到项目文件，改不了任何东西": "Models are read-only: they can see the project files but cannot change anything",
    "开始讨论": "Start the discussion", "设置：": "Settings: ", "每阶段暂停": "pause after each stage", "阶段之间不暂停": "no pause between stages",
    "请写清楚要讨论什么": "Say what to discuss", "$ —（订阅没有价格）": "$ — (no price on a subscription)",
    "你的需求": "Your requirement", "看完整需求": "Show the whole requirement", "读不出结构": "Unreadable", "格式已修复": "Format repaired",
    "超出字数": "Over the word limit", "没看到最新的需求变更": "Missed the latest change", "调用了工具": "Used a tool",
    "又用回了被否掉的方案": "Brought back a rejected plan", "应对：": "Mitigation: ", "理由：": "Reason: ", "正在读取原始回答…": "Reading the raw answer…",
    "（已截断）": "(cut short)", "模型的原始回答": "The model's raw answer", "修格式那一次的回答": "The answer from the format repair",
    "这一步的回答读不出结构，可以展开原始意见看看。": "This answer could not be read; open the raw answer to look.",
    "步骤": "Steps", "验收标准": "Acceptance criteria", "原始意见": "Raw answer", "还没有方案": "No plan yet",
    "GPT 继续构思后的小结": "Summary after GPT's further ideas", "Claude 评审后的小结": "Summary after Claude's review",
    "双方": "both", "只有一方提到": "only one side", "持续分歧": "persistent disagreement", "当前方案": "Current plan", "共识": "Consensus",
    "未解决分歧": "Open disagreements", "主要风险": "Main risks", "需要你决定的问题": "Questions for you to decide", "建议下一步": "Suggested next step",
    "按设置自动批准": "Auto-approved, as set", "马上开始…": "Starting…", "正在暂停，等这一步停下来…": "Pausing; waiting for this step to stop…",
    "已批准。Cowork 领取以后的进展在「决策记录」里。": "Approved. Once Cowork takes it, its progress shows under \"Decision record\".",
    "看决策记录": "See the decision record", "这场讨论已停止，记录会留着。": "This discussion is stopped; the record stays.",
    "这是": "This is a", "其他选择": "Other choices", "回答这个问题": "Answer this question", "（读不出结构）": "(unreadable)",
    "备注：": "Note: ", "原因：": "Reason: ", "被否：": "Rejected: ", "自称事实": "claimed as fact", "来源存在": "source exists",
    "来源找不到": "source not found", "已回答": "answered", "方案版本": "Plan versions", "Crossview 只核对了提交是否存在": "Crossview only checked that the commit exists",
    "看原始意见": "Open the raw answer", "提交存在": "the commit exists", "项目仓库里没有这个提交": "no such commit in the project repository", "没法核对这个提交": "the commit could not be checked",
    "在项目仓库里存在": "exists in the project repository", "项目仓库里没有，来源找不到": "not in the project repository; source not found",
    "没法核对": "could not be checked",
    "原始需求（原文）": "Original requirement (verbatim)", "（无）": "(none)", "对话": "Conversation", "决策记录": "Decision record",
    // server messages
    "只有管理员可以使用讨论": "Only the admin can use discussions",
    "已经有一场讨论在进行，等它停下再开新的": "A discussion is already running; start a new one when it stops",
    "这台电脑上的 claude 不支持推理强度设置": "The claude on this PC does not support the reasoning effort setting",
    "这台电脑上找不到 codex 命令": "The codex command is not on this PC", "这台电脑上找不到 claude 命令": "The claude command is not on this PC",
    "没有这场讨论": "No such discussion",
    "讨论记录正忙，请稍后再试": "The discussion record is busy; try again shortly",
    "这场讨论已经到上限了：请批准、修改需求或停止": "This discussion has reached its limit: approve, change the requirement, or stop",
    "Crossview 自己的数据文件夹在这个项目里面，模型会读到别的讨论和家人的聊天，不能关联这个项目":
      "Crossview's own data folder is inside this project, so the models would read other discussions and the family's chats; this project cannot be used",
    "没有这一步的原始记录": "There is no raw record of this step",
    "需求写得太长了，最多 8000 字": "The requirement is too long; at most 8000 characters",
    "第一个发言的只能是 GPT 或 Claude": "The first speaker must be GPT or Claude", "模型名称不对": "That model name is not valid",
    "推理强度只能是 low、medium 或 high": "Reasoning effort must be low, medium or high",
    "每轮字数上限必须是 100 到 2000": "The word limit per turn must be 100 to 2000", "自动轮数必须是 1 到 4": "Automatic rounds must be 1 to 4",
    "第三方裁判这一版还没有": "There is no third-party judge in this version", "这个问题已经不在了": "That question is no longer open",
    "需求变更已经太多了（最多 20 条、共 12000 字）：请批准、停止或开一场新讨论":
      "Too many requirement changes (at most 20, 12000 characters in all): approve, stop, or start a new discussion",
    "已经否掉 10 个方案了：请批准、停止或开一场新讨论": "10 plans have been rejected already: approve, stop, or start a new discussion",
    "还没有可以批准的方案": "There is no plan to approve yet",
    // --- end of 讨论 ---
    // --- the old task dispatch, switched off (Settings → 旧功能, orch/legacy.py) ---
    "旧功能": "Legacy", "旧的派活功能": "Old task dispatch", "开": "On", "关": "Off",
    "Crossview 早期自己拆任务、派给 AI、合并代码：项目页、聊天里的「交给项目」、Agent 页的控制面板、命令行的 task / run / plan 等。现在开发交给 Cowork，这些默认关闭；数据都还在，随时可以打开。":
      "In its early days Crossview split work into tasks itself, handed them to AI agents and merged the code: the Projects page, " +
      "\"Hand to a project\" in chat, the control panel on the Agent page, and the task / run / plan commands. Development now " +
      "goes to Cowork, so all of this is off by default; its data is all still there, and you can turn it on at any time.",
    "使用旧的派活功能": "Use the old task dispatch",
    "现在由环境变量 MAO_LEGACY_DISPATCH 决定，这里改不了": "The environment variable MAO_LEGACY_DISPATCH decides this now; it cannot be changed here",
    "已打开旧的派活功能": "The old task dispatch is on", "已关闭旧的派活功能": "The old task dispatch is off",
    "旧的派活功能已关闭。管理员可以在 设置 → 旧功能 里打开。": "The old task dispatch is off. The admin can turn it on under Settings → Legacy.",
    "这个文件夹还没有为旧的派活功能准备好（缺少 .agents/orch.toml）。请在 项目 页移除它，再用「添加项目」加回来。":
      "This folder is not prepared for the old task dispatch (.agents/orch.toml is missing). Remove it on the Projects page, then add it back with \"Add a project\".",
    "这台电脑上的 Claude Code 和 Codex 会话：什么时候用过、用了多少。旧的派活功能打开时，还能看到每个 agent 在做什么，并暂停或换人。":
      "Claude Code and Codex sessions on this PC: when they were used and how much. While the old task dispatch is on, you also see what each agent is doing, and can pause it or give its task to another.",
    "登记文件夹": "Register a folder", "登记": "Register", "项目文件夹的完整路径": "Full path of the project folder",
    "已登记这个文件夹": "Folder registered", "找不到这个文件夹，请检查路径。": "Folder not found. Check the path.",
    // --- end of the old task dispatch ---
    // --- phone alerts: errors the server sends (orch/notify.py, orch/notify_hook.py) ---
    "服务器地址要以 https:// 开头，不能带用户名、密码、? 或 #（本机测试可以用 http://127.0.0.1）":
      "The server address must start with https:// and must not contain a user name, password, ? or # (http://127.0.0.1 is fine for a local test)",
    "主题只能用字母、数字、- 和 _，12 到 64 个字符":
      "The topic may only use letters, digits, - and _, 12 to 64 characters",
    "提醒的语言只能是中文或 English":
      "The language of the alerts can only be Chinese or English",
    "提醒的时间设置不在允许的范围里":
      "A time setting of the alerts is out of the allowed range",
    "还没有主题，先生成一个":
      "There is no topic yet; create one first",
    "访问令牌里不能有空格、换行或中文等字符":
      "The access token must not contain spaces, line breaks or non-ASCII characters",
    "没有找到 Claude Code 的设置文件夹。先安装并打开一次 Claude Code。":
      "Claude Code's settings folder was not found. Install Claude Code and open it once first.",
    "Claude Code 的 settings.json 不是有效的 JSON，没有改动它。请先修好这个文件。":
      "Claude Code's settings.json is not valid JSON, so it was left as it is. Fix that file first.",
    "Claude Code 的 settings.json 里 hooks 的格式和预期不同，没有改动它。":
      "The hooks in Claude Code's settings.json are not in the expected shape, so it was left as it is.",
    "找不到要写进钩子的 Python 程序（python.exe 或 pythonw.exe 的完整路径）。":
      "The Python program for the hook was not found (give the full path of python.exe or pythonw.exe).",
    // --- end of phone alerts (errors) ---
    // --- phone alerts: Settings → 手机提醒 (app.js renderNotify) ---
    "手机提醒": "Phone alerts", "开启手机提醒": "Turn on phone alerts",
    "已开启手机提醒": "Phone alerts are on", "已关闭手机提醒": "Phone alerts are off",
    "有事等你的时候（讨论要你决定、Cowork 卡住了、Claude Code 等你批准……），Crossview 往你的手机发一条短提醒。用的是 ntfy App，免费，不用注册。":
      "When something is waiting for you (a discussion needs your decision, Cowork is stuck, Claude Code wants your approval…), " +
      "Crossview sends a short alert to your phone. It uses the ntfy app: free, with no sign-up.",
    "服务器": "Server", "自建服务器才需要改": "Change this only for your own ntfy server",
    "主题": "Topic", "换一个": "New topic", "订阅地址：": "Subscribe link: ",
    "换一个主题？换了以后，手机上要重新订阅新的主题，旧的主题不会再收到提醒。":
      "Make a new topic? Your phone then has to subscribe to the new topic; the old one gets no more alerts.",
    "已换成新的主题，请在手机上重新订阅": "New topic made; subscribe to it again on your phone",
    "访问令牌": "Access token", "已设置（填新的可以替换）": "Set (paste a new one to replace it)", "没有设置": "Not set",
    "只有自建并设了权限的服务器需要": "Only needed for your own server with access control",
    "已保存访问令牌": "Access token saved", "删除访问令牌？": "Delete the access token?", "已删除访问令牌": "Access token deleted",
    "提醒里只写发生了什么和编号，不会有需求、回答、文件内容或密钥。ntfy.sh 是别人的服务器：知道主题名的人都能收到，别把主题告诉别人。":
      "An alert only says what happened and a short id: never a requirement, an answer, file contents or a key. " +
      "ntfy.sh is someone else's server: anyone who knows the topic name gets the alerts, so keep the topic to yourself.",
    "在手机上安装 ntfy（App Store 或 Google Play 搜 ntfy）": "Install ntfy on your phone (search for ntfy in the App Store or Google Play)",
    "打开 ntfy，点 +，主题填上面这一串；用自己的服务器时打开「使用其他服务器」，填服务器地址":
      "Open ntfy, tap +, and enter the topic above; with your own server, turn on \"Use another server\" and enter its address",
    "回到这里点「发一条测试提醒」": "Come back here and tap \"Send a test alert\"",
    "发一条测试提醒": "Send a test alert", "正在发送…": "Sending…", "测试提醒已发出，看看手机": "Test alert sent; check your phone",
    "提醒哪些事": "What to alert about",
    "讨论等你决定、出错或卡住": "A discussion waits for your decision, fails or gets stuck",
    "多久没有动静算卡住：": "Counts as stuck after no progress for", "分钟": "minutes", "小时": "hours",
    "Cowork 遇到阻碍、有问题、做完了": "Cowork is blocked, has a question, or is done",
    "Cowork 领走方案后很久没回报（弱信号）": "No report from Cowork long after it took the plan (a weak signal)",
    "多久没有回报算很久：": "Counts as long after no report for",
    "ChatGPT 里有任务等你按发送": "A task in ChatGPT waits for you to press Send", "等了多久才提醒：": "Alert after it has waited for",
    "Claude Code 等你批准或回答（权限、表单）": "Claude Code waits for your approval or answer (permissions, forms)",
    "Claude Code 做完一轮、停下来等你（每次回答后都会有，容易多）":
      "Claude Code finished a turn and waits for you (after every answer, so it can be a lot)",
    "你在这台电脑前时不发 Claude Code 的提醒：最近": "No Claude Code alerts while you are at this PC: none if the keyboard or mouse was used in the last",
    "分钟内动过键盘或鼠标就不发（0 = 总是发）": "minutes (0 = always send)",
    "Claude Code 钩子：": "Claude Code hook: ", "已安装": "Installed", "还没有安装": "Not installed", "安装": "Install",
    "需要重新安装（Crossview 或 Python 换了位置）": "Needs reinstalling (Crossview or Python has moved)",
    "这台电脑上没有找到 Claude Code": "Claude Code was not found on this PC",
    "Claude Code 的 settings.json 读不懂，先修好它": "Claude Code's settings.json cannot be read; fix it first",
    "上面两项 Claude Code 提醒要靠这个钩子。会在这台电脑的 Claude Code 设置里加一个钩子，改之前先备份 settings.json":
      "The two Claude Code alerts above need this hook. It is added to Claude Code's settings on this PC; settings.json is backed up first",
    "原来的 settings.json 已备份为": "The original settings.json was backed up as",
    "从 Claude Code 的设置里移除 Crossview 的钩子？": "Remove Crossview's hook from Claude Code's settings?",
    "已安装 Claude Code 钩子": "Claude Code hook installed", "钩子已经装好，没有改动": "The hook is already installed; nothing changed",
    "已移除 Claude Code 钩子": "Claude Code hook removed", "没有找到 Crossview 的钩子，没有改动": "Crossview's hook was not there; nothing changed",
    "提醒用的语言": "Language of the alerts",
    "最近的提醒": "Recent alerts", "刷新": "Refresh", "还没有发过提醒。": "No alerts sent yet.",
    "后台检查：正在运行": "Background check: running",
    "后台检查：没有运行（网页版启动时会自动开始）": "Background check: not running (it starts with the web app)",
    "已发出": "Sent", "没有发出：": "Not sent: ", "太多了，稍后再发": "Too many at once; sent later",
    "没有发出（文件被占用）": "Not sent (a file was busy)",
    "讨论等你决定": "Discussion waiting for you", "讨论出错或中断": "Discussion failed or interrupted",
    "讨论可能卡住": "Discussion may be stuck", "Cowork 遇到阻碍": "Cowork is blocked", "Cowork 有问题": "Cowork has a question",
    "Cowork 做完了": "Cowork is done", "Cowork 很久没回报": "No report from Cowork for a long time",
    "ChatGPT 等你按发送": "ChatGPT waits for Send", "Claude Code 等你批准或回答": "Claude Code waits for your approval or answer",
    "Claude Code 停下来等你": "Claude Code stopped and waits for you", "测试提醒": "Test alert",
    // --- end of phone alerts (the tab) ---
  };

  // texts with values: [pattern, (match) => English]
  const tr = (s) => EN[s] || s;
  const DOING = { 独立分析: "is analysing on its own", 评审: "is reviewing", 交换意见: "is exchanging views", 修订方案: "is revising the plan",
                  继续构思: "is developing more ideas", 讨论: "is discussing" };   // "{GPT} 正在{独立分析}…" (discuss.js)
  const names = (s) => s.replace(/ 等 (\d+) 个$/, " ($1 in all)").split("、").join(", ");   // "a、b 等 7 个" from the server
  const PAT = [
    [/^出错了（(\d+)）$/, (m) => `Something went wrong (${m[1]})`],
    [/^删除「(.*)」？删除后找不回来。$/, (m) => `Delete "${m[1]}"? It cannot be undone.`],
    [/^给 (.+) 设一个新密码（至少 8 位）$/, (m) => `New password for ${m[1]} (at least 8 characters)`],
    [/^(.+) 现在可以写程序了$/, (m) => `${m[1]} can now give coding tasks`],
    [/^删除 (.+) 的账号和全部对话？$/, (m) => `Delete ${m[1]}'s account and all conversations?`],
    [/^(\d+) 分钟前$/, (m) => `${m[1]} min ago`], [/^(\d+) 小时前$/, (m) => `${m[1]} h ago`],
    [/^(\d+) 月 (\d+) 日$/, (m) => `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m[1] - 1]} ${m[2]}`],
    [/^这台电脑已经装好 Tailscale（地址 (.+)），但 Crossview 这次没有在这个地址上打开。 请关掉 Crossview 的黑色窗口，双击$/,
      (m) => `Tailscale is installed on this PC (address ${m[1]}), but Crossview did not open on that address this time. Close Crossview's black window, double-click`],
    [/^材料：(.+)$/, (m) => `Material: ${m[1]}`],
    [/^共 (\d+) 行，完整结果在 Excel 里$/, (m) => `${m[1]} rows in all; the full result is in the Excel file`],
    [/^这些没有填：(.+)，下载后在 Word 里补上。$/, (m) => `Left empty: ${m[1]}. Add them in Word after downloading.`],
    [/^看内容（约 (\d+) 字）$/, (m) => `Show the text (about ${m[1]} words)`],
    [/^(\d+) 个正在用$/, (m) => `${m[1]} in use`],
    [/^今天 (.+) token$/, (m) => `${m[1]} tokens today`],
    [/^(\d+)%（最近 (\d+) 次）$/, (m) => `${m[1]}% (last ${m[2]})`],
    [/^(已暂停|暂时停用)，到 (.+)$/, (m) => `${tr(m[1])} until ${m[2]}`],
    [/^([^·]+) 已用 (\d+)%$/, (m) => `${tr(m[1])} ${m[2]}% used`],
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
    // --- server texts ---
    [/^商量方案没有成功：([\s\S]+)$/, (m) => `Planning did not work: ${m[1]}`],
    [/^方案不完整：([\s\S]+)$/, (m) => `The plan is incomplete: ${m[1]}`],
    [/^项目里的 \.agents\/orch\.toml 写得不对：([\s\S]+)$/, (m) => `The project's .agents/orch.toml is not valid: ${m[1]}`],
    // --- end of server texts ---
    // --- projects page (C-projects) ---
    [/^(\d+) 件等你决定$/, (m) => `${m[1]} waiting for you`], [/^(\d+) 件正在做$/, (m) => `${m[1]} in progress`], [/^(\d+) 个有人在用$/, (m) => `${m[1]} in use by someone else`],
    [/^(\d+) 个空闲$/, (m) => `${m[1]} idle`], [/^等 (.+) 决定$/, (m) => `Waiting for ${m[1]}`],
    [/^第 (\d+) \/ (\d+) 项$/, (m) => `Item ${m[1]} of ${m[2]}`], [/^(\d+) 项$/, (m) => `${m[1]} item${m[1] === "1" ? "" : "s"}`],
    [/^(\d+) 分钟$/, (m) => `${m[1]} min`], [/^(\d+) 小时 (\d+) 分钟$/, (m) => `${m[1]} h ${m[2]} min`],
    [/^已经 (.+)$/, (m) => { const r = one(m[1]); return r === null ? null : `${r} so far`; }],
    [/^(.+) 都空着$/, (m) => `${m[1].split("、").join(", ")} are free`],
    [/^今天花了 (.+)$/, (m) => `${m[1]} spent today`], [/^5 小时额度用了 (\d+)%$/, (m) => `5-hour limit: ${m[1]}%`],
    [/^今天用了 (.+) token$/, (m) => `${m[1]} tokens today`], [/^交代的人：(.+)$/, (m) => `Started by ${m[1]}`],
    [/^现在 (.+) 在用这个项目，做完后才能开始新任务$/, (m) => `${m[1]} is using this project now; a new task can start when that is done`],
    [/^把「(.+)」从项目列表里移除？文件夹里的文件不会动。$/, (m) => `Remove "${m[1]}" from the project list? The files in the folder are not touched.`],
    [/^没保存的改动（(\d+) 处）$/, (m) => `Unsaved changes (${m[1]})`], [/^第 (\d+) 步：$/, (m) => `Step ${m[1]}: `],
    [/^已交给项目「(.+)」$/, (m) => `Handed to project "${m[1]}"`],
    [/^(.+)（(还有一件没做完|别人正在用|现在用不了)）$/, (m) => `${m[1]} (${EN[m[2]]})`],
    [/^（共 (\d+) 个）$/, (m) => `(${m[1]} in all)`],
    [/^文件夹的完整路径，例如 (.+)$/, (m) => `Full path of the folder, for example ${m[1]}`],
    [/^(.+) 现在可以用项目了$/, (m) => `${m[1]} can now use projects`],
    [/^这个文件夹在另一个项目（git 仓库）里面：(.+)。请直接添加那个文件夹。$/, (m) => `This folder is inside another project (a git repository): ${m[1]}. Add that folder instead.`],
    [/^git 打不开这个文件夹：(.+)$/, (m) => `Git cannot open this folder: ${m[1]}`],
    [/^这个文件夹已经添加过了，名字是「(.+)」。$/, (m) => `This folder has already been added as "${m[1]}".`],
    [/^这个文件夹里面已经有项目「(.+)」了，请直接用那个项目。$/, (m) => `There is already a project "${m[1]}" inside this folder; use that one.`],
    [/^这个文件夹里的文件太多了（超过 (\d+) 个），不适合交给 AI。请选一个小一点的文件夹。$/, (m) => `This folder has too many files (over ${m[1]}) for the AI. Pick a smaller folder.`],
    [/^检查超时：git (\d+) 秒都没有回应，请稍后再试。$/, (m) => `The check timed out: git did not answer for ${m[1]} seconds. Try again later.`],
    [/^(.+) 还没有准备好：请在项目页用「添加项目」添加它。$/, (m) => `${m[1]} is not ready yet: add it with "Add a project" on the Projects page.`],
    [/^已经有一个叫「(.+)」的项目了，换个名字吧$/, (m) => `There is already a project called "${m[1]}"; choose another name`],
    [/^git 打不开这个项目：(.+)$/, (m) => `Git cannot open this project: ${m[1]}`],
    [/^项目里的 \.agents\/orch\.toml 写得不对：(.+)$/, (m) => `The project's .agents/orch.toml has a mistake: ${m[1]}`],
    [/^准备项目时出错了：(.+)$/, (m) => `Preparing the project failed: ${m[1]}`],
    [/^这个项目还有一件没做完：「(.+)」。先把它做完或点「不做了」。$/, (m) => `This project still has an unfinished task: "${m[1]}". Finish or drop it first.`],
    [/^(.+) 正在用这个项目，等它做完再交代新任务$/, (m) => `${m[1]} is using this project; give it a new task when that is done`],
    [/^Crossview 内部出错了，请稍后再试（(.+)）$/, (m) => `Something went wrong inside Crossview; try again later (${m[1]})`],
    // --- end of projects page (C-projects) ---
    // --- job view (C-job) ---
    [/^(\d+) 月 (\d+) 日 (\d+:\d\d)$/, (m) => `${one(`${m[1]} 月 ${m[2]} 日`)} ${m[3]}`],
    [/^((?:\d+ 月 \d+ 日 )?\d+:\d\d) (开始|同意|合并)$/, (m) => `${{ 开始: "Started", 同意: "Approved", 合并: "Merged" }[m[2]]} ${one(m[1]) ?? m[1]}`],
    [/^(你 )?((?:\d+ 月 \d+ 日 )?\d+:\d\d) 同意了$/, (m) => `${m[1] ? "You approved" : "approved"} at ${one(m[2]) ?? m[2]}`],
    [/^第 (\d+) 项$/, (m) => `Item ${m[1]}`], [/^现在 · 第 (\d+) 项$/, (m) => `Now · item ${m[1]}`],
    [/^(这一项)?已经用了 (\S+) token$/, (m) => `${m[2]} tokens${m[1] ? " on this item" : ""} so far`],
    [/^这次一共 (\S+) token$/, (m) => `${m[1]} tokens in all`], [/^用了 (\S+) token$/, (m) => `${m[1]} tokens`], [/^按量花了 (\S+)$/, (m) => `${m[1]} pay per use`],
    [/^已记下：(.+)$/, (m) => `Noted: ${tr(m[1])}`], [/^等第 (\d+) 项合并$/, (m) => `Waits for item ${m[1]} to merge`],
    [/^第 (\d+) 项合并后再做$/, (m) => `after item ${m[1]} is merged`], [/^退出码 (-?\d+)$/, (m) => `exit code ${m[1]}`],
    [/^看前面的 (\d+) 步$/, (m) => `Show ${m[1]} earlier step${m[1] === "1" ? "" : "s"}`],
    [/^(\d+) 个通过$/, (m) => `${m[1]} passed`], [/^(\d+) 个没通过$/, (m) => `${m[1]} failed`],
    [/^(\d+) 个全部通过$/, (m) => `all ${m[1]} passed`], [/^(\d+) 个通过，(\d+) 个没通过$/, (m) => `${m[1]} passed, ${m[2]} failed`],
    [/^验收 (\d+) 个通过$/, (m) => `checks: ${m[1]} passed`], [/^改了 (\d+) 个文件$/, (m) => `${m[1]} file${m[1] === "1" ? "" : "s"} changed`],
    [/^商量了 (\d+) 轮$/, (m) => `${m[1]} rounds of planning`], [/^检查了 (\d+) 轮$/, (m) => `${m[1]} review round${m[1] === "1" ? "" : "s"}`],
    [/^商量好了：分 (\d+) 项做$/, (m) => `The plan: ${m[1]} item${m[1] === "1" ? "" : "s"}`],
    [/^第 (\d+) 项做好了，(检查也通过了|验收通过了)$/, (m) => `Item ${m[1]} is done and ${m[2] === "验收通过了" ? "passed the checks" : "passed review"}`],
    [/^还有 (\d+) 个小问题，不影响合并$/, (m) => `${m[1]} minor issue${m[1] === "1" ? "" : "s"} left, not blocking the merge`],
    [/^合并后接着做第 (\d+) 项：$/, (m) => `After the merge, item ${m[1]}: `], [/^第 (\d+) 项的改动$/, (m) => `Changes in item ${m[1]}`],
    [/^合并了 (\d+) 项$/, (m) => `${m[1]} item${m[1] === "1" ? "" : "s"} merged`], [/^(\d+) 项放弃了$/, (m) => `${m[1]} dropped`],
    // 放进我的项目文件夹 (the file names in the server's sentences stay as they are)
    [/^有 (\d+) 个文件的改动还没放进你的项目文件夹$/, (m) => `${m[1]} changed file${m[1] === "1" ? " is" : "s are"} not in your project folder yet`],
    [/^已经放进你的项目文件夹了（(\d+) 个文件）$/, (m) => `In your project folder now (${m[1]} file${m[1] === "1" ? "" : "s"})`],
    [/^(.+) 已经放进项目文件夹了（(\d+) 个文件）$/, (m) => `${m[1]} put ${m[2] === "1" ? "it" : "them"} in the project folder (${m[2]} file${m[2] === "1" ? "" : "s"})`],
    [/^还有 (\d+) 个$/, (m) => `and ${m[1]} more`],
    [/^还有 (\d+) 个新文件$/, (m) => `and ${m[1]} more new file${m[1] === "1" ? "" : "s"}`],
    [/^后来又有 (\d+) 个文件的改动，到项目页放进去$/,
      (m) => `${m[1]} more changed file${m[1] === "1" ? "" : "s"} came later: put ${m[1] === "1" ? "it" : "them"} in from the project page`],
    [/^Crossview 想删掉的这些文件还在：(.+)。可能有别的程序正在用它们，可以关掉后自己删掉。$/,
      (m) => `These files Crossview wanted to delete are still there: ${names(m[1])}. Another program may be using them; close it and delete them yourself.`],
    [/^你的项目文件夹里有还没保存的改动：(.+)。为了不弄乱你的文件，这次先不放。$/,
      (m) => `Your project folder has unsaved changes: ${names(m[1])}. To keep your files safe, nothing is put in this time.`],
    [/^你的项目文件夹里已经有同名的文件：(.+)。放进去会把它们盖掉，所以这次先不放。$/,
      (m) => `Your project folder already has files with the same names: ${names(m[1])}. Putting the changes in would overwrite them, so nothing is put in this time.`],
    [/^这些文件在你的项目文件夹里也改过，和这次的改动合不到一起：(.+)。为了不弄乱你的文件，这次先不放。$/,
      (m) => `These files were also changed in your project folder and do not fit with these changes: ${names(m[1])}. To keep your files safe, nothing is put in this time.`],
    [/^这些文件正被别的程序打开着（比如 Excel）：(.+)。先关掉它们，再放一次。$/,
      (m) => `These files are open in another program (for example Excel): ${names(m[1])}. Close them, then try again.`],
    [/^没有放成，而且这些文件没能放回原样：(.+)。先关掉正在用它们的程序，再请管理员看一下。$/,
      (m) => `That did not work, and these files could not be put back as they were: ${names(m[1])}. Close the programs using them, then ask your admin to take a look.`],
    // --- end of job view (C-job) ---
    // --- 讨论 (discuss.js) ---
    [/^第 (\d+) 轮$/, (m) => `Round ${m[1]}`], [/^第 (\d+) 轮小结$/, (m) => `Round ${m[1]} summary`],
    [/^调用 (\d+) \/ (\d+) 次$/, (m) => `${m[1]} of ${m[2]} calls`],
    [/^(\d+) 次调用$/, (m) => `${m[1]} call${m[1] === "1" ? "" : "s"}`],
    [/^输入 (\S+) \/ 输出 (\S+) token$/, (m) => `${m[1]} in / ${m[2]} out tokens`],
    [/^≈(\$\S+)（估算）$/, (m) => `≈${m[1]} (estimate)`],
    [/^(5 小时|7 天)窗口已用 (\d+)%$/, (m) => `${m[1] === "5 小时" ? "5-hour" : "7-day"} window: ${m[2]}% used`],
    [/^已找到 (.+)$/, (m) => `Found ${m[1]}`], [/^没找到 (\S+) 命令$/, (m) => `${m[1]} command not found`],
    [/^自动 (\d+) 轮$/, (m) => `${m[1]} automatic round${m[1] === "1" ? "" : "s"}`],
    [/^(\d+) 秒$/, (m) => `${m[1]} s`],
    [/^(GPT|Claude) 正在(独立分析|评审|交换意见|修订方案|继续构思|讨论)…$/, (m) => `${m[1]} ${DOING[m[2]]}…`],
    [/^(GPT|Claude) 在 (\S+) 失败了：(.+)$/, (m) => `${m[1]} failed at ${m[2]}: ${tr(m[3]).replace(/^[A-Z](?=[a-z])/, (c) => c.toLowerCase())}`],
    [/^额度会在 (.+) 恢复$/, (m) => `The limit resets at ${one(m[1]) ?? m[1]}`],
    [/^(GPT|Claude) 问你：$/, (m) => `${m[1]} asks you:`],
    [/^方案 v(\d+)$/, (m) => `Plan v${m[1]}`],
    [/^批准方案 v(\d+)：$/, (m) => `Approved plan v${m[1]}: `], [/^否掉方案 v(\d+)：$/, (m) => `Rejected plan v${m[1]}: `],
    [/^已被 (.+) 领取（(.+)）$/, (m) => `Taken by ${m[1]} (${one(m[2]) ?? m[2]})`], [/^已被 (.+) 领取$/, (m) => `Taken by ${m[1]}`],
    [/^提交 (\S+) 在项目仓库里存在（Crossview 只核对了这一点）$/, (m) => `Commit ${m[1]} exists in the project repository (Crossview checked only this)`],
    [/^在 Cowork 里说：用 Crossview 的 crossview_get_brief 领取 (\S+) 开始开发$/,
      (m) => `In Cowork, say: "Take ${m[1]} with Crossview's crossview_get_brief and start building"`],
    // --- end of 讨论 ---
    // --- the old task dispatch, switched off ---
    [/^还有 (\d+) 个项目任务在运行。关闭后它们会自己跑完，但在网页上看不到，直到重新打开。确定关闭？$/,
      (m) => `${m[1]} project task${m[1] === "1" ? " is" : "s are"} still running. They will finish on their own, but you will not see them on the web until you turn this on again. Turn it off?`],
    [/^项目文件夹的完整路径，例如 (.+)$/, (m) => `Full path of the project folder, for example ${m[1]}`],
    // --- end of the old task dispatch ---
    // --- phone alerts ---
    [/^后台检查：正在运行，上次 (.+)$/, (m) => `Background check: running, last ${one(m[1]) ?? m[1]}`],
    [/^测试提醒没有发出：(.+)$/, (m) => `The test alert was not sent: ${one(m[1]) ?? m[1]}`],
    // --- end of phone alerts ---
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

  const OWN = ".msg-meta";   // Crossview's own buttons under a message (复制) are translated all the same
  function skip(node) { const el = node.nodeType === 1 ? node : node.parentElement; return !el || (!!el.closest(SKIP) && !el.closest(OWN)); }
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
    for (const c of root.childNodes) walk(c, attrsOnly && !(c.nodeType === 1 && c.matches(OWN)));
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
