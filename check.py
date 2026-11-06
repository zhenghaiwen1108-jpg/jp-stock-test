
import requests
from datetime import datetime, timezone

PRODUCTS = {
    "EC-STORE 14192401":
        "https://www.ec-store.net/sws/g/g14192401/",
    "Sanrio House 75WGG263004":
        "https://mashstylelab.jp/sanriohouse/Form/Product/ProductDetail.aspx?shop=0&pid=75WGG263004&vid=75WGG26300471399&bid=SRO01"
}

for name, url in PRODUCTS.items():
    print("\n========================")
    print("商品:", name)
    print("检测时间:", datetime.now(timezone.utc).isoformat())

    try:
        response = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        print("HTTP状态:", response.status_code)
        print("页面大小:", len(response.content))
        print("最终地址:", response.url)

        for keyword in [
            "在庫あり",
            "在庫なし",
            "カートに入れる",
            "カートへ入れる",
            "SOLD OUT",
            "完売"
        ]:
            if keyword in response.text:
                print("发现关键词:", keyword)

        print("检测完成（尚未确认真实库存）")

    except requests.RequestException as e:
        print("访问失败:", str(e))
