"""Allow one unused sign-in request per address, retiring older ones first."""

from django.db import migrations, models
from django.utils import timezone


def retire_older_requests(apps, schema_editor):
    """Mark every unused request but the newest for its address as used."""
    SignInRequest = apps.get_model("accounts", "SignInRequest")
    unused = SignInRequest.objects.filter(used_at__isnull=True)
    now = timezone.now()
    for key in unused.values_list("email_key", flat=True).distinct():
        mine = unused.filter(email_key=key)
        newest = mine.order_by("-created_at", "-pk").values_list("pk", flat=True).first()
        mine.exclude(pk=newest).update(used_at=now)


class Migration(migrations.Migration):
    """Retire duplicate unused requests, then add the constraint."""

    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.RunPython(retire_older_requests, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="signinrequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("used_at__isnull", True)),
                fields=("email_key",),
                name="one_unused_sign_in_per_address",
            ),
        ),
    ]
