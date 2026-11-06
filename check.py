
import os
import re
import json
import ssl
import smtplib
import requests

from bs4 import BeautifulSoup
from email.message import EmailMessage
from datetime import datetime, timezone

# EC-STORE V4.3
# 库存检测 + 163邮件提醒

PRODUCT_ID = "14192401"
URL = f"https://www.ec-store.net/sws/g/g{PRODUCT_ID}/"

API = "https://api.github.com"
ISSUE_TITLE = "EC-STORE inventory state (automatic)"
MARKER = "EC_STOCK_STATE_V3"


def now():
    return datetime.now(timezone.utc).isoformat()


def github(method, path, **kwargs):
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    r = requests.request(
        method,
        API + path.format(repo=repo),
        headers=headers,
        timeout=20,
        **kwargs
    )
    r.raise_for_status()
    return r.json() if r.content else {}


def get_state():
    issues = github(
        "GET",
        "/repos/{repo}/issues",
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
        "status": "unknown",
        "alerted": None
    }


def save_state(number, state):
    body = json.dumps(
        state,
        ensure_ascii=False,
        indent=2
    )

    if number is None:
        result = github(
            "POST",
            "/repos/{repo}/issues",
            json={
                "title": ISSUE_TITLE,
                "body": body
            }
        )
        return result["number"]

    github(
        "PATCH",
        f"/repos/{{repo}}/issues/{number}",
        json={"body": body}
    )
    return number


def products(data):
    if isinstance(data, list):
        for item in data:
            yield from products(item)

    elif isinstance(data, dict):
        kinds = data.get("@type", [])
        if isinstance(kinds, str):
            kinds = [kinds]

        if "Product" in kinds:
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

        value = str(
            offer.get("availability") or ""
        ).rstrip("/").split("/")[-1]

        if value == "InStock":
            states.append("instock")
        elif value in ("OutOfStock", "SoldOut"):
            states.append("soldout")

    if not states or len(set(states)) != 1:
        return "unknown"

    return states[0]


def detect():
    r = requests.get(
        URL,
        timeout=25,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    print("HTTP:", r.status_code)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    records = []

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):
        try:
            data = json.loads(
                script.string or script.get_text()
            )
        except (ValueError, TypeError):
            continue

        for product in products(data):
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

            if PRODUCT_ID in ids:
                records.append(
                    offer_status(product.get("offers"))
                )

    known = {
        x for x in records
        if x != "unknown"
    }

    structured = (
        next(iter(known))
        if len(known) == 1
        else "unknown"
    )

    text = soup.get_text(" ", strip=True)

    free_soldout = bool(
        re.search(r"FREE\s*在庫なし", text, re.I)
    )
    restock = "再入荷お知らせ" in text

    # 只检查可见且未禁用的购物按钮
    # 不能把页面上任意文字当成购买按钮
    buy_button = False

    for element in soup.find_all(
        ["button", "input", "a"]
    ):
        label = " ".join([
            element.get_text(" ", strip=True),
            str(element.get("value") or ""),
            str(element.get("aria-label") or "")
        ])

        if not re.search(
            r"カートに入れる|カートへ入れる",
            label
        ):
            continue

        classes = element.get("class") or []

        disabled = (
            element.has_attr("disabled")
            or str(
                element.get("aria-disabled", "")
            ).lower() == "true"
            or "disabled" in classes
        )

        if not disabled and not element.has_attr("hidden"):
            buy_button = True

    print("匹配商品记录:", records)
    print("明确结构化状态:", structured)
    print("FREE 在庫なし:", free_soldout)
    print("再入荷お知らせ:", restock)
    print("HTML购买按钮:", buy_button)

    # 双重证据支持无货
    if structured == "soldout" and (
        free_soldout or restock
    ):
        return "soldout"

    # 明确有货数据与无货提示冲突
    if structured == "instock" and free_soldout:
        return "unknown"

    # 结构化数据提示有货
    if structured == "instock":
        if buy_button:
            return "candidate_button"
        return "candidate"

    return "unknown"


def send_email(status):
    required = [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASS",
        "MAIL_TO"
    ]

    for key in required:
        if not os.getenv(key):
            raise RuntimeError(f"缺少配置: {key}")

    msg = EmailMessage()

    if status == "candidate_button":
        subject = "【疑似补货】EC-STORE 已发现购买按钮"
        description = (
            "商品结构化数据提示有货，"
            "HTML中也发现未标记禁用的购买按钮。"
            "但尚未验证按钮实际可用。"
        )
    else:
        subject = "【疑似补货】EC-STORE 库存数据变化"
        description = (
            "商品结构化数据提示有货，"
            "但尚未确认实际购买按钮是否可用。"
        )

    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]

    msg.set_content(
        f"{description}\n\n"
        f"商品编号：{PRODUCT_ID}\n"
        f"检测时间（UTC）：{now()}\n"
        f"商品链接：{URL}\n\n"
        "请打开商品页面人工确认库存。\n"
        "本提醒不保证商品一定可以买到。"
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

    print("邮件已提交给SMTP服务器")


def main():
    print("EC-STORE V4.3 AUTO ALERT")

    number, state = get_state()
    previous = state.get("status", "unknown")
    current = detect()

    print("上次状态:", previous)
    print("当前状态:", current)

    if current == "unknown":
        print("状态不确定，不通知")
        return

    if current == "soldout":
        state["status"] = "soldout"
        state["alerted"] = None
        state["checked_at"] = now()
        number = save_state(number, state)
        print("无货基线已保存")
        return

    # 仅从已记录的无货状态转变时通知
    if previous != "soldout":
        print("缺少无货基线，不发送提醒")
        return

    # 防止同一次变化重复通知
    if state.get("alerted") is not None:
        print("已有提醒记录，不重复发送")
        return

    # 先记录通知意图，降低重复发送风险
    state["alerted"] = "pending"
    state["alert_intent_at"] = now()
    number = save_state(number, state)

    send_email(current)

    state["status"] = current
    state["alerted"] = current
    state["notified_at"] = now()
    save_state(number, state)

    print("疑似补货提醒已发送")


if __name__ == "__main__":
    main()
