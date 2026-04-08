def get_current_time():
    """获取当前的精确时间"""
    return f"现在是：{datetime.datetime.now().strftime('%H:%M:%S')}"