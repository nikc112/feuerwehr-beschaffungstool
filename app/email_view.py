"""Reine Helfer zum Parsen und Darstellen eingegangener E-Mails.

Keine Flask-/DB-Abhängigkeit, damit unabhängig testbar.
Der Mail-Body stammt aus nicht vertrauenswürdiger Quelle und wird daher
ausschliesslich in einem sandbox-iframe ohne Skriptausführung dargestellt.
"""
import email
import email.policy
from html import escape


def parse_email(raw_bytes):
    """RFC822-Bytes in eine EmailMessage (policy=default) parsen."""
    return email.message_from_bytes(raw_bytes, policy=email.policy.default)


def extract_headers(msg):
    return {
        'from': str(msg.get('From', '')),
        'to': str(msg.get('To', '')),
        'subject': str(msg.get('Subject', '')),
        'date': str(msg.get('Date', '')),
    }


def extract_body(msg):
    """(body_html, is_html) liefern.

    Bevorzugt text/html. Sonst text/plain HTML-escaped in <pre>.
    Anhänge werden übersprungen.
    """
    html_part = None
    text_part = None
    for part in msg.walk():
        if part.is_multipart():
            continue
        cd = str(part.get('Content-Disposition') or '')
        if 'attachment' in cd:
            continue
        ct = part.get_content_type()
        if ct == 'text/html' and html_part is None:
            html_part = part
        elif ct == 'text/plain' and text_part is None:
            text_part = part

    if html_part is not None:
        return html_part.get_content(), True
    if text_part is not None:
        safe = escape(text_part.get_content())
        return ('<pre style="white-space:pre-wrap;font-family:inherit;margin:0;">'
                + safe + '</pre>'), False
    return '<p><em>(kein Textinhalt)</em></p>', False


def render_email_page(msg, eml_download_url):
    """Vollständige HTML-Ansichtsseite bauen.

    Kopfbereich mit Headern + Download-Button, darunter der Mail-Body in einem
    sandbox-iframe (kein allow-scripts) via srcdoc (attribut-escaped).
    """
    h = extract_headers(msg)
    body_html, _ = extract_body(msg)
    srcdoc = escape(body_html)          # als Attributwert
    dl = escape(eml_download_url)
    return (
        '<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">'
        '<title>' + escape(h['subject'] or 'E-Mail') + '</title><style>'
        'body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
        'margin:0;background:#f4f6f8;color:#1a2b3c;}'
        '.hdr{background:#fff;border-bottom:1px solid #d9e1e8;padding:14px 18px;}'
        '.hdr h1{font-size:16px;margin:0 0 8px;}'
        '.hdr .row{font-size:13px;color:#52606d;margin:2px 0;}'
        '.hdr .row b{color:#1a2b3c;font-weight:600;}'
        '.dl{display:inline-block;margin-top:10px;padding:6px 14px;background:#185fa5;'
        'color:#fff;text-decoration:none;border-radius:6px;font-size:13px;}'
        'iframe{border:0;width:100%;height:calc(100vh - 150px);background:#fff;}'
        '</style></head><body><div class="hdr">'
        '<h1>' + escape(h['subject'] or '(kein Betreff)') + '</h1>'
        '<div class="row"><b>Von:</b> ' + escape(h['from']) + '</div>'
        '<div class="row"><b>An:</b> ' + escape(h['to']) + '</div>'
        '<div class="row"><b>Datum:</b> ' + escape(h['date']) + '</div>'
        '<a class="dl" href="' + dl + '">Als .eml herunterladen</a>'
        '</div>'
        '<iframe sandbox srcdoc="' + srcdoc + '"></iframe>'
        '</body></html>'
    )
