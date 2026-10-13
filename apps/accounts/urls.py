"""URLs for signing in and out, and the account page (see views.py)."""

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("signin/", views.sign_in_view, name="sign_in"),
    path("signin/code/", views.sign_in_code, name="sign_in_code"),
    path("signin/link/<str:token>/", views.sign_in_link, name="sign_in_link"),
    path("signin/passkey/options/", views.passkey_sign_in_options, name="passkey_sign_in_options"),
    path("signin/passkey/", views.passkey_sign_in, name="passkey_sign_in"),
    path("signout/", views.sign_out, name="sign_out"),
    path("app/account/", views.account, name="account"),
    path(
        "app/account/passkeys/options/",
        views.passkey_register_options,
        name="passkey_register_options",
    ),
    path("app/account/passkeys/", views.passkey_register, name="passkey_register"),
    path("app/account/passkeys/<uuid:uuid>/delete/", views.passkey_delete, name="passkey_delete"),
]
