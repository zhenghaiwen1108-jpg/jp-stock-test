
import os
import json
import ssl
import smtplib
import requests

from bs4 import BeautifulSoup
from email.message import EmailMessage
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

MARKER = "EC_STOCK_STATE_V3"
ISSUE_TITLE = "EC-STORE inventory state (automatic)"

IN_STOCK = {
    "https://schema.org/InStock",
    "http://schema.org/InStock",
    "InStock"
}

OUT_OF_STOCK = {
    "https://schema.org/OutOfStock",
    "http://schema.org/OutOfStock",
    "OutOfStock",
    "https://schema.org/SoldOut",
    "http://schema.org/SoldOut",
    "SoldOut"
}


def github(method, path, **kwargs):
    response = requests.request(
        method,
        API + path,
        headers=HEADERS,
        timeout=20,
        **kwargs
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def get_state():
    issues = github(
        "GET",
        f"/repos/{REPO}/issues",
        params={"state": "open", "per_page": 100}
    )

    for issue in issues:
        if issue.get("title") != ISSUE_TITLE:
            continue

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

    if number is not None:
        github(
            "PATCH",
            f"/repos/{REPO}/issues/{number}",
            json={"body": body}
        )
    else:
        github(
            "POST",
            f"/repos/{REPO}/issues",
            json={
                "title": ISSUE_TITLE,
                "body": body
            }
        )


def find_products(data):
    if isinstance(data, list):
        for item in data:
            yield from find_products(item)

    elif isinstance(data, dict):
        if data.get("@type") == "Product":
            yield data

        elif isinstance(data.get("@type"), list):
            if "Product" in data["@type"]:
                yield data

        for value in data.values():
            if isinstance(value, (dict, list)):
                yield from find_products(value)


def product_matches(product):
    identifiers = [
        product.get("sku"),
        product.get("productID"),
        product.get("mpn")
    ]

    for identifier in identifiers:
        if str(identifier or "").strip() == PRODUCT_ID:
            return True

    return False


def offer_status(offers):
    if isinstance(offers, dict):
        offers = [offers]

    if not isinstance(offers, list) or not offers:
        return "unknown"

    statuses = []

    for offer in offers:
        if not isinstance(offer, dict):
            return "unknown"

        availability = str(
            offer.get("availability") or ""
        ).strip()

        if availability in IN_STOCK:
            statuses.append("instock")

        elif availability in OUT_OF_STOCK:
            statuses.append("soldout")

        else:
            statuses.append("unknown")

    if "unknown" in statuses:
        return "unknown"

    if "instock" in statuses:
        return "instock"

    if statuses and all(
        s == "soldout" for s in statuses
    ):
        return "soldout"

    return "unknown"


def detect():
    response = requests.get(
        URL,
        timeout=25,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; StockMonitor/4.0)"
            )
        }
    )

    print("HTTP:", response.status_code)
    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    scripts = soup.find_all(
        "script",
        type="application/ld+json"
    )

    found = []

    for script in scripts:
        try:
            data = json.loads(script.string or script.text)
        except (ValueError, TypeError):
            continue

        for product in find_products(data):
            if not product_matches(product):
                continue

            found.append(
                offer_status(product.get("offers"))
            )

    print("匹配商品数量:", len(found))
    print("商品状态记录:", found)

    if not found:
        print("未找到匹配商品的结构化库存数据")
        return "unknown"

    if "unknown" in found:
        return "unknown"

    if all(s == "soldout" for s in found):
        return "soldout"

    if all(s == "instock" for s in found):
        return "instock"

    return "unknown"


def send_email():
    required = [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASS",
        "MAIL_TO"
    ]

    for key in required:
        if not os.getenv(key):
            raise RuntimeError(
                f"缺少邮箱配置: {key}"
            )

    msg = EmailMessage()

    msg["Subject"] = (
        "【库存提醒】EC-STORE 商品库存状态变化"
    )
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]

    msg.set_content(
        "EC-STORE 商品检测到可能补货。\n\n"
        f"商品编号：{PRODUCT_ID}\n"
        f"商品链接：{URL}\n\n"
        "请立即打开商品页面，"
        "人工确认规格及实际库存。\n"
        "本邮件不保证商品一定可以购买。"
    )

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(
        os.environ["SMTP_HOST"],
        int(os.environ["SMTP_PORT"]),
        context=context,
        timeout=25
    ) as server:

        server.login(
            os.environ["SMTP_USER"],
            os.environ["SMTP_PASS"]
        )

        server.send_message(msg)

    print("补货提醒已提交给邮件服务器")


def main():
    number, state = get_state()

    previous = state.get("status", "unknown")
    current = detect()

    print("上次状态:", previous)
    print("当前状态:", current)

    now = datetime.now(
        timezone.utc
    ).isoformat()

    if current == "unknown":
        print("库存无法确认，本次不发送邮件")
        return

    # 必须先有可靠的无货基线
    # 才能判定无货转为有货
    if previous == "soldout" and current == "instock":

        print("检测到无货转为有货")

        # 先记录通知意图，避免重复发送
        state["status"] = "instock"
        state["notification"] = "pending"
        state["checked_at"] = now
        save_state(number, state)

        send_email()

        state["notification"] = "sent"
        state["notified_at"] = now
        save_state(number, state)

        print("补货提醒流程完成")
        return

    if current == "instock" and state.get(
        "notification"
    ) == "pending":
        print("存在待核查通知，不自动重复发送")
        return

    if previous == current:
        print("库存状态未变化，不重复通知")
        return

    state["status"] = current
    state["checked_at"] = now

    save_state(number, state)

    print("库存状态已保存")


if __name__ == "__main__":
    main()
