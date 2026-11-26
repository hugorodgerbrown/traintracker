"""Deleting an account, by the person who holds it (/app/account/delete/).

Deletion is final: Titan apps keep no backups. One call removes the
account and everything it owns:

- every row with a foreign key to the user and ``on_delete=CASCADE``:
  passkeys, the app's own records and, with MCP, the OAuth tokens and grants
  that let an app such as Claude act for the account;
- the account's sign-in requests, which are matched by the address's key
  rather than a foreign key;
- the outbox's stored replies (``IdempotencyRecord``), which hold what the
  account wrote and are matched by its pk as a string;
- whatever each hook named in ``ACCOUNTS_DELETE_HOOKS`` removes. A hook is
  for data the cascade can't reach, such as rows keyed by a hash of the
  account; an app whose models all point at the user needs none.

It all happens in one transaction, so a failing hook leaves the account as
it was. The view signs the person out afterwards.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils.module_loading import import_string

from apps.accounts.models import SignInRequest, email_key
from apps.core.models import IdempotencyRecord

logger = logging.getLogger(__name__)


def sign_in_key(user: Any) -> str:
    """Return the key the account's sign-in requests carry.

    The username is that key, unless the app keeps addresses, when it is the
    address itself.
    """
    username = user.get_username()
    return email_key(username) if settings.ACCOUNTS_KEEP_EMAIL else username


def delete_account(user: Any) -> None:
    """Delete ``user`` and everything it owns, at once and for good."""
    pk = user.pk
    with transaction.atomic():
        for path in settings.ACCOUNTS_DELETE_HOOKS:
            import_string(path)(user)
        SignInRequest.objects.filter(email_key=sign_in_key(user)).delete()
        IdempotencyRecord.objects.filter(principal=str(pk)).delete()
        user.delete()
    logger.info("accounts.deleted user=%s", pk)
