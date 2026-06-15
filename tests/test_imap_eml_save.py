import os

from app.imap_worker import _save_raw_email


def test_save_raw_email_schreibt_datei(tmp_path):
    raw = b'From: a@b.de\r\nSubject: Test\r\n\r\nHallo'
    name = _save_raw_email(str(tmp_path), raw, 1700000000)
    assert name == 'email_1700000000.eml'
    written = (tmp_path / name).read_bytes()
    assert written == raw
