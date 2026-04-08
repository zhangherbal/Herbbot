import requests
import time


def get_weibo_hot_search():
    """获取微博热搜榜单 - OpenClaw 数据锁版本"""
    url = 'https://weibo.com/ajax/side/hotSearch'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://weibo.com/'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return f"微博服务器请求失败 (状态码: {response.status_code})"

        data = response.json().get('data', {})
        news_results = []

        # 1. 处理置顶 (带有特殊标识)
        hotgov = data.get('hotgov', {})
        if hotgov:
            news_results.append(f"📌 [置顶] {hotgov.get('word')}")

        # 2. 处理列表 (前15名)
        for i, item in enumerate(data.get('realtime', [])[:15], 1):
            word = item.get('word', '未知话题')
            label = item.get('label_name', '')
            num = item.get('num', '---')
            # 采用更易读的列表格式
            news_results.append(f"{i:02d}. {word} [{label}] - 📈热度:{num}")

        # 3. 核心重构：使用 OpenClaw 风格的隔离标签
        # 增加 <DATA_BLOCK> 标签，强制让 persona 节点识别这是不可改写区
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

        output = (
                f"\n<DATA_BLOCK type='weibo_hot_search' time='{timestamp}'>\n"
                f"### 🕒 微博实时热搜榜单 ({timestamp})\n"
                "------------------------------------------\n"
                + "\n".join(news_results) +
                "\n------------------------------------------\n"
                f"</DATA_BLOCK>\n"
        )

        return output

    except Exception as e:
        return f"获取热搜异常：{str(e)}"