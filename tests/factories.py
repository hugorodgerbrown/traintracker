"""FactoryBoy factories for every model. Always call them with ``.create()``."""

from datetime import timedelta

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import Passkey, ReviewerAccount, SignInRequest, email_key


class UserFactory(factory.django.DjangoModelFactory):
    """An account as sign-in makes one: the address's key is the username, no email, no password.

    Pass ``address`` for the email address it was made for.
    """

    class Meta:
        """Factory metadata."""

        model = get_user_model()
        django_get_or_create = ("username",)

    class Params:
        """The address the account was made for, which is not stored."""

        address = factory.Sequence(lambda n: f"user{n}@example.com")

    username = factory.LazyAttribute(lambda o: email_key(o.address))
    email = ""
    password = factory.django.Password(None)


class SignInRequestFactory(factory.django.DjangoModelFactory):
    """A live sign-in request. Use ``apps.accounts.sign_in.issue`` to get its token and code."""

    class Meta:
        """Factory metadata."""

        model = SignInRequest

    class Params:
        """The address the request was for, which is not stored."""

        address = factory.Sequence(lambda n: f"signin{n}@example.com")

    email_key = factory.LazyAttribute(lambda o: email_key(o.address))
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


class ReviewerAccountFactory(factory.django.DjangoModelFactory):
    """A reviewer account; its code is made on save."""

    class Meta:
        """Factory metadata."""

        model = ReviewerAccount

    email = factory.Sequence(lambda n: f"reviewer{n}@example.com")
