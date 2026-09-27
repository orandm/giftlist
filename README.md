# The Family Christmas List

A family wishlist. Everyone adds what they'd like; everyone else claims things
to buy, alone or split. Nobody ever sees what's happening with their own
household's lists. Gerry the elf is rude about it.

## Ship it (fresh VPS with Docker)

1. **Point your domain** at the server: an `A` record for e.g. `gifts.example.ie`.
2. **Set up Google sign-in** (below) and note the client ID and secret.
3. On the server:

   ```bash
   git clone <your repo> /opt/giftlist && cd /opt/giftlist
   cp .env.example .env && nano .env        # fill in every line
   mkdir -p data && sudo chown 1000:1000 data
   docker compose up -d --build
   ```

4. Open `https://your-domain`, sign in with an `ADMIN_EMAILS` account, open
   **My list → Admin**, copy the invite link and post it in the family group chat.

Caddy fetches and renews the HTTPS certificate on its own. Ports 80 and 443
must be open.

**Updating:** `git pull && docker compose up -d --build`

**Backups** (DB only; photos are re-fetchable). Add to the host's crontab
(`crontab -e`):

```
0 3 * * * cd /opt/giftlist && docker compose exec -T app python -m giftlist.backup >> data/backup.log 2>&1
```

Copies land in `data/backups/`, newest 14 kept. To restore: stop the app,
copy a backup over `data/giftlist.db`, start it again.

**Daily digest email** (Gerry's activity summary, for anyone who's subscribed
on the Activity tab). Needs `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/
`MAIL_FROM` set in `.env`. Runs hourly rather than once a day, since
subscribers can be in different timezones -- each one only actually gets
emailed once, at 7pm in their own zone:

```
0 * * * * cd /opt/giftlist && docker compose exec -T app python -m giftlist.email_digest >> data/digest.log 2>&1
```

## Google sign-in

1. Go to console.cloud.google.com, create a project (e.g. "Family gift list").
2. **APIs & Services → OAuth consent screen**: choose *External*, fill in the
   app name and your email. Scopes: `openid`, `email`, `profile`. Then
   **Publish app** so family can sign in (these basic scopes need no review).
3. **Credentials → Create credentials → OAuth client ID** → *Web application*.
   - Authorised redirect URI: `https://your-domain/auth/callback`
   - For local testing also add `http://localhost:8000/auth/callback`
4. Copy the client ID and secret into `.env`.

## Local development

```bash
pip install -r requirements.txt
export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
DEV_LOGIN=1 ADMIN_EMAILS=you@gmail.com python -m giftlist     # http://localhost:8000
python -m unittest discover -s tests -t .
```

`DEV_LOGIN=1` adds a "pretend to be anyone" login for testing. The app refuses
to start with it on an `https://` BASE_URL.

## How it's put together

```
giftlist/
  models.py        dataclasses: User, Household, Person, Item, Claim, Notice + read models
  repository.py    Repository protocol
  sqlite_repo.py   SQLite implementation (BEGIN IMMEDIATE for check-then-write)
  access.py        who may see / edit / claim what
  accounts.py      sign-in with invites, households, admin, new season
  lists.py         items, ordering, the Everyone view
  claims.py        claims, splits, bought, notices
  activity.py      the site-wide Activity feed (household-hidden the same as Everyone)
  email_digest.py  Gerry's daily digest email -- python -m giftlist.email_digest, hourly
  gerry.py         Gerry's triggers, lines and frequency (pure functions)
  linkpreview.py   shop link -> name, photo, price (blocks private addresses)
  auth.py          Google OpenID client
  mail.py          SMTP sending (magic-sign-in link, daily digest)
  money.py         integer cents
  web/             Flask routes, CSRF, sessions, Gerry plumbing
  templates/, static/
```

### The rules

- Sign-in is Google only. New accounts need the site invite link (admin can
  reset it) or a household link (joins as co-manager). Admin emails get in
  regardless.
- Everyone page shows every household except your own. You can't see or
  claim anything on your household's lists: yours, your partner's, your kids'.
- Claims: alone or split by amount, never more than the price. Only claimers
  can mark bought, only when fully covered. Changing a share after it's bought
  is allowed; the shortfall shows and anyone can top it up. Co-splitters get a
  notice when someone changes or backs out.
- An owner lowering a price below what's claimed is allowed (blocking it
  would reveal claims); claimers see it flagged.
- Removing an item notifies its claimers, including whether it was bought.
  Removing a user (admin) deletes their list and claims; their household's
  kids go too if nobody's left to run it.
- New season (admin, type CLEAR): wipes all lists, claims, notices and photos.

### Gerry

Only reacts to actions, 1 in 3 chance, once per visit, never on someone's
first visit. Banished, he's a ghost: 2 in 3, up to 3 per visit, plus 1 in 4
page loads. "Say sorry to Gerry" undoes it. Thresholds and lines live at the
top of `gerry.py`.
