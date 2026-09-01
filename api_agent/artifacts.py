"""Artifact serialization and schema export helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def write_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2, by_alias=True), encoding="utf-8")


def read_model(path: Path, model_type: type[T]) -> T:
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def export_schemas(directory: Path, models: list[type[BaseModel]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for model in models:
        write_json(directory / f"{model.__name__}.schema.json", model.model_json_schema())

