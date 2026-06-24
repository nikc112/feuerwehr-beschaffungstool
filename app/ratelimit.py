"""Schlanker, dependency-freier In-Memory-Rate-Limiter (pro Prozess).

Genügt für ein kleines, internes Tool, um Brute-Force (Login) und
Spam/Mail-Verstärkung (öffentliches Einreichen) abzubremsen. Hinter einem
Reverse-Proxy sollte TRUSTED_PROXIES gesetzt sein, damit die echte Client-IP
(statt der Proxy-IP) als Schlüssel dient.
"""
import time
import threading
from collections import defaultdict, deque
from functools import wraps

from flask import request, jsonify, current_app

_buckets = defaultdict(deque)
_lock = threading.Lock()


def _allowed(key, max_calls, per_seconds, now=None):
    """(erlaubt: bool, retry_after_sek: int). Sliding-Window pro key."""
    now = now if now is not None else time.time()
    with _lock:
        dq = _buckets[key]
        cutoff = now - per_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= max_calls:
            retry = int(dq[0] + per_seconds - now) + 1
            return False, max(1, retry)
        dq.append(now)
        if not dq:
            _buckets.pop(key, None)
        return True, 0


def rate_limit(max_calls, per_seconds, prefix=''):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # In Tests deaktiviert, sofern nicht explizit erzwungen.
            if current_app.config.get('TESTING') and not current_app.config.get('RATELIMIT_FORCE'):
                return f(*args, **kwargs)
            key = f'{prefix}:{request.remote_addr or "unknown"}'
            ok, retry = _allowed(key, max_calls, per_seconds)
            if not ok:
                resp = jsonify({'error': 'Zu viele Anfragen. Bitte später erneut versuchen.'})
                resp.status_code = 429
                resp.headers['Retry-After'] = str(retry)
                return resp
            return f(*args, **kwargs)
        return wrapper
    return decorator
