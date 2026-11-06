
import json
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

URL = "https://www.ec-store.net/sws/g/g14192401/"
PRODUCT_ID = "14192401"

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
    r = requests.request(
        method, API + path,
        headers=HEADERS,
        timeout=20,
        **kwargs
    )
    r.raise_for_status()
    return r.json() if r.content else {}


def get_state():
    issues = github(
        "GET",
        f"/repos/{REPO}/issues",
        params={"state": "open", "per_page": 100}
    )

    for issue in issues:
        if issue.get("title") == ISSUE_TITLE:
            try:
                data = json.loads(issue.get("body") or "{}")
                if data.get("marker") == MARKER:
                    return issue["number"], data
            except (ValueError, TypeError):
                pass

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
            json={"title": ISSUE_TITLE, "body": body}
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
            if isinstance(value, (list, dict)):
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
            offer.get("availability", "")
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
    r = requests.get(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
        timeout=25
    )

    print("HTTP:", r.status_code)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    matches = []

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):
        try:
            data = json.loads(script.string or script.text)
        except (ValueError, TypeError):
            continue

        for product in find_products(data):
            ids = [
                product.get("sku"),
                product.get("productID"),
                product.get("mpn")
            ]

            if PRODUCT_ID not in [
                str(x).strip() for x in ids if x is not None
            ]:
                continue

            matches.append(
                get_availability(product.get("offers"))
            )

    print("匹配商品数量:", len(matches))
    print("商品状态记录:", matches)

    if not matches or "unknown" in matches:
        return "unknown"

    if all(x == "soldout" for x in matches):
        return "soldout"

    if all(x == "instock" for x in matches):
        return "instock"

    return "unknown"


def main():
    print("EC-STORE V4.0 SAFE DIAGNOSTIC")

    number, state = get_state()
    previous = state.get("status", "unknown")
    current = detect()

    print("上次状态:", previous)
    print("当前状态:", current)

    if current == "unknown":
        print("无法确认商品库存，不发送邮件")
        return

    # V4诊断阶段只记录，不自动通知
    state["status"] = current
    state["checked_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    save_state(number, state)

    print("历史状态已保存")
    print("诊断模式：邮件提醒暂未启用")


if __name__ == "__main__":
    main()
