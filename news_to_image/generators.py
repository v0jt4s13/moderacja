import base64
import os
import logging
import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence


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
        self.logger = logging.getLogger("news_to_image.gemini")
        api_key = os.getenv("GOOGLE_GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ImageGenerationError("Brak Google Gemini API key (GOOGLE_GEMINI_API_KEY lub GEMINI_API_KEY).")
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError as exc:
            raise ImageGenerationError(
                "Pakiet google-generativeai nie jest zainstalowany - dodaj go do środowiska."
            ) from exc

        self._api_key = api_key
        genai.configure(api_key=api_key)
        self._genai = genai
        self.image_size = image_size
        try:
            from google.generativeai import images as genai_images  # type: ignore
        except ImportError:
            genai_images = None

        configured = os.getenv("GEMINI_IMAGE_MODEL")
        base_candidates = [
            "models/imagegeneration",
            "models/imagen-3.0-generate-001",
            "imagen-3.0-generate-001",
            "models/imagen-3.0-latest",
        ]
        candidate_list: List[str] = []
        if configured:
            candidate_list.append(configured)
        candidate_list.extend(base_candidates)
        self._model_methods: Dict[str, List[str]] = {}
        candidate_list.extend(self._discover_available_models())
        seen = set()
        self._model_candidates: List[str] = []
        for cand in candidate_list:
            if cand and cand not in seen:
                seen.add(cand)
                self._model_candidates.append(cand)
        if not self._model_candidates:
            raise ImageGenerationError("Brak skonfigurowanych modeli Gemini do generowania obrazów.")
        self.logger.info("Gemini model candidates: %s", self._model_candidates)

        self._images_api = getattr(genai, "images", None)
        self._images_module = genai_images
        self._generate_images_func = getattr(genai, "generate_images", None)
        self._image_model_cls = getattr(genai, "ImageGenerationModel", None)
        self._image_models: Dict[str, Optional[object]] = {}
        self._content_models: Dict[str, Optional[object]] = {}
        self._active_model: Optional[str] = None

    def _discover_available_models(self) -> List[str]:
        models: List[str] = []
        try:
            all_models = list(self._genai.list_models())
        except Exception as exc:
            self.logger.warning("Gemini list_models failed: %s", exc)
            return models
        for model in all_models:
            name = getattr(model, "name", "")
            if not name:
                continue
            methods = (
                getattr(model, "generation_methods", None)
                or getattr(model, "supported_generation_methods", None)
                or []
            )
            methods = [str(m) for m in methods]
            self._model_methods[name] = methods
            methods_lower = [str(m).lower() for m in methods]
            if any("image" in m for m in methods_lower) or name.endswith("imagegeneration") or "imagen" in name.lower():
                models.append(name)
        if models:
            self.logger.info(
                "Gemini available models: %s",
                ", ".join(f"{m} (methods={self._model_methods.get(m)})" for m in models),
            )
        else:
            self.logger.info("Gemini list_models returned no image-capable models.")
        return models

    def generate(self, prompts: Sequence[str], project_dir: str) -> List[GeneratedImage]:
        generated: List[GeneratedImage] = []
        for idx, prompt in enumerate(prompts, start=1):
            try:
                self.logger.info("Gemini rendering prompt %d/%d", idx, len(prompts))
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
        candidates = self._ordered_candidates()
        last_error: Optional[Exception] = None
        all_not_found = True
        for model_name in candidates:
            try:
                self.logger.debug("Trying Gemini model: %s", model_name)
                img = self._generate_with_model(model_name, prompt)
                self._active_model = model_name
                self.logger.info("Gemini model %s succeeded", model_name)
                return img
            except ImageGenerationError as exc:
                self.logger.warning("Gemini model %s failed: %s", model_name, exc)
                last_error = exc
                if not self._is_not_found_error(exc):
                    all_not_found = False
                continue
        if all_not_found and last_error:
            raise ImageGenerationError(
                "Google Gemini nie udostępnia modeli obrazu dla tego klucza API (404). "
                "Wejdź do Google AI Studio i włącz Imagen / Image generation dla używanego klucza. "
                f"Szczegóły: {last_error}"
            )
        raise ImageGenerationError(str(last_error) if last_error else "Brak dostępnego modelu Gemini.")

    def _ordered_candidates(self) -> Iterable[str]:
        if self._active_model and self._active_model in self._model_candidates:
            yield self._active_model
        for cand in self._model_candidates:
            if cand != self._active_model:
                yield cand

    def _generate_with_model(self, model_name: str, prompt: str) -> bytes:
        methods_lower = [m.lower() for m in self._model_methods.get(model_name, [])]
        image_model = self._get_image_model(model_name)
        if image_model:
            try:
                result = self._call_generate_images(image_model, prompt)
            except Exception as exc:
                self.logger.debug("generate_images error for %s: %s", model_name, exc)
                self._image_models[model_name] = None
                raise ImageGenerationError(f"Gemini ({model_name}) generate_images: {exc}") from exc
            return self._extract_from_images_result(result)

        try:
            result = self._call_images_generate(model_name, prompt)
        except Exception as exc:
            self.logger.debug("images API error for %s: %s", model_name, exc)
        else:
            if result is not None:
                return self._extract_from_images_result(result)

        content_model = self._get_content_model(model_name)
        if not content_model or (
            methods_lower and not any("content" in m for m in methods_lower)
        ):
            self.logger.debug("Skipping generate_content for %s (methods=%s)", model_name, methods_lower)
            content_model = None

        if content_model:
            try:
                result = self._call_generate_content(content_model, prompt)
            except Exception as exc:
                self.logger.debug("generate_content error for %s: %s", model_name, exc)
                self._content_models[model_name] = None
                result = None
            else:
                img_bytes = self._extract_inline_image(result)
                if img_bytes:
                    return img_bytes
                self.logger.debug("generate_content returned no inline image for %s", model_name)

        # Fallback: HTTP API
        try:
            return self._call_rest_generate(model_name, prompt)
        except Exception as exc:
            raise ImageGenerationError(f"Gemini {model_name} fallback HTTP error: {exc}") from exc

    def _call_rest_generate(self, model_name: str, prompt: str) -> bytes:
        import json
        import requests

        endpoints = [
            f"https://generativelanguage.googleapis.com/v1beta/{model_name}:predict",
            f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateImage",
            f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generate",
        ]
        params = {"key": self._api_key}
        payload_variants = [
            {
                "prompt": {"text": prompt},
                "image_format": "png",
                "sample_count": 1,
            },
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            },
            {
                "prompt": {"text": prompt},
            },
        ]
        last_error: Optional[Exception] = None
        for url in endpoints:
            for payload in payload_variants:
                try:
                    self.logger.debug("Gemini HTTP call %s payload=%s", url, list(payload.keys()))
                    resp = requests.post(
                        url,
                        params=params,
                        headers={"Content-Type": "application/json"},
                        data=json.dumps(payload),
                        timeout=60,
                    )
                    if resp.status_code >= 400:
                        last_error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                        continue
                    data = resp.json()
                except Exception as exc:
                    last_error = exc
                    continue
                img_bytes = self._extract_rest_response(data)
                if img_bytes:
                    return img_bytes
        if last_error:
            raise last_error
        raise ImageGenerationError("Gemini HTTP API nie zwrócił obrazu.")

    @staticmethod
    def _extract_rest_response(data: Dict) -> Optional[bytes]:
        if not data:
            return None
        images = (
            data.get("images")
            or data.get("generatedImages")
            or data.get("responses")
            or data.get("predictions")
        )
        if isinstance(images, list):
            first = images[0]
            if isinstance(first, dict):
                if "image" in first and isinstance(first["image"], dict):
                    raw = first["image"].get("bytesBase64") or first["image"].get("b64_json")
                else:
                    raw = first.get("b64_json") or first.get("bytesBase64") or first.get("inlineData")
                if raw:
                    if isinstance(raw, dict):
                        raw = raw.get("data")
                    if isinstance(raw, str):
                        return base64.b64decode(raw)
        return None

    def _invoke_function_with_variants(self, func, prompt: str, model_name: Optional[str]):
        payloads = [
            {"model": model_name, "prompt": prompt, "size": self.image_size, "number_images": 1},
            {"model": model_name, "prompt": prompt, "size": self.image_size},
            {"model": model_name, "prompt": prompt},
            {"prompt": prompt},
        ]
        last_error: Optional[Exception] = None
        for payload in payloads:
            payload = {k: v for k, v in payload.items() if v is not None}
            try:
                return func(**payload)
            except TypeError as exc:
                last_error = exc
                self.logger.debug("Function API TypeError with payload %s: %s", payload, exc)
                continue
            except Exception as exc:
                last_error = exc
                self.logger.debug("Function API error with payload %s: %s", payload, exc)
                break
        if last_error:
            raise last_error
        raise ImageGenerationError("Nie udało się wywołać funkcji generowania obrazów Gemini.")

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        if "404" in msg and "not found" in msg:
            return True
        return False

    def _get_image_model(self, model_name: str):
        if model_name in self._image_models:
            return self._image_models[model_name]
        if not self._image_model_cls:
            self._image_models[model_name] = None
            return None
        try:
            model = self._image_model_cls(model_name=model_name)
            self.logger.debug("Initialized ImageGenerationModel for %s", model_name)
        except Exception as exc:
            self.logger.debug("Failed to init ImageGenerationModel %s: %s", model_name, exc)
            model = None
        self._image_models[model_name] = model
        return model

    def _get_content_model(self, model_name: str):
        if model_name in self._content_models:
            return self._content_models[model_name]
        try:
            model = self._genai.GenerativeModel(model_name)
            self.logger.debug("Initialized GenerativeModel for %s", model_name)
        except Exception as exc:
            self.logger.debug("Failed to init GenerativeModel %s: %s", model_name, exc)
            model = None
        self._content_models[model_name] = model
        return model

    def _call_generate_images(self, image_model, prompt: str):
        return self._invoke_with_variants(
            image_model,
            ("generate_images", "generate", "generate_image"),
            prompt,
        )

    def _call_images_generate(self, model_name: str, prompt: str):
        candidates = [
            ("api", self._images_api),
            ("module", self._images_module),
            ("function", self._generate_images_func),
        ]
        last_error: Optional[Exception] = None
        for label, target in candidates:
            if target is None:
                continue
            try:
                if inspect.isfunction(target) or inspect.ismethod(target):
                    return self._invoke_function_with_variants(target, prompt, model_name)
                return self._invoke_with_variants(
                    target,
                    ("generate", "generate_images", "generate_image"),
                    prompt,
                    model_name=model_name,
                )
            except Exception as exc:
                self.logger.debug("Image API %s failed: %s", label, exc)
                last_error = exc
                continue
        if last_error:
            raise last_error
        return None

    def _call_generate_content(self, content_model, prompt: str):
        return self._invoke_with_variants(
            content_model,
            ("generate_content",),
            prompt,
        )

    def _invoke_with_variants(self, target, method_names: Sequence[str], prompt: str, model_name: Optional[str] = None):
        last_error: Optional[Exception] = None
        for method_name in method_names:
            method = getattr(target, method_name, None)
            if not callable(method):
                continue
            base_payloads = [
                {"prompt": prompt, "number_images": 1, "size": self.image_size},
                {"prompt": prompt, "number_of_images": 1, "size": self.image_size},
                {"prompt": prompt, "number_images": 1},
                {"prompt": prompt},
                {"text": prompt},
                {"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
            ]
            for base in base_payloads:
                opts = dict(base)
                if model_name is not None:
                    opts.setdefault("model", model_name)
                try:
                    return method(**opts)
                except TypeError as exc:
                    last_error = exc
                    self.logger.debug(
                        "Method %s on %s TypeError: %s (payload=%s)",
                        method_name,
                        target,
                        exc,
                        opts,
                    )
                    continue
                except Exception as exc:
                    last_error = exc
                    self.logger.debug(
                        "Method %s on %s failed with %s",
                        method_name,
                        target,
                        exc,
                    )
                    break  # move to next method
        if last_error:
            raise last_error
        raise ImageGenerationError("Brak obsługiwanej metody generowania obrazów dla Gemini.")

    @staticmethod
    def _extract_from_images_result(result) -> bytes:
        images = getattr(result, "images", None) or getattr(result, "data", None)
        if not images:
            raise ImageGenerationError("Gemini nie zwrócił żadnych obrazów.")
        image_obj = images[0]
        raw = (
            getattr(image_obj, "image", None)
            or getattr(image_obj, "data", None)
            or (image_obj.as_base64() if hasattr(image_obj, "as_base64") else None)
        )
        if raw is None:
            raise ImageGenerationError("Nieznany format zwróconego obrazu Gemini.")
        if isinstance(raw, str):
            return base64.b64decode(raw)
        return raw

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
