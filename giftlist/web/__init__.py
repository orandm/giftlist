"""Flask app factory."""

from __future__ import annotations

import os
from datetime import timedelta

from flask import Flask

from .. import gerry
from ..auth import GoogleProvider, IdentityProvider
from ..config import Config
from ..clock import local_today
from ..money import format_amount, to_input
from ..sqlite_repo import connect, init_db
from . import routes_admin, routes_auth, routes_pages, support


def create_app(config: Config | None = None, provider: IdentityProvider | None = None) -> Flask:
    config = config or Config.from_env()
    os.makedirs(config.data_dir, exist_ok=True)
    os.makedirs(config.images_dir, exist_ok=True)
    conn = connect(config.db_path)
    init_db(conn)
    conn.close()

    pkg = os.path.dirname(os.path.dirname(__file__))
    app = Flask(__name__, template_folder=os.path.join(pkg, "templates"), static_folder=os.path.join(pkg, "static"))
    app.config.update(
        SECRET_KEY=config.secret_key,
        GIFTLIST=config,
        GIFTLIST_PROVIDER=provider or GoogleProvider(config.google_client_id, config.google_client_secret),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.secure_cookies,
        PERMANENT_SESSION_LIFETIME=timedelta(days=90),
        MAX_CONTENT_LENGTH=1_000_000,
    )

    app.before_request(support.check_csrf)
    app.before_request(support.start_request)
    app.after_request(support.finish_request)
    app.teardown_appcontext(support.close_repo)

    currency = config.currency
    app.jinja_env.filters["money"] = lambda minor: format_amount(minor, currency)
    app.jinja_env.globals["csrf_token"] = support.csrf_token
    app.jinja_env.globals["to_input"] = to_input
    app.jinja_env.globals["gerry_unclaimed_line"] = lambda: gerry.unclaimed_line(support.rng())
    app.jinja_env.globals["gerry_countdown"] = lambda: gerry.countdown(local_today(), support.rng())
    app.context_processor(lambda: {"season_year": local_today().year})

    app.register_blueprint(routes_auth.bp)
    app.register_blueprint(routes_pages.bp)
    app.register_blueprint(routes_admin.bp)
    return app
