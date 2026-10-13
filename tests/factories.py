"""FactoryBoy factories for every model. Always call them with ``.create()``."""

from datetime import timedelta

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import Passkey, SignInRequest


class UserFactory(factory.django.DjangoModelFactory):
    """An account as sign-in makes one: the email is the username, no password."""

    class Meta:
        """Factory metadata."""

        model = get_user_model()
        django_get_or_create = ("username",)

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    username = factory.SelfAttribute("email")
    password = factory.django.Password(None)


class SignInRequestFactory(factory.django.DjangoModelFactory):
    """A live sign-in request. Use ``apps.accounts.sign_in.issue`` to get its token and code."""

    class Meta:
        """Factory metadata."""

        model = SignInRequest

    email = factory.Sequence(lambda n: f"signin{n}@example.com")
    token_hash = factory.Sequence(lambda n: f"{n:064x}")
    code_hash = "0" * 64
    expires_at = factory.LazyFunction(lambda: timezone.now() + timedelta(minutes=15))


class PasskeyFactory(factory.django.DjangoModelFactory):
    """A saved passkey (its key is a placeholder: it can't verify a real ceremony)."""

    class Meta:
        """Factory metadata."""

        model = Passkey

    user = factory.SubFactory(UserFactory)
    credential_id = factory.Sequence(lambda n: f"credential-{n}")
    public_key = b"not-a-real-key"
    name = "Passkey on iPhone"
