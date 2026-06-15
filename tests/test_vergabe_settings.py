def _valid_tiers(t1=50000, t2=150000, t3=221000, label0='Direktauftrag'):
    return [
        {'key': 'direkt', 'label': label0, 'max': t1, 'info': 'x'},
        {'key': 'beschraenkt', 'label': 'Beschränkte Ausschreibung', 'max': t2, 'info': 'y'},
        {'key': 'oeffentlich', 'label': 'Öffentliche Ausschreibung', 'max': t3, 'info': 'z'},
        {'key': 'europa', 'label': 'Europaweite Ausschreibung', 'max': None, 'info': 'w'},
    ]


def test_template_injects_thresholds(app, client):
    r = client.get('/')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'VERGABE_TIERS' in body
    assert '50000' in body  # Default-Grenze im injizierten JSON


def test_get_settings_returns_default_tiers(app, auth_client):
    r = auth_client.get('/api/settings')
    assert r.status_code == 200
    tiers = r.get_json()['vergabe_tiers']
    assert len(tiers) == 4
    assert [t['key'] for t in tiers] == ['direkt', 'beschraenkt', 'oeffentlich', 'europa']
    assert tiers[0]['max'] == 50000
    assert tiers[-1]['max'] is None


def test_put_custom_tiers_persists(app, auth_client):
    custom = _valid_tiers(t1=60000, label0='Kleinauftrag')
    r = auth_client.put('/api/settings', json={'vergabe_tiers': custom})
    assert r.status_code == 200
    tiers = auth_client.get('/api/settings').get_json()['vergabe_tiers']
    assert tiers[0]['label'] == 'Kleinauftrag'
    assert tiers[0]['max'] == 60000


def test_put_non_ascending_rejected(app, auth_client):
    bad = _valid_tiers(t1=200000, t2=150000)  # nicht aufsteigend
    r = auth_client.put('/api/settings', json={'vergabe_tiers': bad})
    assert r.status_code == 400


def test_put_empty_label_rejected(app, auth_client):
    bad = _valid_tiers(label0='   ')
    r = auth_client.put('/api/settings', json={'vergabe_tiers': bad})
    assert r.status_code == 400


def test_reset_restores_defaults(app, auth_client):
    auth_client.put('/api/settings', json={'vergabe_tiers': _valid_tiers(t1=99000)})
    assert auth_client.get('/api/settings').get_json()['vergabe_tiers'][0]['max'] == 99000
    r = auth_client.put('/api/settings', json={'vergabe_tiers': None})
    assert r.status_code == 200
    assert auth_client.get('/api/settings').get_json()['vergabe_tiers'][0]['max'] == 50000


def test_other_settings_still_work_alongside(app, auth_client):
    # vergabe_tiers + normaler Key in einem PUT
    r = auth_client.put('/api/settings', json={'vergabe_tiers': _valid_tiers(), 'smtp_host': 'mail.example.com'})
    assert r.status_code == 200
    assert auth_client.get('/api/settings').get_json()['smtp_host'] == 'mail.example.com'
