import base64
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Sequence


@dataclass
class GeneratedImage:
    prompt: str
    filename: str
    path: str


class ImageGenerationError(RuntimeError):
    """Raised when image generation fails."""


class ImageGenerator(ABC):
    name: str

    @abstractmethod
    def generate(self, prompts: Sequence[str], project_dir: str) -> List[GeneratedImage]:
        """Generate images for provided prompts and store them within project_dir."""


class Dalle3Generator(ImageGenerator):
    name = "dalle3"

    def __init__(self, image_size: str = "1024x1024"):
        self.image_size = image_size
        self._api_key = os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise ImageGenerationError("Brak zmiennej środowiskowej OPENAI_API_KEY dla DALL·E 3.")

    def _generate_with_new_client(self, prompt: str) -> str:
        from openai import OpenAI  # type: ignore

        client = OpenAI(api_key=self._api_key)
        response = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size=self.image_size,
            quality="high",
            n=1,
        )
        if not response.data:
            raise ImageGenerationError("Puste dane odpowiedzi z DALL·E 3.")
        return response.data[0].b64_json

    def _generate_with_legacy_client(self, prompt: str) -> str:
        import openai  # type: ignore

        openai.api_key = self._api_key
        try:
            # Nowe modele są obsługiwane przez images.generate w nowszym kliencie.
            response = openai.images.generate(
                model="gpt-image-1",
                prompt=prompt,
                size=self.image_size,
                quality="high",
                n=1,
            )
        except AttributeError as exc:
            raise ImageGenerationError(
                "Zainstalowany pakiet openai nie wspiera images.generate – zaktualizuj bibliotekę."
            ) from exc
        data = getattr(response, "data", None) or response.get("data")  # type: ignore[union-attr]
        if not data:
            raise ImageGenerationError("Puste dane odpowiedzi z DALL·E 3.")
        first = data[0]
        return getattr(first, "b64_json", None) or first.get("b64_json")  # type: ignore[union-attr]

    def _generate_single(self, prompt: str) -> bytes:
        try:
            return base64.b64decode(self._generate_with_new_client(prompt))
        except ImportError:
            return base64.b64decode(self._generate_with_legacy_client(prompt))
        except ImageGenerationError:
            raise
        except Exception as exc:
            # spróbuj klienta legacy jako fallback
            try:
                return base64.b64decode(self._generate_with_legacy_client(prompt))
            except Exception:
                raise ImageGenerationError(f"Błąd generowania DALL·E 3: {exc}") from exc

    def generate(self, prompts: Sequence[str], project_dir: str) -> List[GeneratedImage]:
        generated: List[GeneratedImage] = []
        for idx, prompt in enumerate(prompts, start=1):
            raw_image = self._generate_single(prompt)
            filename = f"image_{idx:02d}.png"
            path = os.path.join(project_dir, filename)
            with open(path, "wb") as fp:
                fp.write(raw_image)
            generated.append(
                GeneratedImage(
                    prompt=prompt,
                    filename=filename,
                    path=path,
                )
            )
        return generated


class GeminiGenerator(ImageGenerator):
    name = "gemini"

    def __init__(self, image_size: str = "1024x1024"):
        api_key = os.getenv("GOOGLE_GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ImageGenerationError("Brak Google Gemini API key (GOOGLE_GEMINI_API_KEY lub GEMINI_API_KEY).")
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError as exc:
            raise ImageGenerationError(
                "Pakiet google-generativeai nie jest zainstalowany – dodaj go do środowiska."
            ) from exc

        genai.configure(api_key=api_key)
        self._genai = genai
        self.image_size = image_size
        self._model_name = os.getenv("GEMINI_IMAGE_MODEL", "imagen-3.0-generate-001")
        self._image_model = None
        self._content_model = None
        self._images_api = getattr(genai, "images", None)

        image_model_cls = getattr(genai, "ImageGenerationModel", None)
        if image_model_cls:
            try:
                self._image_model = image_model_cls(model_name=self._model_name)
            except Exception:
                self._image_model = None
        if not self._image_model:
            try:
                self._content_model = genai.GenerativeModel(self._model_name)
            except Exception as exc:
                raise ImageGenerationError(f"Nie udało się zainicjalizować modelu Gemini: {exc}") from exc

    def generate(self, prompts: Sequence[str], project_dir: str) -> List[GeneratedImage]:
        generated: List[GeneratedImage] = []
        for idx, prompt in enumerate(prompts, start=1):
            try:
                img_bytes = self._generate_image(prompt)
            except Exception as exc:
                raise ImageGenerationError(f"Błąd Gemini: {exc}") from exc

            filename = f"image_{idx:02d}.png"
            path = os.path.join(project_dir, filename)
            with open(path, "wb") as fp:
                fp.write(img_bytes)

            generated.append(GeneratedImage(prompt=prompt, filename=filename, path=path))
        return generated

    def _generate_image(self, prompt: str) -> bytes:
        if self._image_model and hasattr(self._image_model, "generate_images"):
            try:
                result = self._image_model.generate_images(
                    prompt=prompt,
                    number_images=1,
                    size=self.image_size,
                )
            except TypeError:
                result = self._image_model.generate_images(
                    prompt=prompt,
                    number_of_images=1,
                    size=self.image_size,
                )
            images = getattr(result, "images", None)
            if not images:
                raise ImageGenerationError("Gemini nie zwrócił żadnych obrazów.")
            image_obj = images[0]
            raw = getattr(image_obj, "image", None) or getattr(image_obj, "data", None)
            if raw is None:
                raise ImageGenerationError("Nieznany format zwróconego obrazu Gemini.")
            if isinstance(raw, str):
                return base64.b64decode(raw)
            return raw

        if self._images_api and hasattr(self._images_api, "generate"):
            try:
                result = self._images_api.generate(
                    model=self._model_name,
                    prompt=prompt,
                    size=self.image_size,
                )
            except TypeError:
                result = self._images_api.generate(
                    model=self._model_name,
                    prompt=prompt,
                    number_of_images=1,
                    size=self.image_size,
                )
            images = getattr(result, "images", None) or getattr(result, "data", None)
            if not images:
                raise ImageGenerationError("Gemini nie zwrócił żadnych obrazów.")
            image_obj = images[0]
            raw = getattr(image_obj, "image", None) or getattr(image_obj, "data", None)
            if raw is None and hasattr(image_obj, "as_base64"):
                raw = image_obj.as_base64()
            if raw is None:
                raise ImageGenerationError("Nieznany format zwróconego obrazu Gemini.")
            if isinstance(raw, str):
                return base64.b64decode(raw)
            return raw

        if not self._content_model:
            raise ImageGenerationError("Model Gemini nie jest dostępny.")

        try:
            result = self._content_model.generate_content(prompt)
        except TypeError:
            result = self._content_model.generate_content(
                [prompt],
            )

        img_bytes = self._extract_inline_image(result)
        if not img_bytes:
            raise ImageGenerationError("Gemini nie zwrócił danych obrazu.")
        return img_bytes

    @staticmethod
    def _extract_inline_image(result) -> bytes | None:
        candidates = getattr(result, "candidates", []) or []
        for cand in candidates:
            content = getattr(cand, "content", None) or cand
            parts = getattr(content, "parts", None) or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    data = inline.data
                    if isinstance(data, str):
                        return base64.b64decode(data)
                    return data
                data = getattr(part, "data", None)
                if data:
                    if isinstance(data, str):
                        return base64.b64decode(data)
                    return data
        return None


class PlaceholderExtensionGenerator(ImageGenerator):
    """Generator wskazujący miejsce na integrację z zewnętrznym silnikiem."""

    def __init__(self, name: str):
        self.name = name

    def generate(self, prompts: Sequence[str], project_dir: str) -> List[GeneratedImage]:
        raise ImageGenerationError(
            f"Silnik {self.name} nie posiada jeszcze integracji. "
            "Dodaj własną implementację w PlaceholderExtensionGenerator."
        )


REGISTERED_GENERATORS = {
    "dalle3": Dalle3Generator,
    "gemini": GeminiGenerator,
    "midjourney": lambda **kwargs: PlaceholderExtensionGenerator("Midjourney"),
    "bluewillow": lambda **kwargs: PlaceholderExtensionGenerator("BlueWillow"),
}


def get_generator(engine: str, image_size: str = "1024x1024") -> ImageGenerator:
    engine = (engine or "dalle3").lower()
    factory = REGISTERED_GENERATORS.get(engine)
    if not factory:
        raise ImageGenerationError(f"Nieznany silnik obrazów: {engine}")
    generator = factory(image_size=image_size) if callable(factory) else factory(image_size=image_size)  # type: ignore[call-arg]
    if not isinstance(generator, ImageGenerator):
        raise ImageGenerationError(f"Fabryka silnika {engine} zwróciła niepoprawny obiekt.")
    return generator
