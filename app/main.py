from flask import Blueprint, render_template

from .models import get_vergabe_tiers

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    return render_template('index.html', vergabe_tiers=get_vergabe_tiers())
