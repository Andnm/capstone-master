"""Wave B - hinh, bang, manifest, bao cao, data dictionary (versioned, strict-null JSON). Khong tinh toan phan tich o day (xem `wave_b.py`)."""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import artifacts  # noqa: E402
import wave_b  # noqa: E402
import wave_b_queries as wbq  # noqa: E402
from wave_b import COVERAGE_BULLETS, HORIZONS, Table, WaveBData, sanitize  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
EDA_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = Path(__file__).resolve().parent
SPLIT_COLORS = {"train": "#4c78a8", "validation": "#f58518", "test": "#54a24b", "purge": "#b0b0b0"}
STATUS_COLORS = {"exact": "#4c78a8", "alias": "#f58518", "unavailable": "#b0b0b0", "ambiguous": "#e45756"}


class WaveBIntegrityError(RuntimeError):
    """Mot kiem tra toan ven bat buoc (= 0) bi vi pham: analysis duoc danh dau FAILED, khong publish nhu PASS."""


# ------------------------------------------------------------------------------------------------ hinh
def _save(fig, figures_dir: Path, name: str) -> Path:
    path = figures_dir / f"{name}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _empty(ax, message: str) -> None:
    ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes)
    ax.set_xticks([])
    ax.set_yticks([])


def make_figures(data: WaveBData, tables: dict[str, Table], figures_dir: Path) -> dict[str, tuple[int, ...]]:
    figures: dict[str, tuple[int, ...]] = {}
    samples = wave_b.prepare_samples(data.samples)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    by_day = samples.groupby(["vn_observation_date", "split_label"]).size().unstack(fill_value=0)
    if by_day.empty:
        _empty(ax, "khong co mau")
    else:
        bottom = np.zeros(len(by_day))
        for split in ("train", "validation", "test", "purge"):
            if split in by_day.columns:
                ax.bar(by_day.index, by_day[split].to_numpy(), bottom=bottom, color=SPLIT_COLORS[split], label=split, width=0.9)
                bottom = bottom + by_day[split].to_numpy()
        ax.legend()
    ax.set_title("Mau duoc chon theo ngay quan sat VN va split (vung xam = purge)")
    ax.set_ylabel("so mau")
    figures[_save_name(fig, figures_dir, "fig01_samples_by_date_split")] = (4, 7)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    rates = tables["b05_label_rates"].frame
    width = 0.25
    for i, split in enumerate(("train", "validation", "test")):
        sub = rates[rates["split"] == split].set_index("horizon").reindex(HORIZONS)
        ax.bar(np.arange(len(HORIZONS)) + i * width, sub["usable_rate_of_source_eligible"].fillna(0).to_numpy(), width, color=SPLIT_COLORS[split], label=split)
        for j, (_, row) in enumerate(sub.iterrows()):
            defined = pd.notna(row["usable_rate_of_source_eligible"])
            ax.text(j + i * width, float(row["usable_rate_of_source_eligible"]) if defined else 0, f"n={int(row['n_source_eligible'])}" if defined else "undefined (n=0)",
                    ha="center", va="bottom", fontsize=7, rotation=90, color="black" if defined else "#c00000")
    ax.set_xticks(np.arange(len(HORIZONS)) + width)
    ax.set_xticklabels([f"h{k}" for k in HORIZONS])
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("usable / source-eligible")
    ax.legend()
    ax.set_title("Ty le nhan usable theo horizon va split (mau so source-eligible ghi tren cot; thieu = mau so 0)")
    figures[_save_name(fig, figures_dir, "fig02_label_usable_rate")] = (5,)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    lead = tables["b02_match_by_lead_bucket"].frame
    post = lead[lead["phase"] == "post_approval"]
    if post.empty:
        _empty(ax, "khong co item post_approval")
    else:
        pivot = post.pivot_table(index="lead_bucket", columns="match_status", values="share", aggfunc="sum").reindex(wave_b.LEAD_LABELS).dropna(how="all")
        bottom = np.zeros(len(pivot))
        for status in wave_b.MATCH_STATUSES:
            if status in pivot.columns:
                ax.bar(pivot.index, pivot[status].fillna(0).to_numpy(), bottom=bottom, color=STATUS_COLORS[status], label=status)
                bottom = bottom + pivot[status].fillna(0).to_numpy()
        ax.legend()
    ax.set_title("Trang thai khop item sau approval theo lead bucket (nua mo)")
    ax.set_ylabel("ty le trong bucket")
    figures[_save_name(fig, figures_dir, "fig03_match_status_by_lead_bucket_post")] = (2,)

    fig, ax = plt.subplots(figsize=(8, 4))
    dist = tables["b01_time_to_approval_distribution"].frame
    ax.bar(dist["bin"].astype(str), dist["n_assignments"].to_numpy(), color="#4c78a8")
    ax.set_title("Thoi gian tu bang chung dau tien toi approved_at (ngay)")
    ax.set_ylabel("so assignment")
    figures[_save_name(fig, figures_dir, "fig04_time_to_approval")] = (1,)

    fig, ax = plt.subplots(figsize=(9, 6))
    miss = tables["b09_feature_missingness"].frame
    heat = miss[miss["dimension"] == "split"].pivot_table(index="feature", columns="value", values="null_share")
    heat = heat[heat.max(axis=1) > 0] if not heat.empty else heat
    if heat.empty:
        _empty(ax, "khong feature nao co null")
    else:
        image = ax.imshow(heat.to_numpy(dtype=float), aspect="auto", cmap="Reds", vmin=0, vmax=1)
        ax.set_xticks(range(len(heat.columns)))
        ax.set_xticklabels(heat.columns)
        ax.set_yticks(range(len(heat.index)))
        ax.set_yticklabels(heat.index, fontsize=7)
        fig.colorbar(image, ax=ax, label="ty le null")
    ax.set_title("Ty le null theo feature va split (chi feature co null)")
    figures[_save_name(fig, figures_dir, "fig05_missingness_heatmap")] = (9,)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    att = tables["b08_coverage_attrition"].frame
    labels = [f"h{k}" for k in att["horizon"]]
    parts = [("n_not_calendar_possible", "ngoai cua so/qua checkin", "#b0b0b0"), ("n_target_day_sample_missing", "thieu mau ngay dich", "#e45756"),
             ("n_has_label_cross_split", "co nhan nhung khac split", "#f58518"), ("n_usable", "usable", "#4c78a8")]
    bottom = np.zeros(len(att))
    for column, text, color in parts:
        ax.bar(labels, att[column].to_numpy(), bottom=bottom, color=color, label=text)
        bottom = bottom + att[column].to_numpy()
    ax.legend(fontsize=8)
    ax.set_title("Attrition cua cap ly thuyet noi tai dataset theo horizon")
    ax.set_ylabel("so mau")
    figures[_save_name(fig, figures_dir, "fig06_coverage_attrition")] = (8,)

    fig, ax = plt.subplots(figsize=(8, 4))
    modes = tables["b10_readiness_by_mode"].frame
    if modes.empty:
        _empty(ax, "khong co mau")
    else:
        pivot = modes.pivot_table(index="split", columns="inference_mode", values="n_samples", aggfunc="sum").reindex(["train", "validation", "test", "purge"]).dropna(how="all")
        pivot.plot(kind="bar", ax=ax, color=["#4c78a8", "#f58518"][:len(pivot.columns)])
    ax.set_title("Mau theo split va inference_mode")
    ax.set_ylabel("so mau")
    figures[_save_name(fig, figures_dir, "fig07_readiness_by_mode")] = (10,)
    return figures


def _save_name(fig, figures_dir: Path, name: str) -> str:
    _save(fig, figures_dir, name)
    return name


# ------------------------------------------------------------------------------------------------ manifest
def _git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def code_provenance() -> dict[str, Any]:
    files = sorted(SRC_DIR.glob("wave_b*.py")) + [EDA_DIR / "run_wave_b.py", EDA_DIR / "notebooks" / "02_curated_ml_eda.ipynb"]
    dirty = _git("status", "--porcelain", "--", "hotel-price-intelligence/eda", "hotel-price-intelligence/ml")
    return {"git_head": _git("rev-parse", "HEAD"), "eda_ml_dirty_files": [ln[3:] for ln in dirty.splitlines()] if dirty else ([] if dirty == "" else None),
            "files_sha256": {str(f.relative_to(REPO_ROOT)).replace("\\", "/"): artifacts.sha256_file(f) for f in files if f.exists()},
            "catalog_version": wbq.CATALOG_VERSION}


def library_versions() -> dict[str, str]:
    import importlib.metadata as md

    out = {"python": platform.python_version()}
    for name in ("pandas", "numpy", "pyarrow", "matplotlib", "mysql-connector-python", "nbclient"):
        try:
            out[name] = md.version(name)
        except md.PackageNotFoundError:
            out[name] = "not-installed"
    return out


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


# ------------------------------------------------------------------------------------------------ ghi tat ca
def write_outputs(analysis_dir: Path, data: WaveBData, tables: dict[str, Table], figures: dict[str, tuple[int, ...]], violations: dict[str, int]) -> dict[str, Any]:
    tables_dir = analysis_dir / "tables"
    for table in tables.values():
        table.frame.to_csv(tables_dir / f"{table.table_id}.csv", index=False, encoding="utf-8")
    coverage = wave_b.coverage_matrix(tables, figures)
    coverage.to_csv(analysis_dir / "coverage_matrix_b.csv", index=False, encoding="utf-8")
    write_json(analysis_dir / "query_catalog_b.json", wbq.catalog_manifest())
    inputs = data.inputs
    generation = data.preflight["artifact_generation"]
    write_json(analysis_dir / "input_manifest_b.json", {
        "inputs": inputs.to_manifest_dict(), "preflight": data.preflight, "code": code_provenance(), "libraries": library_versions(),
        "row_counts": {"samples_parquet": len(data.samples), "ml_samples": len(data.ml_samples), "assignments": len(data.assignments), "item_matches": len(data.matches),
                       "first_reference_evidence": len(data.first_evidence), "hotels_snapshot": len(data.hotels)},
        "evaluation_horizons": data.evaluation_horizons, "denominator_policy": "moi bang ghi n_* rieng; mau so 0 => ty le null + rate_status",
        "bucket_policy": "lead time nua mo [0,3) [3,7) [7,14) [14,30) [30,60) [60,inf); nhan nhu builder",
    })
    summary = summarize(data, tables, violations)
    write_json(analysis_dir / "eda_summary_b.json", summary)
    (analysis_dir / "EDA_REPORT_B.md").write_text(render_report(data, tables, coverage, violations, generation), encoding="utf-8")
    (analysis_dir / "DATA_DICTIONARY_B.md").write_text(render_dictionary(tables), encoding="utf-8")
    return summary


def summarize(data: WaveBData, tables: dict[str, Table], violations: dict[str, int]) -> dict[str, Any]:
    rates = tables["b05_label_rates"].frame
    all_rows = rates[rates["split"] == "ALL"].set_index("horizon")
    return {
        "dataset_version": data.inputs.dataset_version, "database": data.inputs.database, "batch_id": data.inputs.batch_id,
        "artifact_generation": data.preflight["artifact_generation"], "evaluation_horizons": data.evaluation_horizons,
        "selected_samples": int(len(data.samples)), "assignments": int(len(data.assignments)), "hotels": int(data.samples["hotel_id"].nunique()),
        "series": int(data.samples["canonical_series_id"].nunique()),
        "usable_labels_by_horizon": {f"h{k}": int(all_rows.loc[k, "n_usable"]) for k in HORIZONS},
        "usable_rate_of_source_eligible_by_horizon": {f"h{k}": all_rows.loc[k, "usable_rate_of_source_eligible"] for k in HORIZONS},
        "hard_violations": violations, "hard_violations_total": int(sum(violations.values())),
    }


def _markdown_table(frame: pd.DataFrame, max_rows: int = 40) -> str:
    shown = frame.head(max_rows).copy()
    shown = shown.map(lambda v: f"{v:.4g}" if isinstance(v, float) else ("" if v is None or v is pd.NaT else str(v)))
    header = "| " + " | ".join(shown.columns) + " |"
    rule = "|" + "|".join("---" for _ in shown.columns) + "|"
    body = ["| " + " | ".join(row) + " |" for row in shown.astype(str).to_numpy().tolist()]
    note = f"\n\n_(hien {len(shown)}/{len(frame)} dong; day du trong CSV)_" if len(frame) > max_rows else ""
    return "\n".join([header, rule, *body]) + note


def render_report(data: WaveBData, tables: dict[str, Table], coverage: pd.DataFrame, violations: dict[str, int], generation: dict[str, Any]) -> str:
    lines = [f"# EDA Wave B - Curated ML EDA: `{data.inputs.dataset_version}`", "",
             f"- database `{data.inputs.database}` / batch `{data.inputs.batch_id}`; builder `{generation.get('builder_version')}`, feature `{generation.get('feature_version')}`, "
             f"purpose `{generation.get('purpose')}`; evaluation_horizons {data.evaluation_horizons}.",
             f"- artifact thuoc the he `{'legacy (khong co dataset_contract.json)' if generation['legacy'] else 'contract'}`, cot strata: {generation['has_strata_columns']}.",
             ("- **Phan tich REHEARSAL/EXPLORATORY** (purpose=`%s`): khong dung de ket luan danh gia model." % generation.get("purpose"))
             if generation.get("purpose") != "official" else "- dataset purpose=`official`; van chi la EDA mo ta, khong thay the danh gia model.",
             "- Review-score tier la phan khuc post-hoc tren snapshot (khong as-of, khong phai feature).",
             "- Cap ly thuyet o muc 8 la NOI TAI dataset, khong cung quan the voi theoretical_date_pairs cua Wave A.", "",
             "## Toan ven bat buoc (phai = 0)", "", _markdown_table(pd.DataFrame([{"check": k, "violations": v} for k, v in violations.items()])), "",
             "## Coverage matrix yeu cau muc 8", "", _markdown_table(coverage[["bullet", "requirement", "tables", "figures", "covered"]]), ""]
    for table in tables.values():
        lines += [f"### {table.table_id}", "", f"- grain: {table.grain}", f"- mau so: {table.denominator}", *([f"- ghi chu: {table.note}"] if table.note else []), "",
                  _markdown_table(table.frame), ""]
    return "\n".join(lines)


def render_dictionary(tables: dict[str, Table]) -> str:
    lines = ["# Data dictionary Wave B", "", "Moi bang: grain, mau so, cot. Cac bang `rate`/`share` luon di kem `*_status` (undefined_zero_denominator khi mau so 0).", ""]
    for table in tables.values():
        lines += [f"## {table.table_id}", "", f"- yeu cau muc 8: {', '.join(str(b) for b in table.bullets)}", f"- grain: {table.grain}", f"- mau so: {table.denominator}",
                  *([f"- ghi chu: {table.note}"] if table.note else []), "- cot: " + ", ".join(f"`{c}`" for c in table.frame.columns), ""]
    lines += ["## Yeu cau muc 8", ""] + [f"- ({n}) {text}" for n, text in COVERAGE_BULLETS.items()]
    return "\n".join(lines) + "\n"


def check_violations(violations: dict[str, int]) -> None:
    bad = {k: v for k, v in violations.items() if v}
    if bad:
        raise WaveBIntegrityError(f"kiem tra toan ven Wave B vi pham: {bad}")


def run_all(conn, inputs, preflight, analysis_dir: Path) -> dict[str, Any]:
    """Diem vao duy nhat cho notebook: nap -> tinh -> hinh -> ghi -> kiem toan ven (raise sau khi da ghi het bang chung)."""
    data = wave_b.load_data(conn, inputs, preflight)
    tables = wave_b.compute_tables(data)
    figures = make_figures(data, tables, analysis_dir / "figures")
    violations = wave_b.hard_violations(tables)
    summary = write_outputs(analysis_dir, data, tables, figures, violations)
    check_violations(violations)
    return summary


if __name__ == "__main__":  # pragma: no cover
    sys.exit("chay qua run_wave_b.py")
