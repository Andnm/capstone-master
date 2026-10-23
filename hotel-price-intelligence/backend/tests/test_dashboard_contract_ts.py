"""A1 (TEST_PLAN): hợp đồng pydantic (camelCase) ↔ `dashboard-frontend/types.ts` — so khóa hai chiều cho mọi interface cùng tên + các kiểu liệt kê (Literal). Không cần Node."""
import json
import re
import shutil
import subprocess
import typing
from pathlib import Path

import pytest

from app.dashboard import labels
from app.dashboard import schemas as S

ROOT = Path(__file__).resolve().parents[2]
TYPES_TS = ROOT / "dashboard-frontend" / "types.ts"
CHECK_LABELS = ROOT / "dashboard-frontend" / "scripts" / "check-ui-labels.mjs"
pytestmark = pytest.mark.skipif(not TYPES_TS.exists(), reason="thiếu dashboard-frontend/types.ts")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def parse_interfaces(source: str) -> dict[str, tuple[str | None, set[str]]]:
    source = strip_comments(source)
    out: dict[str, tuple[str | None, set[str]]] = {}
    for match in re.finditer(r"export interface (\w+)(?: extends (\w+))? \{", source):
        name, parent = match.group(1), match.group(2)
        depth, i, props, token = 1, match.end(), set(), ""
        while i < len(source) and depth > 0:
            ch = source[i]
            if ch in "{[(<":
                depth += 1
            elif ch in "}])>":
                if not (ch == ">" and source[i - 1] == "="):                       # bỏ qua mũi tên =>
                    depth -= 1
            elif depth == 1 and ch == ":":
                key = re.search(r"(\w+)\??\s*$", token)
                if key:
                    props.add(key.group(1))
                token = ""
                i += 1
                continue
            elif depth == 1 and ch in ";\n":
                token = ""
            if depth == 1:
                token += ch
            i += 1
        out[name] = (parent, props)
    return out


def ts_props(interfaces: dict[str, tuple[str | None, set[str]]], name: str) -> set[str]:
    parent, props = interfaces[name]
    return props | (ts_props(interfaces, parent) if parent else set())


def literal_members(source: str, name: str) -> set[str]:
    match = re.search(rf"export type {name} = ([^;]+);", strip_comments(source))
    assert match, name
    return {m.strip().strip('"') for m in match.group(1).split("|")}


INTERFACES = parse_interfaces(TYPES_TS.read_text(encoding="utf-8"))
PYDANTIC = {name: obj for name, obj in vars(S).items() if isinstance(obj, type) and issubclass(obj, S.ApiModel) and obj is not S.ApiModel}
SHARED = sorted(set(INTERFACES) & set(PYDANTIC))


def test_every_ts_interface_with_a_pydantic_twin_has_identical_keys():
    assert len(SHARED) >= 20, SHARED
    for name in SHARED:
        pyd = {field.alias or key for key, field in PYDANTIC[name].model_fields.items()}
        assert pyd == ts_props(INTERFACES, name), (name, pyd ^ ts_props(INTERFACES, name))


def test_key_endpoints_are_covered_by_the_comparison():
    for name in ("DashboardConfig", "HotelsResponse", "ForecastResponse", "HotelierResponse", "ModelInfoResponse", "CompsetHotel", "GroupSummary", "HorizonModelInfo", "HotelierAlert"):
        assert name in SHARED, name


@pytest.mark.parametrize("name", ["WarningCode", "Direction", "InferenceMode", "PredictionMethod", "ClassifierReason"])
def test_string_enumerations_match(name):
    ts = literal_members(TYPES_TS.read_text(encoding="utf-8"), name)
    py = set(typing.get_args(getattr(S, name)))
    assert ts == py, (name, ts ^ py)


def test_horizon_enumerations_match():
    source = TYPES_TS.read_text(encoding="utf-8")
    assert {int(x) for x in literal_members(source, "Horizon")} == set(typing.get_args(S.Horizon))
    assert {int(x) for x in literal_members(source, "ServedHorizon")} == set(typing.get_args(S.ServedHorizon))


def test_no_confidence_tier_or_sold_out_anywhere_in_contract():
    text = TYPES_TS.read_text(encoding="utf-8")
    assert "confidenceTier" not in text and "soldOut" not in text and "competitorAverage" not in text
    assert not [k for model in PYDANTIC.values() for k in model.model_fields if k in {"confidence_tier", "sold_out", "competitor_average"}]


def test_frontend_label_scanner_uses_the_same_banned_patterns_as_backend():
    text = CHECK_LABELS.read_text(encoding="utf-8")
    block = re.search(r"export const BANNED_PATTERNS = \[(.*?)\];", text, flags=re.S).group(1)
    frontend = re.findall(r'"([^"]+)"', block)
    assert frontend == list(labels.BANNED_PATTERNS)


# ---------------------------------------------------------------- A1 đệ quy: TypeScript compiler thật ↔ pydantic JSON schema (mọi tầng)
SHAPE_SCRIPT = ROOT / "dashboard-frontend" / "scripts" / "ts-contract-shape.mjs"
ROOTS = ["DashboardConfig", "HotelsResponse", "ForecastResponse", "HotelierResponse", "ModelInfoResponse"]
needs_node = pytest.mark.skipif(shutil.which("node") is None or not SHAPE_SCRIPT.exists() or not (ROOT / "dashboard-frontend" / "node_modules" / "typescript").exists(),
                                reason="thiếu node/typescript của dashboard-frontend")


def ts_shapes(types_path: Path | None = None) -> dict:
    command = ["node", str(SHAPE_SCRIPT)] + ([str(types_path)] if types_path else []) + ROOTS
    out = subprocess.run(command, cwd=ROOT / "dashboard-frontend", capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout)


def norm(node: dict, defs: dict) -> dict:
    """pydantic JSON schema -> cùng dạng chuẩn với ts-contract-shape.mjs."""
    if "$ref" in node:
        return norm(defs[node["$ref"].split("/")[-1]], defs)
    if "allOf" in node and len(node["allOf"]) == 1:
        return norm(node["allOf"][0], defs)
    if "anyOf" in node:
        members = node["anyOf"]
        nullable = any(m.get("type") == "null" for m in members)
        rest = [m for m in members if m.get("type") != "null"]
        assert len(rest) == 1, node
        return {**norm(rest[0], defs), "nullable": nullable}
    if "const" in node:
        return {"nullable": False, "kind": "enum", "values": [node["const"]]}
    if "enum" in node:
        return {"nullable": False, "kind": "enum", "values": sorted(node["enum"])}
    kind = node.get("type")
    if kind == "string":
        return {"nullable": False, "kind": "string"}
    if kind in ("integer", "number"):
        return {"nullable": False, "kind": "number"}
    if kind == "boolean":
        return {"nullable": False, "kind": "boolean"}
    if kind == "array":
        return {"nullable": False, "kind": "array", "item": norm(node["items"], defs)}
    if kind == "object":
        if "properties" in node:
            required = set(node.get("required", []))
            return {"nullable": False, "kind": "object", "props": {k: {"optional": k not in required, **norm(v, defs)} for k, v in node["properties"].items()}}
        return {"nullable": False, "kind": "record", "value": norm(node["additionalProperties"], defs)}
    raise AssertionError(f"schema không hỗ trợ: {node}")


def pydantic_shape(model) -> dict:
    schema = model.model_json_schema(by_alias=True)
    return norm({k: v for k, v in schema.items() if k != "$defs"}, schema.get("$defs", {}))


def diff(a, b, path="") -> list[str]:
    if type(a) is not type(b):
        return [f"{path}: kiểu khác ({type(a).__name__} vs {type(b).__name__})"]
    if isinstance(a, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append(f"{path}.{key}: chỉ có ở TS")
            elif key not in b:
                out.append(f"{path}.{key}: chỉ có ở pydantic")
            else:
                out += diff(a[key], b[key], f"{path}.{key}")
        return out
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


@needs_node
@pytest.mark.parametrize("name", ROOTS)
def test_a1_recursive_ts_compiler_shape_equals_pydantic_schema(name):
    ts_side = ts_shapes()[name]
    py_side = pydantic_shape(getattr(S, name))
    assert diff(py_side, ts_side, name) == []                                           # (pydantic, TS): "chỉ có ở TS" = TS thừa, "chỉ có ở pydantic" = TS thiếu


@needs_node
def test_a1_classifier_child_is_covered_recursively():
    shape = pydantic_shape(S.ModelInfoResponse)["props"]["classifier"]["props"]["horizons"]["item"]["props"]
    assert {"trainedEnvironment", "validatedRuntime", "classMapping", "compatibility", "coverage", "limitations"} <= set(shape)
    ts_child = ts_shapes()["ModelInfoResponse"]["props"]["classifier"]["props"]["horizons"]["item"]["props"]
    assert set(ts_child) == set(shape) and ts_child["coverage"]["props"]["recordIdsLinkedToRegression"]["values"] == [True]


@needs_node
@pytest.mark.parametrize("old, new, expect", [
    ("  classMapping: Record<string, string>;\n", "", "classMapping"),                                    # thiếu field ở child inline của classifier
    ("historySpanDays: number | null;", "historySpanDays: number;", "historySpanDays"),                  # mất nullable
    ("  coverage: { snapshotRecords: number; recordIdsLinkedToRegression: true };\n", "  coverage: { snapshotRecords: number };\n", "recordIdsLinkedToRegression"),   # thiếu field ở object lồng
    ("pctVsGroupMedian: number | null;", "pctVsGroupMedian?: number | null;", "pctVsGroupMedian"),      # sai optional
    ('direction: Direction;\n  targetObservationDate', 'direction: "up" | "down";\n  targetObservationDate', "direction"),                              # enum lệch
    ("trainedEnvironment: Record<string, string>;\n  validatedRuntime: Record<string, string>;\n  classMapping", "trainedEnvironment: string[];\n  validatedRuntime: Record<string, string>;\n  classMapping", "trainedEnvironment"),   # record -> array
])
def test_a1_negative_controls_mutated_types_are_caught(tmp_path, old, new, expect):
    source = TYPES_TS.read_text(encoding="utf-8")
    assert old in source, old
    mutated = tmp_path / "types.ts"
    mutated.write_text(source.replace(old, new, 1), encoding="utf-8")
    shapes = ts_shapes(mutated)
    problems = []
    for name in ROOTS:
        problems += diff(pydantic_shape(getattr(S, name)), shapes[name], name)
    assert any(expect in p for p in problems), (expect, problems)
