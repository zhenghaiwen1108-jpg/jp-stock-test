
import os
import re
import json
import smtplib
import requests
from bs4 import BeautifulSoup
from email.message import EmailMessage
from datetime import datetime, timezone

URL = "https://www.ec-store.net/sws/g/g14192401/"
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

def github(method, path, **kwargs):
    r = requests.request(
        method, API + path, headers=HEADERS,
        timeout=20, **kwargs
    )
    r.raise_for_status()
    return r.json() if r.content else {}

def get_state():
    issues = github(
        "GET", f"/repos/{REPO}/issues",
        params={"state": "open", "per_page": 100}
    )
    for issue in issues:
        if issue["title"] == ISSUE_TITLE:
            try:
                data = json.loads(issue["body"])
                if data.get("marker") == MARKER:
                    return issue["number"], data
            except (ValueError, TypeError):
                pass
    return None, {
        "marker": MARKER,
        "status": "unknown"
    }

def save_state(number, state):
    body = json.dumps(state, ensure_ascii=False)
    if number:
        github(
            "PATCH",
            f"/repos/{REPO}/issues/{number}",
            json={"body": body}
        )
    else:
        github(
            "POST", f"/repos/{REPO}/issues",
            json={"title": ISSUE_TITLE, "body": body}
        )

def detect():
    r = requests.get(
        URL,
        timeout=25,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; StockMonitor/3.0)"
        }
    )
    print("HTTP:", r.status_code)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    # 只识别商品级的明确状态。
    # 多规格页面不能仅凭全页关键词判定有货。
    sold = bool(re.search(
        r"在庫なし|SOLD\s*OUT|売り切れ|完売",
        text, re.I
    ))
    available = bool(re.search(
        r"在庫あり|カートに入れる|カートへ入れる",
        text
    ))

    print("无货关键词:", sold)
    print("有货关键词:", available)

    # 同时存在时，无法确定具体规格
    if sold and available:
        return "unknown"
    if sold:
        return "soldout"

    # 仅有全页有货关键词仍不足以确认指定商品
    if available:
        return "possible"

    return "unknown"

def send_email():
    required = [
        "SMTP_HOST", "SMTP_USER",
        "SMTP_PASS", "MAIL_TO"
    ]
    if any(not os.getenv(k) for k in required):
        raise RuntimeError("邮件配置不完整")

    msg = EmailMessage()
    msg["Subject"] = "【库存提醒】EC-STORE 商品可能补货"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg.set_content(
        "EC-STORE 商品 14192401 检测到库存状态变化。\n"
        "请打开商品页面人工确认库存：\n" + URL
    )

    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "465"))

    if port == 465:
        with smtplib.SMTP_SSL(
            host, port, timeout=20
        ) as server:
            server.login(
                os.environ["SMTP_USER"],
                os.environ["SMTP_PASS"]
            )
            server.send_message(msg)
    else:
        with smtplib.SMTP(
            host, port, timeout=20
        ) as server:
            server.starttls()
            server.login(
                os.environ["SMTP_USER"],
                os.environ["SMTP_PASS"]
            )
            server.send_message(msg)

def main():
    number, state = get_state()
    previous = state.get("status", "unknown")
    current = detect()

    print("上次状态:", previous)
    print("当前状态:", current)

    # 尚未完成商品级校准，暂不自动发送补货邮件
    if current == "possible":
        print("发现可能有货，需要校准商品规格")
        return

    if current == "unknown":
        print("无法确定库存，保留历史状态")
        return

    state["status"] = current
    state["checked_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    save_state(number, state)
    print("历史状态已保存")

if __name__ == "__main__":
    main()
