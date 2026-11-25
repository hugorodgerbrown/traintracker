"""Deleting an account takes away every app's access to it (apps.accounts.deletion)."""

import datetime as dt

import pytest
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application, Grant, RefreshToken

from apps.accounts.deletion import delete_account
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_deleting_an_account_removes_its_tokens_and_grants() -> None:
    """Access and refresh tokens and grants go with the account; the app's registration stays."""
    user = UserFactory.create()
    other = UserFactory.create()
    app = Application.objects.create(
        name="Claude",
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="https://claude.ai/api/mcp/auth_callback",
    )
    expires = timezone.now() + dt.timedelta(hours=1)
    for owner, tag in ((user, "mine"), (other, "theirs")):
        access = AccessToken.objects.create(
            user=owner, application=app, token=f"access-{tag}", expires=expires, scope="mcp"
        )
        RefreshToken.objects.create(
            user=owner, application=app, token=f"refresh-{tag}", access_token=access
        )
        Grant.objects.create(
            user=owner,
            application=app,
            code=f"code-{tag}",
            expires=expires,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            scope="mcp",
        )

    delete_account(user)

    assert not AccessToken.objects.filter(user_id=user.pk).exists()
    assert not RefreshToken.objects.filter(user_id=user.pk).exists()
    assert not Grant.objects.filter(user_id=user.pk).exists()
    assert AccessToken.objects.filter(user=other).count() == 1
    assert RefreshToken.objects.filter(user=other).count() == 1
    assert Application.objects.filter(pk=app.pk).exists()
