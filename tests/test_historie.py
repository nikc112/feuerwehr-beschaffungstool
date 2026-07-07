import io
import os


def _set(app, **kv):
    from app import db
    from app.models import Settings
    with app.app_context():
        for k, v in kv.items():
            Settings.set(k, v)
        db.session.commit()


def _mk_proposal(app, nr='77/2026', status='approved'):
    from app import db
    from app.models import Proposal
    with app.app_context():
        p = Proposal(nr=nr, bezeichnung='Testgerät', status=status)
        db.session.add(p)
        db.session.commit()
    return nr


def _mk_supplier(app, name='Acme GmbH', is_test=False):
    from app import db
    from app.models import Supplier
    with app.app_context():
        s = Supplier(name=name, email='a@acme.de', is_test=is_test)
        db.session.add(s)
        db.session.commit()
        return s.id


def _get(auth_client, nr):
    return [x for x in auth_client.get('/api/proposals?status=approved').get_json()
            if x['nr'] == nr][0]


# ── POST /beschaffung ───────────────────────────────────────────────────────────

def test_beschaffung_stores_fields_and_ablauf(app, auth_client):
    nr = _mk_proposal(app)
    sid = _mk_supplier(app)
    r = auth_client.post(f'/api/proposals/{nr}/beschaffung',
                         json={'supplier_id': sid, 'rechnungsbetrag': '7490.50'})
    assert r.status_code == 200
    d = r.get_json()
    assert d['beschafft_lieferant'] == 'Acme GmbH'      # Name-Snapshot
    assert d['beschafft_supplier_id'] == sid
    assert d['rechnungsbetrag'] == 7490.50
    assert d['beschafft_am']                            # gesetzt
    assert 'Beschafft' in d['ablauf']


def test_beschaffung_required_blocks_empty(app, auth_client):
    _set(app, beschaffung_required='true')
    nr = _mk_proposal(app)
    sid = _mk_supplier(app)
    assert auth_client.post(f'/api/proposals/{nr}/beschaffung', json={}).status_code == 400
    r = auth_client.post(f'/api/proposals/{nr}/beschaffung',
                         json={'supplier_id': sid, 'rechnungsbetrag': 100})
    assert r.status_code == 200


def test_beschaffung_skip_allowed_when_not_required(app, auth_client):
    nr = _mk_proposal(app)
    r = auth_client.post(f'/api/proposals/{nr}/beschaffung', json={})
    assert r.status_code == 200
    d = r.get_json()
    assert 'Beschafft' in d['ablauf']
    assert d['rechnungsbetrag'] is None
    assert d['beschafft_lieferant'] == ''


def test_beschaffung_pdf_upload_and_replace(app, auth_client):
    nr = _mk_proposal(app)
    upload_dir = app.config['UPLOAD_FOLDER']

    def post_pdf(name, content):
        return auth_client.post(f'/api/proposals/{nr}/beschaffung',
                                data={'pdf': (io.BytesIO(content), name, 'application/pdf')},
                                content_type='multipart/form-data')

    r = post_pdf('rechnung-alt.pdf', b'%PDF-1.4 alt')
    assert r.status_code == 200
    first = r.get_json()['rechnung_filepath']
    assert first.startswith('rechnung_') and os.path.exists(os.path.join(upload_dir, first))

    r = post_pdf('rechnung-neu.pdf', b'%PDF-1.4 neu')
    second = r.get_json()['rechnung_filepath']
    assert second != first
    assert not os.path.exists(os.path.join(upload_dir, first))   # alte Datei ersetzt
    assert os.path.exists(os.path.join(upload_dir, second))


def test_beschaffung_rejects_test_and_unknown_supplier(app, auth_client):
    nr = _mk_proposal(app)
    test_sid = _mk_supplier(app, name='Testfirma', is_test=True)
    assert auth_client.post(f'/api/proposals/{nr}/beschaffung',
                            json={'supplier_id': test_sid}).status_code == 400
    assert auth_client.post(f'/api/proposals/{nr}/beschaffung',
                            json={'supplier_id': 99999}).status_code == 404


# ── Ablauf-Lifecycle über PUT ───────────────────────────────────────────────────

def test_put_ablauf_remove_clears_beschafft_am_keeps_data(app, auth_client):
    nr = _mk_proposal(app)
    sid = _mk_supplier(app)
    auth_client.post(f'/api/proposals/{nr}/beschaffung',
                     json={'supplier_id': sid, 'rechnungsbetrag': 500})
    # "Beschafft" entfernen -> zurück in die Investitionsliste
    r = auth_client.put(f'/api/proposals/{nr}', json={'ablauf': ['Markterkundung']})
    d = r.get_json()
    assert d['beschafft_am'] is None
    assert d['beschafft_lieferant'] == 'Acme GmbH'      # Daten bleiben erhalten
    assert d['rechnungsbetrag'] == 500
    # erneut setzen (keine Pflicht aktiv) -> beschafft_am neu
    d = auth_client.put(f'/api/proposals/{nr}',
                        json={'ablauf': ['Markterkundung', 'Beschafft']}).get_json()
    assert d['beschafft_am']


def test_put_add_beschafft_blocked_when_required_and_no_data(app, auth_client):
    _set(app, beschaffung_required='true')
    nr = _mk_proposal(app)
    r = auth_client.put(f'/api/proposals/{nr}', json={'ablauf': ['Beschafft']})
    assert r.status_code == 400


# ── Zugriff & Settings ──────────────────────────────────────────────────────────

def test_betrachter_can_download_invoice_of_approved(app, client, auth_client):
    from app import db
    from app.models import User
    nr = _mk_proposal(app)
    r = auth_client.post(f'/api/proposals/{nr}/beschaffung',
                         data={'pdf': (io.BytesIO(b'%PDF-1.4 x'), 'r.pdf', 'application/pdf')},
                         content_type='multipart/form-data')
    path = r.get_json()['rechnung_filepath']
    with app.app_context():
        u = User(username='bet', role='betrachter')
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()
    client.post('/api/auth/login', json={'username': 'bet', 'password': 'secret123'})
    assert client.get('/api/uploads/' + path).status_code == 200


def test_settings_key_persists(app, auth_client):
    auth_client.put('/api/settings', json={'beschaffung_required': 'true'})
    assert auth_client.get('/api/settings').get_json()['beschaffung_required'] == 'true'


def test_index_exposes_beschaffung_required_flag(app, client):
    _set(app, beschaffung_required='true')
    body = client.get('/').get_data(as_text=True)
    assert 'window.BESCHAFFUNG_REQUIRED = true' in body
