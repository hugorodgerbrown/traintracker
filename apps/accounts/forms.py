"""Forms for signing in by email."""

from django import forms

from apps.accounts.sign_in import CODE_DIGITS, normalise_email


class EmailForm(forms.Form):
    """The one field sign-in and sign-up share."""

    email = forms.EmailField(max_length=254)
    next = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_email(self) -> str:
        """Lowercase the address, as every lookup expects."""
        return normalise_email(self.cleaned_data["email"])


class CodeForm(forms.Form):
    """The six-digit code from the email."""

    code = forms.RegexField(regex=rf"^\s*\d{{{CODE_DIGITS}}}\s*$", max_length=20)

    def clean_code(self) -> str:
        """Drop the spaces people paste with it."""
        return str(self.cleaned_data["code"]).strip()
