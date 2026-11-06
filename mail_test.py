
import os
import ssl
import smtplib
from email.message import EmailMessage

# 读取 GitHub Secrets
required = [
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASS",
    "MAIL_TO"
]

for key in required:
    if not os.getenv(key):
        raise RuntimeError(f"缺少邮箱配置: {key}")

# 创建测试邮件
msg = EmailMessage()

msg["Subject"] = "EC-STORE 库存监控邮件测试"
msg["From"] = os.environ["SMTP_USER"]
msg["To"] = os.environ["MAIL_TO"]

msg.set_content(
    "恭喜！\n\n"
    "GitHub 云端邮件发送测试成功。\n\n"
    "监控商品：EC-STORE 14192401\n"
    "商品链接：\n"
    "https://www.ec-store.net/sws/g/g14192401/\n\n"
    "注意：这只是测试邮件，不代表商品补货。"
)

# 通过 SSL 连接163邮箱
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

print("测试邮件已提交给 SMTP 服务器")
