from __future__ import annotations

from flask import Blueprint, render_template, request

from auth import login_required
from news_to_video.renders_engines.s3_proc import fetch_gallery_entries

materialy_reklamowe_bp = Blueprint(
    "materialy_reklamowe",
    __name__,
    url_prefix="/materialy-reklamowe",
)


@materialy_reklamowe_bp.route("/", methods=["GET", "POST"])
@login_required(role=["admin", "redakcja", "moderator", "tester"])
def index():
    """
    Widok galerii materiałów reklamowych z podanych adresów/prefiksów S3.
    """
    print("\n\t\tSTART ==> materialy_reklamowe.index()")
    # Akceptuj zarówno POST, jak i GET oraz obie nazwy parametru
    if request.method == "POST":
        paths_input = (request.form.get("s3_paths") or request.form.get("paths") or "").strip()
    else:
        paths_input = (request.args.get("s3_paths") or request.args.get("paths") or "").strip()

    # Paginacja (GET)
    try:
        page = int(request.args.get("page", 1))
    except Exception:
        page = 1
    try:
        per_page = int(request.args.get("per_page", 24))
    except Exception:
        per_page = 24

    results: list[dict[str, object]] = []
    error: str | None = None
    submitted = False

    locations = [line.strip() for line in paths_input.splitlines() if line.strip()]
    if locations:
        submitted = True
        try:
            results = fetch_gallery_entries(locations, page=page, per_page=per_page)
        except Exception as exc:  # pylint: disable=broad-except
            error = str(exc)

    return render_template(
        "materialy_reklamowe/index.html",
        paths_input=paths_input,
        results=results,
        error=error,
        submitted=submitted,
        page=page,
        per_page=per_page,
    )
