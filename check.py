
import os
import re
import json
import ssl
import smtplib
import requests

from bs4 import BeautifulSoup
from email.message import EmailMessage
from datetime import datetime, timezone

# EC-STORE V4.2
# 商品库存监控与安全校准

PRODUCT_ID = "14192401"
URL = f"https://www.ec-store.net/sws/g/g{PRODUCT_ID}/"

API = "https://api.github.com"
MARKER = "EC_STOCK_STATE_V3"
ISSUE_TITLE = "EC-STORE inventory state (automatic)"


def github(method, path, **kwargs):
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    response = requests.request(
        method,
        API + path.format(repo=repo),
        headers=headers,
        timeout=20,
        **kwargs
    )

    response.raise_for_status()

    return response.json() if response.content else {}


def get_state():
    issues = github(
        "GET",
        "/repos/{repo}/issues",
        params={
            "state": "open",
            "per_page": 100
        }
    )

    for issue in issues:
        if issue.get("title") != ISSUE_TITLE:
            continue

        try:
            state = json.loads(
                issue.get("body") or "{}"
            )

            if state.get("marker") == MARKER:
                return issue["number"], state

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
            "/repos/{repo}/issues",
            json={
                "title": ISSUE_TITLE,
                "body": body
            }
        )
    else:
        github(
            "PATCH",
            f"/repos/{{repo}}/issues/{number}",
            json={"body": body}
        )


def products(data):
    if isinstance(data, list):
        for item in data:
            yield from products(item)

    elif isinstance(data, dict):
        kind = data.get("@type", [])

        if isinstance(kind, str):
            kind = [kind]

        if "Product" in kind:
            yield data

        for value in data.values():
            if isinstance(value, (dict, list)):
                yield from products(value)


def offer_status(offers):
    if isinstance(offers, dict):
        offers = [offers]

    if not isinstance(offers, list):
        return "unknown"

    states = []

    for offer in offers:
        if not isinstance(offer, dict):
            continue

        raw = str(
            offer.get("availability") or ""
        ).rstrip("/").split("/")[-1]

        if raw == "InStock":
            states.append("instock")

        elif raw in ("OutOfStock", "SoldOut"):
            states.append("soldout")

    if not states:
        return "unknown"

    if len(set(states)) != 1:
        return "unknown"

    return states[0]


def detect():
    response = requests.get(
        URL,
        timeout=25,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    print("HTTP:", response.status_code)
    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    records = []

    scripts = soup.find_all(
        "script",
        type="application/ld+json"
    )

    for script in scripts:
        try:
            payload = json.loads(
                script.string or script.get_text()
            )

        except (ValueError, TypeError):
            continue

        for product in products(payload):
            identifiers = [
                product.get("sku"),
                product.get("productID"),
                product.get("mpn")
            ]

            identifiers = [
                str(x).strip()
                for x in identifiers
                if x is not None
            ]

            if PRODUCT_ID not in identifiers:
                continue

            status = offer_status(
                product.get("offers")
            )

            records.append(status)

    known = {
        x for x in records
        if x != "unknown"
    }

    if len(known) == 1:
        structured = next(iter(known))
    else:
        structured = "unknown"

    text = soup.get_text(" ", strip=True)

    free_soldout = bool(
        re.search(
            r"FREE\s*在庫なし",
            text,
            re.I
        )
    )

    restock = "再入荷お知らせ" in text

    print("EC-STORE V4.2")
    print("匹配商品记录:", records)
    print("明确结构化状态:", structured)
    print("FREE 在庫なし:", free_soldout)
    print("再入荷お知らせ:", restock)

    # 结构化数据和页面文字共同支持无货
    if structured == "soldout" and (
        free_soldout or restock
    ):
        return "soldout"

    # 结构化数据提示有货
    # 但尚未验证实际购买按钮
    if (
        structured == "instock"
        and not free_soldout
        and not restock
    ):
        return "candidate"

    return "unknown"


def send_email():
    required = (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASS",
        "MAIL_TO"
    )

    for key in required:
        if not os.getenv(key):
            raise RuntimeError(
                f"缺少配置: {key}"
            )

    msg = EmailMessage()

    msg["Subject"] = (
        "【待确认补货】EC-STORE 14192401"
    )

    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]

    msg.set_content(
        "商品库存数据提示可能补货，"
        "请手动检查购买按钮。\n"
        + URL
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


def main():
    print("EC-STORE V4.2 CHECK")

    number, state = get_state()

    previous = state.get(
        "status",
        "unknown"
    )

    current = detect()

    print("上次状态:", previous)
    print("当前状态:", current)

    if current == "unknown":
        print("状态不确定，不更新历史，不发送邮件")
        return

    if current == "candidate":
        print("可能补货，但尚未验证购买按钮")
        print("本次不发送邮件")
        return

    # 只保存有双重证据支持的无货基线
    state["status"] = "soldout"

    state["checked_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    state["verification"] = (
        "structured_plus_page_soldout"
    )

    save_state(number, state)

    print("已记录无货基线")
    print("有货验证与邮件自动提醒尚未启用")


if __name__ == "__main__":
    main()
