"""Production entrypoint for gunicorn."""

from giftlist.web import create_app

app = create_app()
