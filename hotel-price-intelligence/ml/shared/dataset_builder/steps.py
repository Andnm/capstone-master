"""Registry cac step (`STEP_FUNCTIONS`): ten step -> ham nhan `StepContext`, tra ve report dict.

Batch B1: causal_references. B2: item_matches, samples_labels. B3: split, features_labels. B4: validation.
`_not_implemented` giu lai cho step tuong lai (ro rang, khong am tham bo qua step).
"""
from __future__ import annotations

from typing import Any

from .causal_references import build_causal_references
from .export import build_features_labels
from .manifest import load_manifest
from .matches import build_item_matches
from .runner import StepContext
from .samples import build_samples_labels
from .splitter import build_split
from .validation import validate_dataset


def _causal_references(ctx: StepContext) -> dict[str, Any]:
    return build_causal_references(ctx.conn, dataset_version=ctx.dataset_version, config=ctx.config)


def _item_matches(ctx: StepContext) -> dict[str, Any]:
    return build_item_matches(ctx.conn, dataset_version=ctx.dataset_version, config=ctx.config)


def _samples_labels(ctx: StepContext) -> dict[str, Any]:
    return build_samples_labels(ctx.conn, dataset_version=ctx.dataset_version, config=ctx.config)


def _split(ctx: StepContext) -> dict[str, Any]:
    return build_split(ctx.conn, dataset_version=ctx.dataset_version, config=ctx.config)


def _features_labels(ctx: StepContext) -> dict[str, Any]:
    return build_features_labels(ctx.conn, dataset_version=ctx.dataset_version, config=ctx.config,
                                 output_root=ctx.output_root, report_dir=ctx.report_dir)


def _validation(ctx: StepContext) -> dict[str, Any]:
    manifest = load_manifest(ctx.conn, ctx.dataset_version)
    ctx.conn.commit()
    return validate_dataset(ctx.conn, dataset_version=ctx.dataset_version, config=ctx.config, manifest=manifest, output_root=ctx.output_root)


def _not_implemented(name: str):
    def run(ctx: StepContext) -> dict[str, Any]:
        raise NotImplementedError(f"step {name!r} chua duoc cai dat (xem discuss/dataset-builder-3b batch plan).")
    return run


STEP_FUNCTIONS = {
    "causal_references": _causal_references,
    "item_matches": _item_matches,
    "samples_labels": _samples_labels,
    "split": _split,
    "features_labels": _features_labels,
    "validation": _validation,
}
