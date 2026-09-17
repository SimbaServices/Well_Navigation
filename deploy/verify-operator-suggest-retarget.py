from unittest.mock import patch

from starlette.testclient import TestClient

from app import app

rows = [
    {"number": "630591", "name": "OXY USA INC."},
    {"number": "103869", "name": "OXY USA EOR, LLC"},
]
with (
    patch("app.current_user", return_value={"id": 1, "username": "sam"}),
    patch("app.CACHE.set"),
    patch("app.REPO.search_operators", return_value=rows),
):
    response = TestClient(app).get(
        "/search",
        params={"mode": "operator", "q": "oxy", "commit": "1", "state": "tx"},
        headers={"HX-Request": "true"},
    )
print("status", response.status_code)
print("retarget", response.headers.get("HX-Retarget"))
print("suggest_lists", response.text.count('class="suggest"'))
print("ok", response.headers.get("HX-Retarget") == "#operator-suggest" and response.text.count('class="suggest"') == 1)
