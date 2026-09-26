"""Google sign-in (OpenID Connect, authorisation-code flow).

The ID is fetched server-to-server from Google's userinfo endpoint over TLS
with the access token we just exchanged, so no token signature checking is
needed here. `state` (checked by the web layer) stops login CSRF.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

import requests


@dataclass(slots=True, frozen=True)
class Identity:
    sub: str
    email: str
    name: str


class AuthError(Exception):
    pass


class IdentityProvider(Protocol):
    def authorize_url(self, redirect_uri: str, state: str) -> str: ...
    def identity(self, code: str, redirect_uri: str) -> Identity: ...


class GoogleProvider:
    AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"
    USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"

    def __init__(self, client_id: str, client_secret: str, session: requests.Session | None = None) -> None:
        self._id = client_id
        self._secret = client_secret
        self._http = session or requests.Session()

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        return self.AUTHORIZE + "?" + urlencode({
            "client_id": self._id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        })

    def identity(self, code: str, redirect_uri: str) -> Identity:
        try:
            tok = self._http.post(self.TOKEN, timeout=10, data={
                "code": code, "client_id": self._id, "client_secret": self._secret,
                "redirect_uri": redirect_uri, "grant_type": "authorization_code",
            })
            tok.raise_for_status()
            access = tok.json()["access_token"]
            info = self._http.get(self.USERINFO, timeout=10, headers={"Authorization": f"Bearer {access}"})
            info.raise_for_status()
            data = info.json()
        except (requests.RequestException, KeyError, ValueError) as e:
            raise AuthError("Google didn't play ball. Try again.") from e
        if not data.get("email_verified"):
            raise AuthError("Your Google email isn't verified.")
        name = data.get("given_name") or data.get("name") or data["email"].split("@")[0]
        return Identity(sub=str(data["sub"]), email=data["email"], name=name)
