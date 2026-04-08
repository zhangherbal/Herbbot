import random
def simulate_case_opening(case_name: str = "武器箱"):
    """模拟CSGO武器箱开箱过程"""
    skins = {
        "金": ["★ 蝴蝶刀 | 渐变大理石", "★ 爪子刀 | 多普勒", "★ 运动手套 | 迈阿密风云", "★ M9 刺刀 | 传说", "★ 蝴蝶刀 | 传说"],
        "红": ["AWP | 永恒之枪", "AK-47 | 火蛇", "M4A1-S | 骑士", "M4A4 | 咆哮", "AK-47 | 野荷"],
        "粉": ["AWP | 闪电打击", "USP-S | 黑色莲花", "Desert Eagle | 红色代码", "AK-47 | 霓虹革命"],
        "紫": ["Glock-18 | 摩登时代", "MAC-10 | 霓虹骑士", "FAMAS | 元素轮廓", "Galil AR | 火箭冰棒"],
        "蓝": ["P250 | 翼手龙", "MP9 | 粘性物", "SSG 08 | 边境线", "Tec-9 | 竹林", "五七 | 耍酷"]
    }

    r = random.random()
    if r < 0.0026: grade, suffix = "金", "！！！卧槽！张皓博看了直接原地退役！"
    elif r < 0.0064: grade, suffix = "红", "隐秘级！红色大货！兄弟你这手气，不去打职业可惜了。"
    elif r < 0.032: grade, suffix = "粉", "保密级。可以啊，这波没亏。"
    elif r < 0.15: grade, suffix = "紫", "受限级。紫色心情，也就那样吧。"
    else: grade, suffix = "蓝", "军规级。蓝天白云，标准的保底。"

    item = random.choice(skins[grade])
    prefix = "StatTrak™ " if random.random() < 0.1 else ""
    return f"【Herb 模拟开箱：{case_name}】\n物品：{prefix}{item}\n品质：{grade}色\n点评：{suffix}"