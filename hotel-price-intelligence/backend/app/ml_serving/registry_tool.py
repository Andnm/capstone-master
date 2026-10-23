"""Công cụ dựng registry — KHÔNG TỰ CẤP "validated" (GPT 10 M2).

Chỉ sinh ỨNG VIÊN để review. Quy tắc:
  * bundle LUÔN được đối chiếu sha256 với `run_manifest.json["outputs"]` (provenance lúc train) VÀ với registry đã duyệt (nếu có) TRƯỚC KHI deserialize;
  * `validatedRuntime`/`compatibility` đã duyệt chỉ được MANG SANG nguyên văn khi runtime hiện tại khớp đúng runtime đã duyệt VÀ mã phụ thuộc không đổi VÀ hash bundle không đổi;
  * mọi trường hợp khác ⇒ mục ứng viên `unvalidated_candidate`, KHÔNG có `validatedRuntime` (nên `load_registry` từ chối nạp nhầm) và không bao giờ mang nhãn `dev_compatibility_empirically_checked`;
  * công cụ không bao giờ ghi đè `registry.json`; chuyển ứng viên thành registry là bước REVIEW của con người/GPT sau khi có full replay có báo cáo.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .errors import IntegrityError
from .registry import COMPAT_STATUS, RUNTIME_PACKAGES, SCHEMA, default_loader, resolve_contained, runtime_drift, sha256_file

CANDIDATE_STATUS = "unvalidated_candidate"


@dataclass(frozen=True)
class BundleSpec:
    id: str
    role: str
    horizon: int
    dataset: str
    run_rel: str                  # tương đối workspace, trong vùng cho phép
    bundle: str
    predictions: str
    report: str
    name: str


@dataclass
class CandidateResult:
    registry: dict[str, Any]
    approved: bool
    findings: list[str] = field(default_factory=list)


def _rel(workspace_root: Path, path: Path) -> str:
    return path.resolve().relative_to(Path(workspace_root).resolve()).as_posix()


def build_candidate(*, workspace_root: Path, existing: Mapping[str, Any] | None, specs: Sequence[BundleSpec], code_dependencies: list[dict[str, str]],
                    nullable_features: list[str], datasets: list[dict[str, Any]], runtime: Mapping[str, str | None],
                    loader: Callable[[Path], Any] | None = None) -> CandidateResult:
    workspace_root = Path(workspace_root).resolve()
    loader = loader or default_loader()
    existing_entries = {e["id"]: e for e in (existing or {}).get("bundles", [])}
    findings: list[str] = []
    code_same = existing is not None and list(existing.get("codeDependencies", [])) == list(code_dependencies)
    if existing is not None and not code_same:
        findings.append("codeDependencies khác registry đã duyệt: cần validate lại (full replay) trước khi duyệt")
    observed = {"python": runtime.get("python"), **{k: runtime.get(k) for k in RUNTIME_PACKAGES}}
    entries: list[dict[str, Any]] = []
    for spec in specs:
        run_dir = resolve_contained(workspace_root, spec.run_rel)
        bundle_path = resolve_contained(workspace_root, f"{spec.run_rel}/{spec.bundle}")
        actual = sha256_file(bundle_path)
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        recorded = (manifest.get("outputs") or {}).get(spec.bundle)
        if recorded is None or actual != recorded:
            raise IntegrityError(f"{spec.id}: sha256 bundle không khớp run_manifest (provenance lúc train) — KHÔNG deserialize")
        prior = existing_entries.get(spec.id)
        if prior is not None and prior["sha256"] != actual:
            raise IntegrityError(f"{spec.id}: sha256 bundle khác registry đã duyệt — KHÔNG deserialize")
        bundle = loader(bundle_path)                                                    # chỉ SAU khi hai lần đối chiếu hash đã qua
        trained = {k: str(v) for k, v in bundle["environment_actual"].items()}
        entry: dict[str, Any] = {
            "id": spec.id, "role": spec.role, "horizon": spec.horizon, "path": _rel(workspace_root, bundle_path), "sha256": actual, "datasetVersion": spec.dataset,
            "runId": run_dir.name, "modelName": bundle["name"], "family": bundle["family"], "matrix": bundle["matrix"], "targetTransform": bundle["target_transform"],
            "claimLevel": bundle["claim_level"], "nFeaturesIn": int(bundle["n_features_in"]), "featureListSha256": bundle["feature_list_sha256"],
            "effectiveFeatureListSha256": bundle["effective_feature_list_sha256"], "trainedEnvironment": trained,
        }
        if bundle["name"] != spec.name or bundle["horizon"] != spec.horizon:
            raise IntegrityError(f"{spec.id}: danh tính bundle khác đặc tả")
        approved = (prior is not None and code_same and not runtime_drift(prior["validatedRuntime"], runtime)
                    and (prior.get("compatibility") or {}).get("status") == COMPAT_STATUS)
        if approved:
            entry["validatedRuntime"] = prior["validatedRuntime"]
            entry["compatibility"] = prior["compatibility"]
        else:
            reasons = []
            if prior is None:
                reasons.append("bundle mới, chưa có registry đã duyệt")
            elif runtime_drift(prior["validatedRuntime"], runtime):
                reasons.append("runtime hiện tại ngoài validatedRuntime đã duyệt: " + "; ".join(runtime_drift(prior["validatedRuntime"], runtime)))
            if not code_same:
                reasons.append("mã phụ thuộc đổi")
            findings.append(f"{spec.id}: ứng viên — {'; '.join(reasons)}")
            entry["observedRuntime"] = observed                                         # quan sát, KHÔNG phải đã kiểm
            entry["compatibility"] = {"status": CANDIDATE_STATUS, "evidence": None, "reason": reasons}
        entry["predictions"] = {"path": _rel(workspace_root, resolve_contained(workspace_root, f"{spec.run_rel}/{spec.predictions}")),
                                "sha256": sha256_file(run_dir / spec.predictions), "unroutedColumn": f"pred_{bundle['name']}", "routedColumn": f"pred_routed:{bundle['name']}"}
        entry["report"] = {"path": _rel(workspace_root, resolve_contained(workspace_root, f"{spec.run_rel}/{spec.report}")), "sha256": sha256_file(run_dir / spec.report)}
        entries.append(entry)
    # Công cụ này chỉ dựng mục HỒI QUY. Mục phân loại (đã duyệt riêng qua m06 + classify.replay_classifier) và hai khối phụ thuộc của chúng chỉ được MANG SANG nguyên văn từ registry đã duyệt, không bao giờ tạo mới ở đây.
    carried = [e for e in (existing or {}).get("bundles", []) if e.get("role") != "regression_primary"]
    carried_sections = {k: existing[k] for k in ("classificationCodeDependencies", "extraLibs") if existing is not None and k in existing}
    registry = {"schema": SCHEMA, "note": "dev/exploratory; KHÔNG phải package production. validatedRuntime = môi trường đã replay; môi trường khác ⇒ fail.",
                "codeDependencies": code_dependencies, **carried_sections, "nullableFeatures": sorted(nullable_features), "datasets": datasets, "bundles": entries + carried}
    all_approved = all(e["compatibility"]["status"] == COMPAT_STATUS for e in entries)
    if existing is not None and all_approved:
        for key in ("note", "datasets", "nullableFeatures"):
            if existing.get(key) != registry.get(key):
                findings.append(f"trường {key} khác registry đã duyệt")
                all_approved = False
    return CandidateResult(registry=registry, approved=all_approved, findings=findings)
