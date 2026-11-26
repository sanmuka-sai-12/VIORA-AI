"""Export the aggregate consumed by OpenAPI client generators."""

import json
from pathlib import Path

from pydantic.json_schema import models_json_schema

from .contracts import (
    ArtifactVersionRef,
    IdempotentRequest,
    JobAcceptance,
    JobStatus,
    SourceEvidence,
    TenantContext,
)
from .main import create_app


def export(destination: Path) -> None:
    spec = create_app().openapi()
    _, definitions = models_json_schema(
        [
            (model, "validation")
            for model in (
                ArtifactVersionRef,
                JobAcceptance,
                JobStatus,
                IdempotentRequest,
                SourceEvidence,
                TenantContext,
            )
        ],
        ref_template="#/components/schemas/{model}",
    )
    schemas = spec["components"]["schemas"]
    for name, schema in definitions.get("$defs", {}).items():
        if name in schemas and schemas[name] != schema:
            raise ValueError(f"Conflicting contract: {name}")
        schemas[name] = schema
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    export(Path("contracts/http/openapi.json"))
