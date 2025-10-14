import os
from dataclasses import dataclass


BASE_DIR = os.path.dirname(__file__)
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)

DEFAULT_IMAGE_SIZE = os.getenv("NEWS_TO_IMAGE_SIZE", "1024x1024")
DEFAULT_LANGUAGE = os.getenv("NEWS_TO_IMAGE_LANGUAGE", "pl")


@dataclass(frozen=True)
class ImageProjectPaths:
    project_dir: str
    manifest_path: str


def ensure_project_paths(project_id: str) -> ImageProjectPaths:
    project_dir = os.path.join(PROJECTS_DIR, project_id)
    os.makedirs(project_dir, exist_ok=True)
    manifest_path = os.path.join(project_dir, "manifest.json")
    return ImageProjectPaths(project_dir=project_dir, manifest_path=manifest_path)
