import imaplib
import os
import smtplib
import ssl

user = os.environ.get("WELLNAV_SMTP_USER", "")
password = os.environ.get("WELLNAV_SMTP_PASSWORD", "")
print("user", user, "pwlen", len(password))

for name in (user, user.split("@")[0] if "@" in user else ""):
    if not name:
        continue
    try:
        mailbox = imaplib.IMAP4_SSL("mail.simba.services", 993, timeout=12)
        print("IMAP_LOGIN", name, mailbox.login(name, password))
        mailbox.logout()
    except Exception as exc:
        print("IMAP_FAIL", name, type(exc).__name__, exc)

try:
    smtp = smtplib.SMTP("mail.simba.services", 587, timeout=12)
    code, resp = smtp.ehlo()
    print("EHLO", code, resp)
    print("AUTH", smtp.esmtp_features.get("auth"))
    smtp.starttls(context=ssl.create_default_context())
    smtp.ehlo()
    print("AUTH_TLS", smtp.esmtp_features.get("auth"))
    try:
        print("SMTP_LOGIN", smtp.login(user, password))
    except Exception as exc:
        print("SMTP_LOGIN_FAIL", type(exc).__name__, exc)
    smtp.quit()
except Exception as exc:
    print("SMTP_FAIL", type(exc).__name__, exc)
