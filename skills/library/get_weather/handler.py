import  requests
def get_weather(city: str):
    """查询指定城市的实时天气"""
    AMAP_KEY = "f7447a99e3b6454ebbb12285dccda1be"
    try:
        clean_city = city.replace("查询", "").strip()
        geo_url = f"https://restapi.amap.com/v3/geocode/geo?address={clean_city}&key={AMAP_KEY}"
        geo_resp = requests.get(geo_url, timeout=5).json()

        if geo_resp.get('status') == '1' and geo_resp.get('geocodes'):
            adcode = geo_resp['geocodes'][0]['adcode']
            city_name = geo_resp['geocodes'][0]['formatted_address']
        else:
            return f"找不到 【{city}】 的坐标，这地方是不是还没开服？"

        weather_url = f"https://restapi.amap.com/v3/weather/weatherInfo?city={adcode}&key={AMAP_KEY}&extensions=all"
        w_resp = requests.get(weather_url, timeout=5).json()

        if w_resp.get('status') == '1' and w_resp.get('forecasts'):
            f = w_resp['forecasts'][0]
            cast = f['casts'][0]
            return (
                f"【{city_name} 战况简报】\n"
                f"● 天气：{cast['dayweather']} 转 {cast['nightweather']}\n"
                f"● 温度：{cast['nighttemp']}℃ ~ {cast['daytemp']}℃\n"
                f"● 风力：{cast['daywind']}风 {cast['daypower']}级\n"
                f"-------------------\n"
                f"链路同步成功，Herb 建议：别管几级风，稳住心态就能赢。"
            )
        return "高德基站断连，这波天气没抓到。"
    except Exception as e:
        return f"天气模块由于硬件错误寄了: {str(e)}"