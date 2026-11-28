"""The form on the phone: the code the TV shows, and the board to put on it."""

from __future__ import annotations

from django import forms

from apps.pairing.services import CODE_LENGTH, normalise_code
from apps.rail import stations

CODE_HELP = f"A code is {CODE_LENGTH} letters and numbers, like K7Q-4MD."
NO_BOARD = "Choose a board, or type a station."


class PairForm(forms.Form):
    """The code (with or without its dash), and a board: a saved one's address or a station.

    A typed station wins over a chosen board, so typing one is enough. Which
    board they name is decided in the view, which can ask "which station?".
    """

    code = forms.CharField(max_length=20, strip=True)
    board = forms.CharField(max_length=64, required=False, strip=True)
    station = forms.CharField(max_length=stations.MAX_QUERY, required=False, strip=True)

    def clean_code(self) -> str:
        """Return the bare code, or say what a code looks like."""
        code = normalise_code(self.cleaned_data["code"])
        if len(code) != CODE_LENGTH:
            raise forms.ValidationError(CODE_HELP)
        return code

    def clean(self) -> dict[str, str]:
        """Ask for a board when neither a board nor a station was given."""
        data = super().clean() or {}
        if not data.get("board") and not data.get("station"):
            self.add_error("station", NO_BOARD)
        return data
