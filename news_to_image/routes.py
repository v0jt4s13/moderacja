import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Sequence

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from auth import login_required
from llm import ask_model_openai
from news_to_video.main import scrap_page
from markupsafe import Markup
import logging

from .config import DEFAULT_IMAGE_SIZE, DEFAULT_LANGUAGE, PROJECTS_DIR, ensure_project_paths
from .generators import GeneratedImage, ImageGenerationError, get_generator


news_to_image_bp = Blueprint(
    "news_to_image",
    __name__,
    url_prefix="/news-to-image",
    template_folder="templates/news_to_image",
    static_folder="static/news_to_image",
)

AVAILABLE_ENGINES = [
    ("dalle3", "DALL·E 3"),
    ("gemini", "Gemini Nano Banana"),
    ("midjourney", "Midjourney (extension point)"),
    ("bluewillow", "BlueWillow (extension point)"),
]

logger = logging.getLogger("news_to_image")


def _save_json(path: str, payload: Dict) -> None:
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, default=str)


def _load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def _generate_prompts(article: Dict, num_images: int) -> Sequence[str]:
    title = article.get("title", "").strip()
    summary = article.get("text", "").strip()
    source = article.get("source_url", "").strip()

    system_prompt = (
        "Jesteś dyrektorem kreatywnym w agencji prasowej. "
        "Tworzysz szczegółowe prompty dla generatorów obrazów, aby powstały fotorealistyczne ujęcia. "
        "Opisz scenę, nastrój, otoczenie, kluczowe rekwizyty oraz plan filmowy. "
        "Dodaj styl fotografii reporterskiej i parametry obiektywu."
    )
    user_prompt = (
        "Przygotuj {num} unikalnych promptów po polsku (możesz dodać krótkie akcenty angielskie, jeżeli to pomoże), "
        "każdy maksymalnie 80 słów. Prompty powinny odpowiadać treści artykułu.\n"
        "Zwróć czysty JSON – listę tekstów bez dodatkowych komentarzy.\n\n"
        f"Tytuł artykułu: {title}\n"
        f"Podsumowanie artykułu: {summary}\n"
        f"Źródło: {source or 'brak'}"
    ).format(num=num_images)

    try:
        response = ask_model_openai(system_prompt, user_prompt)
        prompts = json.loads(response)
        if isinstance(prompts, list) and len(prompts) == num_images:
            return [str(p).strip() for p in prompts]
    except Exception:
        pass

    # fallback heurystyczny
    base_prompt = (
        f"{title}. Fotorealistyczne ujęcie reporterskie, dynamiczne światło, "
        f"styl prasy. Kontekst artykułu: {summary[:400]}"
    )
    return [f"{base_prompt} – wariant {i}" for i in range(1, num_images + 1)]


def _create_manifest(
    project_id: str,
    engine: str,
    article: Dict,
    prompts: Sequence[str],
    images: List[GeneratedImage],
    status: str,
    error: str | None,
) -> Dict:
    return {
        "project_id": project_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": engine,
        "source_url": article.get("source_url"),
        "article": {
            "title": article.get("title"),
            "text": article.get("text"),
        },
        "prompt_variants": list(prompts),
        "status": status,
        "error": error,
        "outputs": [
            {
                "filename": img.filename,
                "path": os.path.relpath(img.path, PROJECTS_DIR),
                "prompt": img.prompt,
            }
            for img in images
        ],
    }


@news_to_image_bp.get("/")
@login_required(role=["admin", "redakcja", "moderator", "tester"])
def create_form() -> str:
    form_defaults = session.get("news_to_image_form") or {}
    current_engine = form_defaults.get("engine") or "dalle3"
    try:
        current_count = int(form_defaults.get("image_count", 2))
    except (TypeError, ValueError):
        current_count = 2
    form_defaults = {
        "article_url": form_defaults.get("article_url", ""),
        "image_count": current_count,
        "engine": current_engine,
    }
    return render_template(
        "news_to_image/create.html",
        engines=AVAILABLE_ENGINES,
        default_engine="dalle3",
        form_defaults=form_defaults,
    )


@news_to_image_bp.post("/generate")
@login_required(role=["admin", "redakcja", "moderator", "tester"])
def generate_images():
    logger.info("Start generate_images request")
    article_url = (request.form.get("article_url") or "").strip()
    engine = (request.form.get("engine") or "dalle3").strip().lower()
    try:
        num_images = int(request.form.get("image_count") or 2)
    except ValueError:
        num_images = 2
    num_images = max(2, min(4, num_images))
    session["news_to_image_form"] = {
        "article_url": article_url,
        "image_count": num_images,
        "engine": engine,
    }

    if not article_url:
        logger.warning("No article_url provided")
        flash("Podaj adres URL artykułu.", "danger")
        return redirect(url_for("news_to_image.create_form"))

    try:
        article_payload = scrap_page(article_url, language=DEFAULT_LANGUAGE)
        article_payload["source_url"] = article_url
        logger.info("Scraped article: title=%s length=%s", article_payload.get("title"), len(article_payload.get("text", "")))
    except Exception as exc:
        logger.exception("scrap_page failed: %s", exc)
        flash(f"Nie udało się pobrać artykułu: {exc}", "danger")
        return redirect(url_for("news_to_image.create_form"))

    prompts = _generate_prompts(article_payload, num_images)
    logger.debug("Generated prompts count=%s", len(prompts))

    project_id = datetime.utcnow().strftime("img-%Y%m%d-%H%M%S")
    paths = ensure_project_paths(project_id)
    logger.info("Using project_id=%s engine=%s", project_id, engine)

    try:
        generator = get_generator(engine, image_size=DEFAULT_IMAGE_SIZE)
        generated_images = generator.generate(prompts, paths.project_dir)
        status = "completed"
        error = None
        logger.info("Generated %d images via engine=%s", len(generated_images), engine)
    except ImageGenerationError as exc:
        status = "failed"
        generated_images = []
        error = str(exc)
        logger.error("ImageGenerationError: %s", error)
        flash(f"Błąd generowania obrazów ({engine}): {error}", "danger")
    except Exception as exc:
        status = "failed"
        generated_images = []
        error = f"Nieoczekiwany błąd: {exc}"
        logger.exception("Unexpected error during generation: %s", exc)
        flash(error, "danger")

    manifest = _create_manifest(
        project_id=project_id,
        engine=engine,
        article=article_payload,
        prompts=prompts,
        images=generated_images,
        status=status,
        error=error,
    )
    _save_json(paths.manifest_path, manifest)

    if status != "completed":
        return redirect(url_for("news_to_image.create_form"))

    project_url = url_for("news_to_image.detail_view", project_id=project_id)
    flash(
        Markup(f"Obrazy zostały wygenerowane. <a href=\"{project_url}\">Przejdź do projektu</a>"),
        "success",
    )
    return redirect(url_for("news_to_image.create_form"))


@news_to_image_bp.get("/projects/<project_id>")
@login_required(role=["admin", "redakcja", "moderator", "tester"])
def detail_view(project_id: str):
    manifest_path = os.path.join(PROJECTS_DIR, project_id, "manifest.json")
    if not os.path.exists(manifest_path):
        flash("Nie znaleziono projektu.", "warning")
        return redirect(url_for("news_to_image.create_form"))

    manifest = _load_json(manifest_path)
    for output in manifest.get("outputs", []):
        output["serve_url"] = url_for(
            "news_to_image.project_asset",
            project_id=project_id,
            filename=output.get("filename"),
        )
    return render_template("news_to_image/detail.html", manifest=manifest)


@news_to_image_bp.get("/projects/<project_id>/assets/<path:filename>")
@login_required(role=["admin", "redakcja", "moderator", "tester"])
def project_asset(project_id: str, filename: str):
    project_dir = os.path.join(PROJECTS_DIR, project_id)
    return send_from_directory(project_dir, filename, as_attachment=False)


def _list_manifests() -> List[Dict]:
    entries: List[Dict] = []
    if not os.path.isdir(PROJECTS_DIR):
        return entries
    for item in os.listdir(PROJECTS_DIR):
        project_dir = os.path.join(PROJECTS_DIR, item)
        manifest_path = os.path.join(project_dir, "manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            manifest = _load_json(manifest_path)
        except Exception:
            continue
        manifest["project_dir"] = item
        created_raw = manifest.get("created_at")
        try:
            manifest["_created_ts"] = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except Exception:
            manifest["_created_ts"] = datetime.min.replace(tzinfo=timezone.utc)
        entries.append(manifest)
    entries.sort(key=lambda m: m.get("_created_ts", datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return entries


@news_to_image_bp.get("/projects")
@login_required(role=["admin", "redakcja", "moderator", "tester"])
def projects_list():
    manifests = _list_manifests()
    return render_template(
        "news_to_image/index.html",
        projects=manifests,
    )
