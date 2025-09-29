"""Structural validation for bundled OpenAPI specifications."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest


HTTP_METHODS = {
    "get",
    "put",
    "post",
    "delete",
    "options",
    "head",
    "patch",
    "trace",
}


@pytest.mark.parametrize(
    "spec_path",
    [
        Path(__file__).resolve().parents[2] / "openapi.json",
        Path(__file__).resolve().parents[2] / "openapi_bliss.json",
    ],
)
def test_openapi_files_meet_minimum_requirements(spec_path: Path) -> None:
    """Ensure the shipped OpenAPI documents satisfy the documented requirements."""

    with spec_path.open("r", encoding="utf-8") as handle:
        spec: Dict[str, object] = json.load(handle)

    assert isinstance(spec.get("openapi"), str), "Missing 'openapi' version string"
    assert spec["openapi"].startswith(
        "3.",
    ), f"Unsupported OpenAPI version: {spec['openapi']}"

    info = spec.get("info")
    assert isinstance(info, dict), "Missing top-level 'info' object"
    for field in ("title", "version"):
        assert isinstance(info.get(field), str) and info[field].strip(), (
            f"Missing required info.{field} in {spec_path.name}"
        )

    paths = spec.get("paths")
    assert isinstance(paths, dict) and paths, "Specification must define at least one path"

    operation_ids: set[str] = set()
    for route, methods in paths.items():
        assert isinstance(methods, dict), f"Path item {route} must be an object"
        http_operations = {
            method.lower(): data
            for method, data in methods.items()
            if method.lower() in HTTP_METHODS
        }
        assert http_operations, f"Path {route} does not declare HTTP operations"

        for method, operation in http_operations.items():
            assert isinstance(operation, dict), (
                f"Operation object for {method.upper()} {route} must be an object"
            )

            op_id = operation.get("operationId")
            assert isinstance(op_id, str) and op_id.strip(), (
                f"operationId is required for {method.upper()} {route}"
            )
            assert op_id not in operation_ids, f"Duplicate operationId detected: {op_id}"
            operation_ids.add(op_id)

            responses = operation.get("responses")
            assert isinstance(responses, dict) and responses, (
                f"{method.upper()} {route} must declare at least one response"
            )

            for status, response in responses.items():
                assert isinstance(response, dict), (
                    f"Response {status} for {method.upper()} {route} must be an object"
                )

                description = response.get("description")
                assert isinstance(description, str) and description.strip(), (
                    f"Response {status} for {method.upper()} {route} requires a description"
                )

                content = response.get("content")
                assert isinstance(content, dict) and content, (
                    f"Response {status} for {method.upper()} {route} must declare content"
                )

                for media_type, media_object in content.items():
                    assert isinstance(media_object, dict), (
                        f"Content for {media_type} in response {status} of {method.upper()} {route} must be an object"
                    )
                    schema = media_object.get("schema")
                    assert isinstance(schema, dict), (
                        f"Media type {media_type} in response {status} of {method.upper()} {route} requires a schema"
                    )

