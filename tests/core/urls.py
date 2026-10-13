"""A URLconf for the core tests: one keyed JSON write to replay.

Traintrackr has no offline write of its own yet, so the idempotency and
CSRF tests post to this endpoint instead of a real feature's.
"""

import json

from django.http import HttpRequest, JsonResponse
from django.urls import path

from apps.core.decorators import login_required_json

# Each call the view runs, so a test can tell a replay from a second run.
CALLS: list[dict[str, object]] = []


@login_required_json
def write(request: HttpRequest) -> JsonResponse:
    """Record the posted body and answer 201, as a real JSON write would."""
    if request.method != "POST":
        return JsonResponse({"calls": len(CALLS)})
    body = json.loads(request.body or b"{}")
    CALLS.append(body)
    response = JsonResponse({"saved": body, "calls": len(CALLS)}, status=201)
    # A created resource's address, as a real write would give it.
    response["Location"] = f"/write/{len(CALLS)}/"
    response.set_cookie("seen", "1")
    return response


urlpatterns = [path("write/", write, name="write")]
