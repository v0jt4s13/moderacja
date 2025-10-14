import json
import logging
# from logging_config import news_to_images_logger
import os
import sys
from typing import Iterable


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _get_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_GEMINI_API_KEY")


def _validate_api_key(logger: logging.Logger, api_key: str) -> None:
    stripped = api_key.strip()
    if stripped != api_key:
        logger.warning(
            "Klucz API zawiera wiodące lub końcowe białe znaki. Rozważ użycie wartości bez spacji/nowych linii."
        )
        api_key = stripped
    bad_chars = [ch for ch in api_key if ord(ch) < 32 or ord(ch) > 126]
    if bad_chars:
        logger.warning(
            "Klucz API zawiera nietypowe znaki (np. znak nowej linii)."
        )
    if len(api_key) < 20:
        logger.warning(
            "Klucz API wygląda na bardzo krótki – upewnij się, że wkleiłeś pełną wartość z AI Studio."
        )


def _format_methods(methods: Iterable[str] | None) -> str:
    if not methods:
        return "-"
    return ", ".join(str(m) for m in methods)


def main() -> int:
    log_level = os.getenv("LOG_LEVEL", "INFO")
    _setup_logging(log_level)
    logger = logging.getLogger("news_to_image.debug_gemini")

    api_key = _get_api_key()
    if not api_key:
        logger.error(
            "Brak klucza API. Ustaw GEMINI_API_KEY lub GOOGLE_GEMINI_API_KEY w środowisku."
        )
        return 2
    _validate_api_key(logger, api_key)

    try:
        import google.generativeai as genai  # type: ignore
    except ImportError as exc:
        logger.exception(
            "Pakiet google-generativeai nie jest dostępny w aktywnym środowisku."
        )
        return 3

    genai.configure(api_key=api_key)
    logger.info("google-generativeai version: %s", getattr(genai, "__version__", "?"))

    try:
        models = list(genai.list_models())
    except Exception as exc:
        logger.exception("Nie udało się pobrać listy modeli: %s", exc)
        return 4

    if not models:
        logger.warning("Brak modeli zwróconych przez list_models().")
        return 1

    logger.info("Dostępne modele (%d):", len(models))
    for model in models:
        name = getattr(model, "name", "<unknown>")
        methods = getattr(model, "generation_methods", None) or getattr(
            model, "supported_generation_methods", None
        )
        extra = {
            "available": getattr(model, "available", ""),
            "display_name": getattr(model, "display_name", ""),
        }
        print(
            f"- {name}: methods=[{_format_methods(methods)}] extras={json.dumps(extra, ensure_ascii=False)}"
        )

    # Prosty smoke-test dla pierwszego modelu z generateImages
    for model in models:
        methods = getattr(model, "generation_methods", None) or getattr(
            model, "supported_generation_methods", None
        )
        methods = [str(m).lower() for m in (methods or [])]
        if any("image" in m for m in methods) or "generateimages" in methods:
            target_model = getattr(model, "name", "")
            if not target_model:
                continue
            logger.info("Próbuję wywołać generate_content na modelu %s", target_model)
            sample_prompt = os.getenv(
                "NEWS_TO_IMAGE_DEBUG_PROMPT",
                "Krótki testowy prompt do generowania obrazu prasy.",
            )
            try:
                client = genai.GenerativeModel(target_model)
                result = client.generate_content(sample_prompt)
                has_image = any(
                    getattr(part, "inline_data", None)
                    for cand in getattr(result, "candidates", []) or []
                    for part in getattr(getattr(cand, "content", cand), "parts", [])
                )
                logger.info(
                    "generate_content wynik: candidates=%d, zawiera_inline=%s",
                    len(getattr(result, "candidates", []) or []),
                    has_image,
                )
                break
            except Exception as exc:
                logger.error(
                    "Wywołanie generate_content na %s zakończyło się błędem: %s",
                    target_model,
                    exc,
                )
                break

    return 0


if __name__ == "__main__":
    sys.exit(main())
