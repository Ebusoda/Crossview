"""Writing templates for the 文档 page: pick a kind of document, fill in a few boxes, and the model writes it.

Each template has a name, the layout it is usually written in, and the boxes a person fills in. compose() turns the
filled boxes into the request that orch/doc.py sends (after masking). Empty boxes are left out; the model is told to
leave a placeholder such as [日期] for anything it is not given, so nothing is made up.
"""
from __future__ import annotations

TEMPLATES: list[dict] = [
    {"id": "leave", "name": "请假条", "style": "plain", "hint": "向单位或学校请假",
     "fields": [["who", "请假人", "例如：王五"], ["unit", "交给谁", "例如：办公室 / 三年级二班 李老师"],
                ["type", "请假类型", "事假 / 病假 / 年假"], ["dates", "起止时间", "例如：10 月 15 日至 17 日，共 3 天"],
                ["reason", "事由", "例如：陪家人看病"]]},
    {"id": "notice", "name": "通知", "style": "gongwen", "hint": "开会、放假、值班、活动安排",
     "fields": [["issuer", "发文单位", "例如：办公室"], ["matter", "通知什么事", "例如：国庆节放假和值班安排"],
                ["when", "时间", "例如：10 月 1 日至 7 日"], ["where", "地点", "可以不填"],
                ["who", "对象", "例如：各部门、全体员工"], ["require", "要求", "例如：每天两人值班，保持电话畅通"]]},
    {"id": "summary", "name": "工作总结", "style": "gongwen", "hint": "月度、季度、年度总结",
     "fields": [["period", "时间段", "例如：2026 年第三季度"], ["work", "主要做了什么", "一条一行"],
                ["results", "成绩和数字", "例如：完成项目 3 个，节约经费 12 万元"], ["problems", "存在的问题", "可以不填"],
                ["next", "下一步打算", "可以不填"]]},
    {"id": "minutes", "name": "会议纪要", "style": "gongwen", "hint": "把开会的内容整理成纪要",
     "fields": [["title", "会议名称", "例如：十月工作例会"], ["when", "时间地点", "例如：10 月 13 日上午，3 楼会议室"],
                ["people", "参会人员", "例如：张三、李四等 8 人"], ["topics", "讨论了什么、定了什么", "一条一行"],
                ["todo", "要办的事（谁、什么时候）", "可以不填"]]},
    {"id": "request", "name": "请示", "style": "gongwen", "hint": "向上级请求批准某件事",
     "fields": [["issuer", "以谁的名义", "例如：后勤科"], ["to", "报给谁", "例如：局领导"],
                ["matter", "请示什么", "例如：购置办公电脑 5 台"], ["why", "理由", "例如：现有电脑使用超过 8 年，经常故障"],
                ["detail", "具体内容（数量、金额等）", "例如：预算 3 万元"]]},
    {"id": "explain", "name": "情况说明", "style": "gongwen", "hint": "说明一件事的经过和原因",
     "fields": [["issuer", "以谁的名义", "例如：财务室"], ["event", "什么事", "例如：9 月报表延迟提交"],
                ["process", "经过", "按时间写"], ["reason", "原因", ""], ["handle", "已经怎么处理、以后怎么做", "可以不填"]]},
    {"id": "thanks", "name": "感谢信", "style": "plain", "hint": "感谢老师、医生、邻居、同事",
     "fields": [["to", "写给谁", "例如：市人民医院心内科全体医护人员"], ["why", "为什么感谢", "具体的事，越具体越好"],
                ["from", "落款", "例如：患者家属 王五"]]},
    {"id": "greeting", "name": "祝福语", "style": "plain", "hint": "生日、节日、婚礼、升学",
     "fields": [["to", "送给谁", "例如：妈妈 / 老同学"], ["occasion", "什么场合", "例如：六十岁生日"],
                ["tone", "语气和长短", "例如：温暖一点，三四句话"]]},
]
BY_ID = {t["id"]: t for t in TEMPLATES}


def public() -> list[dict]:
    return [{"id": t["id"], "name": t["name"], "style": t["style"], "hint": t["hint"],
             "fields": [{"key": k, "label": label, "placeholder": ph} for k, label, ph in t["fields"]]}
            for t in TEMPLATES]


def compose(template_id: str, fields: dict, extra: str = "") -> tuple[str, str]:
    """(request text, layout) for a template and the boxes the person filled in."""
    t = BY_ID.get(str(template_id))
    if t is None:
        raise ValueError("没有这个模板")
    lines = [f"写一份「{t['name']}」。"]
    filled = 0
    for key, label, _ in t["fields"]:
        value = str((fields or {}).get(key, "")).strip()[:2000]
        if value:
            lines.append(f"{label}：{value}")
            filled += 1
    if not filled:
        raise ValueError("请至少填一项")
    if extra.strip():
        lines.append(f"其他要求：{extra.strip()[:2000]}")
    lines.append("没有给出的内容（例如日期、落款）用 [日期] 这样的空位留着，不要编造。")
    return "\n".join(lines), t["style"]
