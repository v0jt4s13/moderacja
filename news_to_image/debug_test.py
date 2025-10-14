import logging
import os

import google.generativeai as genai


def main() -> None:
    logging.basicConfig(level=logging.DEBUG)

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_GEMINI_API_KEY")
    print("API key present:", bool(api_key))
    if not api_key:
        raise SystemExit("Brak zmiennej GEMINI_API_KEY / GOOGLE_GEMINI_API_KEY.")

    genai.configure(api_key=api_key)

    if not hasattr(genai, "ImageGenerationModel"):
        raise SystemExit(
            "Zainstalowana wersja google-generativeai nie posiada ImageGenerationModel."
        )

    model = genai.ImageGenerationModel(model_name="models/imagegeneration")
    prompt = "Krótki testowy prompt do wygenerowania zdjęcia redakcyjnego."
    print("calling generate_images…")

    try:
        result = model.generate_images(
            prompt=prompt,
            number_of_images=1,
            size=os.getenv("NEWS_TO_IMAGE_DEBUG_SIZE", "1024x1024"),
        )
        images = getattr(result, "images", []) or getattr(result, "data", [])
        print("images returned:", len(images))
    except Exception as exc:
        logging.exception("generate_images failed: %s", exc)


if __name__ == "__main__":
    main()
