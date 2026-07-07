import os

from app.imap_worker import _save_raw_email


def test_save_raw_email_schreibt_datei(tmp_path):
    raw = b'From: a@b.de\r\nSubject: Test\r\n\r\nHallo'
    name = _save_raw_email(str(tmp_path), raw, 1700000000)
    # Format: email_<ts>_<8-hex>.eml (Zufallssuffix gegen Namenskollisionen)
    assert name.startswith('email_1700000000_') and name.endswith('.eml')
    written = (tmp_path / name).read_bytes()
    assert written == raw


def test_save_raw_email_eindeutig_bei_gleicher_sekunde(tmp_path):
    raw = b'x'
    n1 = _save_raw_email(str(tmp_path), raw, 1700000000)
    n2 = _save_raw_email(str(tmp_path), raw, 1700000000)
    assert n1 != n2                       # zwei Mails derselben Sekunde -> zwei Dateien
