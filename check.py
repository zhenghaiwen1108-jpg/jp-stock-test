
import os
import re
import json
import ssl
import time
import smtplib
import requests

from bs4 import BeautifulSoup
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta

# =====================================
# EC-STORE V4.4
# 稳定库存检测 + 163邮箱提醒
# =====================================

PRODUCT_ID = "14192401"
URL = f"https://www.ec-store.net/sws/g/g{PRODUCT_ID}/"

API = "https://api.github.com"
ISSUE_TITLE = "EC-STORE inventory state (automatic)"
MARKER = "EC_STOCK_STATE_V3"

MAX_MAIL_ATTEMPTS = 3
RETRY_INTERVAL_MINUTES = 5
PENDING_TIMEOUT_MINUTES = 10


def utcnow():
    return datetime.now(timezone.utc)


def now():
    return utcnow().isoformat()


def parse_time(value):
    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (AttributeError, ValueError):
        return None


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

    if response.content:
        return response.json()

    return {}


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
            data = json.loads(
                issue.get("body") or "{}"
            )

            if data.get("marker") == MARKER:
                return issue["number"], data

        except (ValueError, TypeError):
            continue

    return None, {
        "marker": MARKER,
        "status": "unknown",
        "alerted": None,
        "mail_attempts": 0
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
        re.search(
            r"FREE\s*在庫なし",
            text,
            re.I
        )
    )

    restock = "再入荷お知らせ" in text

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

        classes = " ".join(
            element.get("class") or []
        ).lower()

        disabled = (
            element.has_attr("disabled")
            or element.has_attr("hidden")
            or str(
                element.get("aria-disabled", "")
            ).lower() == "true"
            or "disabled" in classes
        )

        if not disabled:
            buy_button = True

    print("商品编号:", PRODUCT_ID)
    print("匹配商品记录:", records)
    print("明确结构化状态:", structured)
    print("FREE 在庫なし:", free_soldout)
    print("再入荷お知らせ:", restock)
    print("HTML购买按钮:", buy_button)

    # 明确的无货证据
    if structured == "soldout":
        if free_soldout or restock:
            return "soldout"
        return "unknown"

    # 有货结构化数据与无货标记冲突
    if structured == "instock":
        if free_soldout or restock:
            return "unknown"

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
            raise RuntimeError(
                f"缺少邮箱配置: {key}"
            )

    msg = EmailMessage()

    if status == "candidate_button":
        subject = (
            "【疑似补货】EC-STORE 发现购买按钮"
        )
        description = (
            "结构化库存数据提示有货，"
            "HTML中发现未标记禁用的购买按钮。"
            "尚未验证实际能否下单。"
        )
    else:
        subject = (
            "【疑似补货】EC-STORE 库存状态变化"
        )
        description = (
            "结构化库存数据提示有货，"
            "但购买按钮尚未得到验证。"
        )

    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]

    msg.set_content(
        f"{description}\n\n"
        f"商品编号：{PRODUCT_ID}\n"
        f"检测时间 UTC：{now()}\n"
        f"商品链接：{URL}\n\n"
        "请尽快打开商品页面人工确认。\n"
        "此邮件不保证商品一定有库存。"
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

    print("SMTP已接受邮件")


def cooldown_finished(state):
    last = parse_time(
        state.get("last_mail_attempt_at")
    )

    if last is None:
        return True

    return utcnow() >= last + timedelta(
        minutes=RETRY_INTERVAL_MINUTES
    )


def main():
    print("EC-STORE V4.4 STABLE ALERT")

    number, state = get_state()

    previous = state.get(
        "status",
        "unknown"
    )

    current = detect()

    print("上次状态:", previous)
    print("当前状态:", current)

    # 不确定时不改变已确认状态
    if current == "unknown":
        print("库存不确定，保留历史")
        return

    # 无货时重置本轮通知
    if current == "soldout":
        state["status"] = "soldout"
        state["alerted"] = None
        state["mail_attempts"] = 0
        state["last_mail_attempt_at"] = None
        state["checked_at"] = now()

        save_state(number, state)

        print("无货基线已保存")
        return

    # 只有从无货状态变化时才提醒
    if previous != "soldout":
        print("未找到无货基线，不通知")
        return

    alerted = state.get("alerted")

    if alerted in (
        "candidate",
        "candidate_button",
        "sent"
    ):
        print("本轮已经提醒，不重复发送")
        return

    if alerted == "pending":
        pending_at = parse_time(
            state.get("last_mail_attempt_at")
        )

        if pending_at is not None:
            elapsed = utcnow() - pending_at

            if elapsed < timedelta(
                minutes=PENDING_TIMEOUT_MINUTES
            ):
                print("上次邮件仍在等待确认")
                return

        print("上次发送结果不明确，暂停自动重发")
        print("请人工核实邮件与GitHub记录")
        return

    attempts = int(
        state.get("mail_attempts") or 0
    )

    if attempts >= MAX_MAIL_ATTEMPTS:
        print("邮件重试次数已达到上限")
        return

    if not cooldown_finished(state):
        print("邮件重试冷却中")
        return

    # 先记录发送意图，避免并发重复
    state["alerted"] = "pending"
    state["mail_attempts"] = attempts + 1
    state["last_mail_attempt_at"] = now()

    number = save_state(number, state)

    try:
        send_email(current)

    except (OSError, smtplib.SMTPException) as exc:
        print(
            "邮件发送异常:",
            type(exc).__name__,
            str(exc)[:200]
        )

        # SMTP异常不一定意味着邮件未被接受
        # 为避免重复提醒，不盲目重发
        state["alerted"] = "pending"
        state["last_mail_error"] = (
            type(exc).__name__
        )

        save_state(number, state)
        raise

    # SMTP成功后记录通知状态
    state["status"] = current
    state["alerted"] = "sent"
    state["notified_at"] = now()
    state["last_mail_error"] = None

    save_state(number, state)

    print("疑似补货邮件已提交")
    print("本轮提醒状态已保存")


if __name__ == "__main__":
    main()
