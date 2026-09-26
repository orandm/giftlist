"""Local dev server: DEV_LOGIN=1 SECRET_KEY=... python -m giftlist"""

from .web import create_app

create_app().run(host="0.0.0.0", port=8000, debug=True)
