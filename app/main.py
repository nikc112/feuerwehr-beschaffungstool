from flask import Blueprint, render_template, jsonify

from .models import get_vergabe_tiers, get_form_texts, get_branding

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    return render_template('index.html',
                           vergabe_tiers=get_vergabe_tiers(),
                           form_texts=get_form_texts(),
                           branding=get_branding())


@main_bp.route('/manifest.webmanifest')
def manifest():
    b = get_branding()
    return jsonify({
        'name': b['name'] + ' – Beschaffung',
        'short_name': b['name'][:24],
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'background_color': b['color_bg'],
        'theme_color': b['color_primary'],
        'icons': [
            {'src': '/static/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
            {'src': '/static/icon-180.png', 'sizes': '180x180', 'type': 'image/png'},
        ],
    })
