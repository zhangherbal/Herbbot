import re
import time

def set_reminder(minutes: float = 0, seconds: float = 0, task: str = "提醒任务", **kwargs):
    """
    设置一个定时提醒任务。
    支持直接传入数值，或通过 duration_str 自动解析。
    """
    total_seconds = 0
    duration_str = kwargs.get("duration_str", "")

    # 1. 如果有直接数值传入
    if minutes > 0 or seconds > 0:
        total_seconds = int(float(minutes) * 60 + float(seconds))

    # 2. 如果是通过自然语言提取的 duration_str (支持：1小时20分30秒)
    elif duration_str:
        # 支持：小时/时，分钟/分，秒钟/秒
        h_match = re.search(r"(\d+\.?\d*)\s*(?:小时|时)", duration_str)
        m_match = re.search(r"(\d+\.?\d*)\s*(?:分钟|分)", duration_str)
        s_match = re.search(r"(\d+\.?\d*)\s*(?:秒钟|秒)", duration_str)

        h_val = float(h_match.group(1)) if h_match else 0
        m_val = float(m_match.group(1)) if m_match else 0
        s_val = float(s_match.group(1)) if s_match else 0

        total_seconds = int(h_val * 3600 + m_val * 60 + s_val)

    # 3. 结果校验
    if total_seconds <= 0:
        return "⚠️ 提醒设置失败：老板，你这时间给得不对啊，没法掐表。"

    # 4. 返回标准格式，[SEC:xxx] 用于后端定时器逻辑识别
    # 修复了你原代码中重复【task】的问题
    set_at = time.strftime("%M:%S")
    return f"✅ 闹钟已定好！[SEC:{total_seconds}] 任务：【{task}】(设定于{set_at})"