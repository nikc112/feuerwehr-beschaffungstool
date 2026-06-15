from email.message import EmailMessage

from app.email_view import parse_email, extract_headers, extract_body, render_email_page


def _raw(subject='Test-Betreff', sender='lieferant@firma.de',
         body_text='Hallo Welt', body_html=None):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = 'ff@moorrege.de'
    msg['Date'] = 'Sat, 14 Jun 2026 10:00:00 +0200'
    msg.set_content(body_text)
    if body_html is not None:
        msg.add_alternative(body_html, subtype='html')
    return msg.as_bytes()


def test_extract_headers_liest_kopfzeilen():
    msg = parse_email(_raw())
    h = extract_headers(msg)
    assert h['subject'] == 'Test-Betreff'
    assert h['from'] == 'lieferant@firma.de'
    assert h['to'] == 'ff@moorrege.de'
    assert '2026' in h['date']


def test_extract_body_bevorzugt_html():
    msg = parse_email(_raw(body_text='Nur Text',
                           body_html='<p>Schickes <b>HTML</b></p>'))
    body, is_html = extract_body(msg)
    assert is_html is True
    assert '<b>HTML</b>' in body


def test_extract_body_text_wird_escaped():
    msg = parse_email(_raw(body_text='Achtung <script>alert(1)</script>'))
    body, is_html = extract_body(msg)
    assert is_html is False
    assert '<script>' not in body
    assert '&lt;script&gt;' in body


def test_render_email_page_enthaelt_sandbox_und_download():
    msg = parse_email(_raw())
    page = render_email_page(msg, '/api/quotes/5/email.eml')
    assert 'sandbox' in page
    assert 'srcdoc=' in page
    assert '/api/quotes/5/email.eml' in page
    assert 'Test-Betreff' in page
