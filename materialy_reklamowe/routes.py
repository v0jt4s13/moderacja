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
    if request.method == "POST":
        paths_input = request.form.get("s3_paths", "").strip()
    else:
        paths_input = (request.args.get("paths") or "").strip()

    results: list[dict[str, object]] = []
    error: str | None = None
    submitted = False

    locations = [line.strip() for line in paths_input.splitlines() if line.strip()]
    if locations:
        submitted = True
        try:
            results = fetch_gallery_entries(locations)
        except Exception as exc:  # pylint: disable=broad-except
            error = str(exc)

    return render_template(
        "materialy_reklamowe/index.html",
        paths_input=paths_input,
        results=results,
        error=error,
        submitted=submitted,
    )
