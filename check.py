
import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

# ======================================
# EC-STORE V4.1 商品库存精准诊断版
# ======================================

PRODUCT_ID = "14192401"
URL = "https://www.ec-store.net/sws/g/g14192401/"

API = "https://api.github.com"
REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}

ISSUE_TITLE = "EC-STORE inventory state (automatic)"
MARKER = "EC_STOCK_STATE_V3"


def github(method, path, **kwargs):
    response = requests.request(
        method,
        API + path,
        headers=HEADERS,
        timeout=20,
        **kwargs
    )

    response.raise_for_status()

    if response.content:
        return response.json()

    return {}


def get_state():
    issues = github(
        "GET",
        f"/repos/{REPO}/issues",
        params={
            "state": "open",
            "per_page": 100
        }
    )

    for issue in issues:
        if issue.get("title") != ISSUE_TITLE:
            continue

        try:
            data = json.loads(
                issue.get("body") or "{}"
            )

            if data.get("marker") == MARKER:
                return issue["number"], data

        except (ValueError, TypeError):
            continue

    return None, {
        "marker": MARKER,
        "status": "unknown"
    }


def save_state(number, state):
    body = json.dumps(
        state,
        ensure_ascii=False,
        indent=2
    )

    if number is None:
        github(
            "POST",
            f"/repos/{REPO}/issues",
            json={
                "title": ISSUE_TITLE,
                "body": body
            }
        )
    else:
        github(
            "PATCH",
            f"/repos/{REPO}/issues/{number}",
            json={"body": body}
        )


def find_products(data):
    if isinstance(data, list):
        for item in data:
            yield from find_products(item)

    elif isinstance(data, dict):
        types = data.get("@type", [])

        if isinstance(types, str):
            types = [types]

        if "Product" in types:
            yield data

        for value in data.values():
            if isinstance(value, (dict, list)):
                yield from find_products(value)


def get_availability(offers):
    if isinstance(offers, dict):
        offers = [offers]

    if not isinstance(offers, list) or not offers:
        return "unknown"

    results = []

    for offer in offers:
        if not isinstance(offer, dict):
            return "unknown"

        value = str(
            offer.get("availability") or ""
        ).rstrip("/").split("/")[-1]

        if value == "InStock":
            results.append("instock")

        elif value in ("OutOfStock", "SoldOut"):
            results.append("soldout")

        else:
            results.append("unknown")

    if "unknown" in results:
        return "unknown"

    if all(x == "soldout" for x in results):
        return "soldout"

    if all(x == "instock" for x in results):
        return "instock"

    return "unknown"


def detect():
    response = requests.get(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
        timeout=25
    )

    print("HTTP:", response.status_code)
    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    matches = []
    details = []

    scripts = soup.find_all(
        "script",
        type="application/ld+json"
    )

    for script in scripts:
        try:
            data = json.loads(
                script.string or script.text
            )
        except (ValueError, TypeError):
            continue

        for product in find_products(data):
            ids = [
                product.get("sku"),
                product.get("productID"),
                product.get("mpn")
            ]

            ids = [
                str(x).strip()
                for x in ids
                if x is not None
            ]

            if PRODUCT_ID not in ids:
                continue

            status = get_availability(
                product.get("offers")
            )

            matches.append(status)

            # 只记录非敏感的商品字段
            offers = product.get("offers")
            offer_details = []

            if isinstance(offers, dict):
                offers = [offers]

            if isinstance(offers, list):
                for offer in offers:
                    if isinstance(offer, dict):
                        offer_details.append({
                            "availability": offer.get(
                                "availability"
                            ),
                            "sku": offer.get("sku"),
                            "url": offer.get("url")
                        })

            details.append({
                "name": product.get("name"),
                "sku": product.get("sku"),
                "productID": product.get("productID"),
                "status": status,
                "offers": offer_details
            })

    print("=== V4.1 商品数据诊断 ===")

    for index, item in enumerate(details, 1):
        print(
            f"记录 {index}:",
            json.dumps(
                item,
                ensure_ascii=False
            )
        )

    print("匹配商品数量:", len(matches))
    print("商品状态记录:", matches)

    if not matches:
        print("没有找到商品结构化库存数据")
        return "unknown"

    if "unknown" in matches:
        print("存在无法确定的库存记录")
        return "unknown"

    if all(x == "soldout" for x in matches):
        return "soldout"

    if all(x == "instock" for x in matches):
        return "instock"

    print("商品库存记录存在冲突")
    return "unknown"


def main():
    print("EC-STORE V4.1 DIAGNOSTIC")
    print("监控商品:", PRODUCT_ID)
    print("监控网址:", URL)

    number, state = get_state()

    previous = state.get(
        "status",
        "unknown"
    )

    current = detect()

    print("上次状态:", previous)
    print("当前状态:", current)

    if current == "unknown":
        print("无法确认库存，保留历史状态")
        print("本次不发送补货邮件")
        return

    # 当前版本先进行安全诊断
    # 不自动发送邮件，避免误报

    state["status"] = current
    state["checked_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    save_state(number, state)

    print("历史状态已保存")
    print("诊断模式：邮件提醒暂未启用")


if __name__ == "__main__":
    main()
