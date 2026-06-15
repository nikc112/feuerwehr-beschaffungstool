import os

from app import db
from app.models import Proposal, Quote


def _seed_quote_with_eml(app, upload_dir, with_eml=True):
    with app.app_context():
        db.session.add(Proposal(nr='07/2026', bezeichnung='Test'))
        q = Quote(proposal_nr='07/2026', preis_stueck=0.0, source='email')
        if with_eml:
            raw = (b'From: lieferant@firma.de\r\nSubject: Angebot 07/2026\r\n'
                   b'To: ff@moorrege.de\r\n\r\nMit freundlichen Gruessen')
            with open(os.path.join(upload_dir, 'email_42.eml'), 'wb') as fh:
                fh.write(raw)
            q.eml_path = 'email_42.eml'
        db.session.add(q)
        db.session.commit()
        return q.id


def test_view_email_rendert_seite(app, auth_client, upload_dir):
    qid = _seed_quote_with_eml(app, upload_dir)
    r = auth_client.get(f'/api/quotes/{qid}/email')
    assert r.status_code == 200
    assert r.mimetype == 'text/html'
    body = r.get_data(as_text=True)
    assert 'Angebot 07/2026' in body
    assert 'sandbox' in body
    assert r.headers.get('Content-Security-Policy') == "script-src 'none'"


def test_download_email_liefert_eml(app, auth_client, upload_dir):
    qid = _seed_quote_with_eml(app, upload_dir)
    r = auth_client.get(f'/api/quotes/{qid}/email.eml')
    assert r.status_code == 200
    assert b'Mit freundlichen Gruessen' in r.get_data()
    assert 'attachment' in r.headers.get('Content-Disposition', '')


def test_view_email_404_ohne_eml(app, auth_client, upload_dir):
    qid = _seed_quote_with_eml(app, upload_dir, with_eml=False)
    assert auth_client.get(f'/api/quotes/{qid}/email').status_code == 404


def test_view_email_braucht_login(app, client, upload_dir):
    qid = _seed_quote_with_eml(app, upload_dir)
    assert client.get(f'/api/quotes/{qid}/email').status_code == 401
