"""Replace every stored email address with its key (apps.accounts.models.email_key).

Accounts: the username becomes the key of the address (the email field, or
the username where it is an address) and the email field is cleared.
Sign-in requests: ``email`` becomes ``email_key``.

The key is computed here rather than imported, so this migration keeps
working if the app's code changes. Reversing it restores the schema but
not the addresses: they are gone, which is the point.
"""

from django.conf import settings
from django.db import migrations, models
from django.utils.crypto import salted_hmac

# Must match apps.accounts.models.EMAIL_KEY_SALT.
EMAIL_KEY_SALT = "accounts.email"


def _key(email: str) -> str:
    return salted_hmac(EMAIL_KEY_SALT, email.strip().lower(), algorithm="sha256").hexdigest()


def key_accounts(apps, schema_editor):
    """Give each account with an address its key as username, and forget the address."""
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)
    taken = set(user_model.objects.values_list("username", flat=True))
    # Accounts made by signing in (username is the address) first: sign-in
    # found them first, so they keep the key if a createsuperuser account
    # shares the address.
    users = sorted(user_model.objects.all(), key=lambda u: ("@" not in u.username, u.pk))
    for user in users:
        address = user.username if "@" in user.username else user.email
        if not address:
            continue
        key = _key(address)
        if key != user.username and key in taken:
            # Another account already has this address's key. Keep this one's
            # username and only forget the address.
            user.email = ""
            user.save(update_fields=["email"])
            continue
        taken.discard(user.username)
        taken.add(key)
        user.username = key
        user.email = ""
        user.save(update_fields=["username", "email"])


def key_sign_in_requests(apps, schema_editor):
    """Key each pending sign-in request by its address's key."""
    sign_in_request = apps.get_model("accounts", "SignInRequest")
    for request in sign_in_request.objects.all():
        request.email_key = _key(request.email)
        request.save(update_fields=["email_key"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="signinrequest",
            name="email_key",
            field=models.CharField(db_index=True, default="", max_length=64),
            preserve_default=False,
        ),
        migrations.RunPython(key_sign_in_requests, migrations.RunPython.noop),
        migrations.RemoveField(model_name="signinrequest", name="email"),
        migrations.RunPython(key_accounts, migrations.RunPython.noop),
    ]
