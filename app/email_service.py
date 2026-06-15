import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def _get_smtp_setting(db_key: str, env_key: str, default: str = '') -> str:
    try:
        from .models import Settings
        val = Settings.get(db_key)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(env_key, default)


def send_email(to_email: str, to_name: str, subject: str, body: str):
    host = _get_smtp_setting('smtp_host', 'SMTP_HOST', '')
    port = int(_get_smtp_setting('smtp_port', 'SMTP_PORT', '587'))
    user = _get_smtp_setting('smtp_user', 'SMTP_USER', '')
    password = _get_smtp_setting('smtp_password', 'SMTP_PASSWORD', '')
    from_addr = _get_smtp_setting('smtp_from', 'SMTP_FROM', '') or user
    use_tls = _get_smtp_setting('smtp_tls', 'SMTP_TLS', 'true').lower() == 'true'

    if not host:
        raise RuntimeError('SMTP_HOST nicht konfiguriert')

    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = f'{to_name} <{to_email}>'
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    if use_tls:
        server = smtplib.SMTP(host, port)
        server.starttls()
    else:
        server = smtplib.SMTP_SSL(host, port)

    if user and password:
        server.login(user, password)

    server.sendmail(from_addr, [to_email], msg.as_string())
    server.quit()
