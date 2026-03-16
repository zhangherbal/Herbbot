import datetime
import requests
import urllib.request
import gzip
from bs4 import BeautifulSoup
import time
import re
import random
def get_current_time():
    """获取当前系统时间"""
    return f"现在是：{datetime.datetime.now().strftime('%H:%M:%S')}"

def daily_quote():
    """获取今日励志（或毒舌）语录"""
    return "咱就是说，早起真的需要勇气。"


def get_weibo_hot_search():
    """
    获取微博热搜 
    """
    print('************** Herb 正在同步微博热搜 **************')

    url = 'https://weibo.com/ajax/side/hotSearch'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://weibo.com/'
    }

    try:
        # 设置超时，防止机器人卡死
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            return f"微博服务器拒绝了 Herb 的请求 (状态码: {response.status_code})"

        json_data = response.json()
        data = json_data.get('data', {})

        news_results = []

        # 1. 处理置顶/政府热搜
        hotgov = data.get('hotgov', {})
        if hotgov:
            news_results.append(f"🔥 [置顶] {hotgov.get('word')} (官方推广)")

        # 2. 处理实时热搜列表 (取前 15 条)
        realtime = data.get('realtime', [])
        for i, item in enumerate(realtime[:15], 1):
            word = item.get('word', '未知话题')
            label = item.get('label_name', '')
            # 有些热搜带热度数值，有些不带
            num = item.get('num', '---')

            label_str = f" [{label}]" if label else ""
            news_results.append(f"{i}. {word}{label_str} (热度:{num})")

        if not news_results:
            return "热搜列表竟然是空的？微博可能又在维护了。"

        # 3. 格式化输出
        get_time = time.strftime('%H:%M:%S', time.localtime())
        content = "【Herb 实时播报：微博热搜】\n" + "\n".join(news_results)
        content += f"\n\n更新时间：{get_time}\n吃瓜要紧，Rush B 先等等。"

        return content

    except Exception as e:
        return f"获取热搜时发生了意外：{str(e)}"
def set_reminder(minutes: float = 0, seconds: float = 0, task: str = "任务", **kwargs):
    """
    设置提醒任务。支持显式分钟、秒或混合字符串解析。
    """
    total_seconds = 0
    duration_str = kwargs.get("duration_str", "")


    if minutes > 0 or seconds > 0:
        total_seconds = int(float(minutes) * 60 + float(seconds))


    elif duration_str:

        m_match = re.search(r"(\d+\.?\d*)分", duration_str)
        s_match = re.search(r"(\d+\.?\d*)秒", duration_str)

        m_val = float(m_match.group(1)) if m_match else 0
        s_val = float(s_match.group(1)) if s_match else 0
        total_seconds = int(m_val * 60 + s_val)

    if total_seconds <= 0:
        return "提醒失败：未识别到有效时间，请明确说出时长（如：30秒后提醒我）"

    return f"已设置提醒【{task}】 [SEC:{total_seconds}]【{task}】"


def get_weather(city: str):
    try:

        url = f"https://wttr.in/{city}?format=j1&lang=zh"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()


            curr = data['current_condition'][0]
            today = data['weather'][0]

            tomorrow = data['weather'][1]
            t_desc = tomorrow['hourly'][4]['lang_zh'][0]['value']  # 取中午前后的描述
            t_max = tomorrow['maxtempC']
            t_min = tomorrow['mintempC']

            hourly_next = today['hourly'][1]
            chance_rain = hourly_next['chanceofrain']

            res = (
                f"【{city}天气深度汇报】\n"
                f"● 当前：{curr['lang_zh'][0]['value']} {curr['temp_C']}℃ (体感 {curr['FeelsLikeC']}℃)\n"
                f"● 今日波动：{today['mintempC']}℃ ~ {today['maxtempC']}℃\n"
                f"● 短期趋势：未来几小时有【{hourly_next['lang_zh'][0]['value']}】，降水概率 {chance_rain}%\n"
                f"-------------------\n"
                f"● 明天预报：{t_desc}，气温 {t_min}℃ ~ {t_max}℃\n"
            )


            if int(chance_rain) > 50:
                res += "⚠️ 别怪哥没提醒你，等下出门带把伞，别把键盘淋湿了。"
            elif int(t_max) > 30:
                res += "🔥 明天挺热的，建议窝在空调房里打竞技，别出去晒成肉夹馍。"
            else:
                res += "✅ 天气还可以，适合下楼吃顿好的补补手感。"

            return res
        return f"找不到 {city} 的地图包（数据），你确定这地方在地球上？"
    except Exception as e:
        return f"天气模块寄了，报错原因: {str(e)}"

def simulate_case_opening(case_name: str = "武器箱"):

    skins = {
        "金": [
            "★ 蝴蝶刀 | 渐变大理石 (崭新出厂)",
            "★ 爪子刀 | 多普勒 (蓝宝石)",
            "★ 运动手套 | 迈阿密风云 (略有磨损)",
            "★ M9 刺刀 | 传说 (久经沙场)",
            "★ 蝴蝶刀  | 传说 (崭新出厂)"
        ],
        "红": [
            "AWP | 永恒之枪 (崭新出厂)",
            "AK-47 | 火蛇 (久经沙场)",
            "M4A1-S | 骑士 (崭新出厂)",
            "M4A4 | 咆哮 (略有磨损)",
            "AK-47 | 野荷 (久经沙场)"
        ],
        "粉": [
            "AWP | 闪电打击 (崭新出厂)",
            "USP-S | 黑色莲花 (崭新出厂)",
            "Desert Eagle | 红色代码 (略有磨损)",
            "AK-47 | 霓虹革命 (久经沙场)"
        ],
        "紫": [
            "Glock-18 | 摩登时代 (略有磨损)",
            "MAC-10 | 霓虹骑士 (崭新出厂)",
            "FAMAS | 元素轮廓 (久经沙场)",
            "Galil AR | 火箭冰棒 (崭新出厂)"
        ],
        "蓝": [
            "P250 | 翼手龙 (久经沙场)",
            "MP9 | 粘性物 (战痕累累)",
            "SSG 08 | 边境线 (略有磨损)",
            "Tec-9 | 竹林 (久经沙场)",
            "五七 | 耍酷 (战痕累累)"
            ]
        }

    r = random.random()

            # 概率判定 (参考官方概率)
    if r < 0.0026:
        grade = "金"
        suffix = "！！！卧槽！张皓博看了直接原地退役！这把刀够你吃一年猪脚饭了！"
    elif r < 0.0064:
        grade = "红"
        suffix = "隐秘级！红色大货！兄弟你这手气，不去打职业可惜了。"
    elif r < 0.032:
        grade = "粉"
        suffix = "保密级。可以啊，这波没亏，这枪拿手里还挺帅。"
    elif r < 0.15:
        grade = "紫"
        suffix = "受限级。紫色心情，也就那样吧，打竞技凑合用。"
    else:
        grade = "蓝"
        suffix = "军规级。蓝天白云，标准的保底。听哥一句话，这行水太深，你把握不住。"

    item = random.choice(skins[grade])

    # 随机加上“StatTrak™”（暗金）属性
    is_stattrak = random.random() < 0.1
    prefix = "StatTrak™ " if is_stattrak else ""

    return f"【Herb 模拟开箱：{case_name}】\n物品：{prefix}{item}\n品质：{grade}色\n点评：{suffix}"


LOCAL_SKILLS_MAP = {
    "get_current_time": get_current_time,
    "daily_quote": daily_quote,
    "get_weather": get_weather,
    "get_weibo_hot_search": get_weibo_hot_search,
    "simulate_case_opening": simulate_case_opening,
    "set_reminder": set_reminder,  # 之前这里漏掉了！
}


SKILL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前的精确时间"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "daily_quote",
            "description": "获取今日的一句话语录"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的实时天气。注意：参数city只需传入城市名（如'日照'），不要带省份前缀。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，例如：北京、日照、上海"
                    }
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weibo_hot_search",
            "description": "返回微博热搜榜单"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_case_opening",
            "description": "模拟CSGO武器箱开箱过程",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_name": {
                        "type": "string",
                        "description": "武器箱名称，默认为'武器箱'"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "设置一个定时提醒任务",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "description": "多少分钟后提醒，支持小数如0.5"},
                    "task": {"type": "string", "description": "提醒内容"}
                },
                "required": ["minutes", "task"]
            }
        }
    }
]
