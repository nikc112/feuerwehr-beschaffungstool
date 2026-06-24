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


def _clean_header(value: str) -> str:
    """CR/LF (und Tabs) aus Header-Werten entfernen – verhindert Header-Injection."""
    return ' '.join(str(value or '').splitlines()).replace('\t', ' ').strip()


def send_email(to_email: str, to_name: str, subject: str, body: str):
    to_email = _clean_header(to_email)
    to_name = _clean_header(to_name)
    subject = _clean_header(subject)
    # Empfängeradresse muss plausibel sein (genau eine Adresse, keine Steuerzeichen)
    if not to_email or ' ' in to_email or '@' not in to_email:
        raise ValueError(f'Ungültige Empfängeradresse: {to_email!r}')

    # Verbindungsart: Microsoft 365 (Graph) oder klassisch SMTP
    if _get_smtp_setting('mail_provider', 'MAIL_PROVIDER', 'smtp') == 'm365':
        from .graph_mail import send_mail as graph_send
        graph_send(to_email, to_name, subject, body)
        return
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
