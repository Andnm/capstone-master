#!/usr/bin/env node
/**
 * Test thuần cho logic UI không cần trình duyệt/React: transpile MÃ THẬT (`lib/*.ts`) bằng TypeScript compiler rồi chạy trong VM với fetch/timer GIẢ — không mạng, không backend, không dependency mới.
 * Phủ: trạng thái ô "Tín hiệu" (U1: thiếu dữ liệu ≠ không có tín hiệu), timeout khi KHÔNG có `AbortSignal.any` (U2), hủy chủ động, lỗi mạng/HTTP, guard cấu trúc (collection null ⇒ bad_response),
 * dọn timer, ngày VN quanh ranh 17:00 UTC, hiển thị % không làm đổi hướng.
 *
 *   node scripts/ui-pure-tests.mjs [--responses <thư mục chứa endpoint__nhãn.json do backend sinh từ API thật>]
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";
import { ENDPOINT_ROOTS, render as renderSchema } from "./gen-validate-schema.mjs";
import { shapesFor } from "./ts-shape-lib.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const nodeRequire = createRequire(path.join(root, "package.json"));
let assertions = 0;
const ok = (fn) => { assertions += 1; return fn(); };
const same = (a, b) => assert.equal(JSON.stringify(a), JSON.stringify(b));          // đối tượng sinh trong VM khác prototype: so theo JSON

function makeLoader(globals) {
  const context = vm.createContext(globals);
  const cache = new Map();
  const load = (rel) => {
    if (cache.has(rel)) return cache.get(rel).exports;
    const file = path.join(root, `${rel}.ts`);
    const { outputText } = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } });
    const mod = { exports: {} };
    cache.set(rel, mod);
    const dir = path.posix.dirname(rel);
    const req = (spec) => {
      if (spec === "@/types") return {};
      if (spec.startsWith("@/")) return load(spec.slice(2));
      if (spec.startsWith("./") || spec.startsWith("../")) return load(path.posix.normalize(path.posix.join(dir, spec)));
      return nodeRequire(spec);
    };
    vm.runInContext(`(function (exports, require, module) {${outputText}\n})`, context, { filename: file })(mod.exports, req, mod);
    return mod.exports;
  };
  return load;
}

function pendingTimers() {
  const live = new Set();
  return {
    setTimeout: (fn, ms) => { const id = setTimeout(() => { live.delete(id); fn(); }, ms); live.add(id); return id; },
    clearTimeout: (id) => { clearTimeout(id); live.delete(id); },
    live,
  };
}
const abortError = () => Object.assign(new Error("aborted"), { name: "AbortError" });
const jsonResponse = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

function makeApi(fetchImpl, { withAny = false } = {}) {
  const timers = pendingTimers();
  const signalGlobal = withAny ? AbortSignal : {};                                // KHÔNG có `any`/`timeout`: mô phỏng trình duyệt cũ
  const globals = { fetch: fetchImpl, AbortController, AbortSignal: signalGlobal, setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout, URLSearchParams, Intl, Date, Math, JSON, Number, String, Object, Array, Set, Map, Promise, Error, console };
  const load = makeLoader(globals);
  return { api: load("lib/api"), timers, load };
}

const hangUntilAbort = (url, init) => new Promise((_, reject) => {
  if (init.signal.aborted) return reject(abortError());                           // fetch thật từ chối ngay khi signal đã hủy
  init.signal.addEventListener("abort", () => reject(abortError()), { once: true });
});

async function apiTests() {
  // 1) thành công + dọn timer
  {
    const body = { snapshot: { snapshotId: "s", dataAsOf: "2026-10-04", generatedAt: "2026-10-23T00:00:00Z", claimLevel: "dev_research_exploratory", datasetVersions: { h1: "ds1" }, label: "l" }, horizons: [1, 3], classifier: { available: false, reason: "dependency_missing" }, unsupportedHorizons: [{ horizon: 7, reason: "model_not_available" }], limits: { minGroupSize: 5, positionAlertPct: 10, marketAlertPct: 5 } };
    const { api, timers } = makeApi(async () => jsonResponse(200, body));
    const out = await api.fetchEndpoint("config", {}, undefined, 5000);
    ok(() => assert.equal(out.horizons.length, 2));
    ok(() => assert.equal(timers.live.size, 0));
  }
  // 2) U2: không có AbortSignal.any ⇒ timeout vẫn hoạt động khi caller TRUYỀN signal
  {
    const { api, timers } = makeApi(hangUntilAbort);
    const controller = new AbortController();
    const started = Date.now();
    await assert.rejects(() => api.fetchEndpoint("forecast", { hotelId: "a", checkIn: "2026-10-12" }, controller.signal, 40), (e) => e.name === "ApiError" && e.code === "timeout");
    assertions += 1;
    ok(() => assert.ok(Date.now() - started < 2000));
    ok(() => assert.equal(timers.live.size, 0));
  }
  // 3) hủy chủ động: không biến thành ApiError, timer đã dọn
  {
    const { api, timers } = makeApi(hangUntilAbort);
    const controller = new AbortController();
    const pending = api.fetchEndpoint("hotels", {}, controller.signal, 5000);
    controller.abort();
    await assert.rejects(() => pending, (e) => e.name === "AbortError");
    assertions += 1;
    ok(() => assert.equal(timers.live.size, 0));
  }
  // 4) signal đã bị hủy từ trước
  {
    const { api } = makeApi(hangUntilAbort);
    const controller = new AbortController();
    controller.abort();
    await assert.rejects(() => api.fetchEndpoint("hotels", {}, controller.signal, 5000), (e) => e.name === "AbortError");
    assertions += 1;
  }
  // 5) lỗi mạng / HTTP / JSON hỏng
  {
    const net = makeApi(async () => { throw new TypeError("failed to fetch"); });
    await assert.rejects(() => net.api.fetchEndpoint("hotels", {}, undefined, 1000), (e) => e.code === "network_error" && e.status === 0);
    assertions += 1;
    const http = makeApi(async () => jsonResponse(503, { error: { code: "snapshot_unavailable", message: "Dữ liệu dự báo chưa sẵn sàng." } }));
    await assert.rejects(() => http.api.fetchEndpoint("config", {}, undefined, 1000), (e) => e.code === "snapshot_unavailable" && e.status === 503 && e.message.includes("chưa sẵn sàng"));
    assertions += 1;
    const html = makeApi(async () => ({ ok: false, status: 502, json: async () => { throw new SyntaxError("Unexpected token <"); } }));
    await assert.rejects(() => html.api.fetchEndpoint("config", {}, undefined, 1000), (e) => e.code === "bad_response");
    assertions += 1;
  }
  // 6) U2: guard cấu trúc — collection null ⇒ bad_response (không để vỡ render)
  {
    for (const [endpoint, body] of [["hotels", { snapshotId: "x", hotels: null }], ["hotels", { snapshotId: "x", hotels: [{ id: "a", name: "A", city: "Hà Nội", reviewScore: null, checkIns: null }] }],
      ["config", { snapshot: null, horizons: [], classifier: {}, unsupportedHorizons: [], limits: {} }], ["forecast", { predictions: null, history: [], supportedHorizons: [], currentPrice: 1 }]]) {
      const { api, timers } = makeApi(async () => jsonResponse(200, body));
      await assert.rejects(() => api.fetchEndpoint(endpoint, {}, undefined, 1000), (e) => e.code === "bad_response");
      assertions += 1;
      ok(() => assert.equal(timers.live.size, 0));
    }
  }
}

function pureTests() {
  const { load } = makeApi(async () => jsonResponse(200, {}));
  // guard sinh ra từ types.ts phải TƯƠI (không ai sửa tay / types.ts đổi mà quên sinh lại)
  ok(() => assert.equal(fs.readFileSync(path.join(root, "lib", "validate-schema.ts"), "utf8"), renderSchema(path.join(root, "types.ts")), "lib/validate-schema.ts lỗi thời: node scripts/gen-validate-schema.mjs"));
  const view = load("lib/hotelier-view");
  const labels = load("lib/labels").L;
  const sufficient = { groupSummary: { insufficient: false, reason: null }, alerts: [] };
  ok(() => same(view.alertsPanel(sufficient), { kind: "evaluated_none", badge: "0", message: labels.noAlerts }));
  ok(() => assert.equal(view.alertsPanel({ groupSummary: { insufficient: false, reason: null }, alerts: [{ id: "a" }, { id: "b" }] }).badge, "2"));
  ok(() => assert.equal(view.alertsPanel({ groupSummary: { insufficient: false, reason: null }, alerts: [{ id: "a" }] }).message, null));
  const few = view.alertsPanel({ groupSummary: { insufficient: true, reason: "group_too_small" }, alerts: [] });
  ok(() => same(few, { kind: "not_evaluated", badge: "—", message: labels.alertsNotEvaluated }));
  const noReview = view.alertsPanel({ groupSummary: { insufficient: true, reason: "anchor_review_missing" }, alerts: [] });
  ok(() => same(noReview, { kind: "not_evaluated", badge: "—", message: labels.alertsNotEvaluatedNoReview }));
  ok(() => assert.notEqual(few.message, labels.noAlerts));                         // U1: thiếu dữ liệu KHÔNG được trình bày là "không có tín hiệu"
  ok(() => assert.ok(!/không có tín hiệu/i.test(few.message) && !/không có tín hiệu/i.test(noReview.message)));

  const format = load("lib/format");
  ok(() => assert.equal(format.daysSince("2026-10-04", new Date("2026-10-22T16:59:59Z")), 18));          // 23:59:59 giờ VN ngày 22/10
  ok(() => assert.equal(format.daysSince("2026-10-04", new Date("2026-10-22T17:00:00Z")), 19));          // 00:00 giờ VN ngày 23/10
  ok(() => assert.equal(format.vnDateOf("2026-10-03T18:46:48Z"), "2026-10-04"));
  ok(() => assert.equal(format.shortDate("2026-10-04"), "04/10"));
  ok(() => assert.equal(format.pct(2.0004), "+2,0%"));                                                  // hiển thị làm tròn KHÔNG quyết định hướng (hướng do API)
  ok(() => assert.equal(format.pct(-0.04), "0,0%"));
  ok(() => assert.equal(format.spanText(null), null));
  ok(() => assert.equal(format.reviewText(null), "—"));
  const lbl = load("lib/labels");
  ok(() => assert.ok(lbl.stalenessBanner("2026-10-04", 19).includes("cách hôm nay 19 ngày")));
  ok(() => assert.equal(lbl.directionPlain("up"), "Có thể tăng"));
  ok(() => assert.ok(lbl.signalSentences([{ horizon: 1, direction: "stable", expectedPctChange: 0 }])[0].includes("ít biến động")));
}

const RENDER_CRITICAL = {
  config: ["snapshot", "horizons", "classifier", "unsupportedHorizons", "limits"],
  hotels: ["hotels"],
  forecast: ["hotel", "roomSummary", "supportedHorizons", "unsupportedHorizons", "predictions", "classifier", "warnings", "history"],
  hotelier: ["anchor", "compset", "groupSummary", "checkInPrices", "alerts", "warnings"],
  "model-info": ["snapshot", "horizons", "classifier", "rules", "notes", "unsupportedHorizons"],
};

const isObject = (v) => typeof v === "object" && v !== null && !Array.isArray(v);
const clone = (v) => JSON.parse(JSON.stringify(v));
const getAt = (rootValue, trail) => trail.reduce((node, key) => node[key], rootValue);
const childShape = (shape, key) => (shape.kind === "object" ? shape.props[key] : shape.kind === "array" ? shape.item : shape.value);

/** Duyệt MỌI nút của phản hồi thật song song với hình dạng chuẩn (từ TypeScript compiler, KHÔNG phải từ guard); mảng/record chỉ lấy phần tử đầu và cuối. `shape === null` ⇒ khóa ngoài hợp đồng. */
function visit(value, shape, trail, fn) {
  fn(trail, shape, value);
  if (value === null) return;
  if (shape.kind === "object" && isObject(value)) {
    for (const [key, child] of Object.entries(value)) {
      if (!(key in shape.props)) { fn([...trail, key], null, child); continue; }
      visit(child, shape.props[key], [...trail, key], fn);
    }
  } else if (shape.kind === "array" && Array.isArray(value)) {
    for (const i of new Set(value.length ? [0, value.length - 1] : [])) visit(value[i], shape.item, [...trail, i], fn);
  } else if (shape.kind === "record" && isObject(value)) {
    for (const key of Object.keys(value).slice(0, 2)) visit(value[key], shape.value, [...trail, key], fn);
  }
}

const wrongTypeFor = (shape) => ({ string: 42, number: "1", boolean: "true", enum: "__bad__", array: {}, object: [], record: [] })[shape.kind];

function responseTests(dir, validateOverride) {
  const { load } = makeApi(async () => jsonResponse(200, {}));
  const validateBody = validateOverride ?? load("lib/validate").validateBody;
  const files = fs.readdirSync(dir).filter((f) => f.endsWith(".json")).sort();
  assert.ok(files.length >= 8, `cần >= 8 phản hồi mẫu từ API thật, có ${files.length}`);

  const oracle = shapesFor(path.join(root, "types.ts"), Object.values(ENDPOINT_ROOTS));

  let mutations = 0;
  for (const file of files) {
    const endpoint = file.split("__")[0];
    const body = JSON.parse(fs.readFileSync(path.join(dir, file), "utf8"));
    const shape = oracle[ENDPOINT_ROOTS[endpoint]];
    ok(() => assert.equal(validateBody(endpoint, body), true, `${file} phải qua guard`));
    // các khóa CẦN CHO RENDER đặt thành null / bỏ đi ⇒ phải bị từ chối
    for (const key of RENDER_CRITICAL[endpoint]) {
      ok(() => assert.equal(validateBody(endpoint, { ...body, [key]: null }), false, `${file}: ${key}=null phải bị từ chối`));
      ok(() => assert.equal(validateBody(endpoint, Object.fromEntries(Object.entries(body).filter(([k]) => k !== key))), false, `${file}: thiếu ${key} phải bị từ chối`));
    }
    // PHỦ: mọi khóa có mặt trong phản hồi thật đều thuộc hợp đồng (không có trường nào chạy ngoài guard)
    const extras = [];
    const nodes = [];
    visit(body, shape, [], (trail, node) => {
      if (node === null) extras.push(trail.join("."));
      else if (trail.length) nodes.push({ trail, node });
    });
    ok(() => assert.deepEqual(extras, [], `${file}: khóa ngoài hợp đồng ${extras}`));
    // ĐỘT BIẾN MỌI NÚT (kể cả trong object/mảng lồng): null / bỏ khóa / sai kiểu; kỳ vọng lấy từ hình dạng chuẩn
    for (const { trail, node } of nodes) {
      const parentTrail = trail.slice(0, -1);
      const key = trail[trail.length - 1];
      const label = `${file}:${trail.join(".")}`;
      if (getAt(body, trail) !== null) {
        const asNull = clone(body);
        getAt(asNull, parentTrail)[key] = null;
        mutations += 1;
        assert.equal(validateBody(endpoint, asNull), node.nullable, `${label}: null ${node.nullable ? "được phép" : "phải bị từ chối"}`);
      }
      let parentShape = shape;
      for (const k of parentTrail) parentShape = childShape(parentShape, k);
      if (parentShape.kind === "object") {
        const removed = clone(body);
        delete getAt(removed, parentTrail)[key];
        mutations += 1;
        assert.equal(validateBody(endpoint, removed), Boolean(node.optional), `${label}: bỏ khóa ${node.optional ? "được phép (optional)" : "phải bị từ chối"}`);
      }
      const wrong = clone(body);
      getAt(wrong, parentTrail)[key] = wrongTypeFor(node);
      mutations += 1;
      assert.equal(validateBody(endpoint, wrong), false, `${label}: sai kiểu (${node.kind}) phải bị từ chối`);
    }
    assertions += 1;
  }
  assertions += mutations;
  assert.ok(mutations > 1500, `số đột biến quá ít: ${mutations}`);

  // Phản ví dụ cụ thể của GPT (file 16 §3) + độ dài bộ số + child phân loại khi available=true
  const pick = (prefix, variant) => JSON.parse(fs.readFileSync(path.join(dir, files.find((f) => f.startsWith(prefix) && f.includes(variant))), "utf8"));
  const forecast = pick("forecast__", "plain_normal");
  const noArea = clone(forecast); delete noArea.roomSummary.areaM2;
  ok(() => assert.equal(validateBody("forecast", noArea), false));
  const info = pick("model-info__", "plain");
  const noDatasets = clone(info); delete noDatasets.snapshot.datasetVersions;
  ok(() => assert.equal(validateBody("model-info", noDatasets), false));
  const nullDatasets = clone(info); nullDatasets.snapshot.datasetVersions = null;
  ok(() => assert.equal(validateBody("model-info", nullDatasets), false));
  for (const field of ["validationLiftPct", "testLiftPct"]) {
    for (const bad of [[], [1, 2], [1, 2, 3, 4], [1, 2, "3"]]) {
      const mutated = clone(info); mutated.horizons[0].evidence[field] = bad;
      ok(() => assert.equal(validateBody("model-info", mutated), false, `${field}=${JSON.stringify(bad)}`));
    }
  }
  const badCi = clone(info); badCi.horizons[0].routing[0].ci95 = [0.1];
  ok(() => assert.equal(validateBody("model-info", badCi), false));
  const withClassifier = pick("model-info__", "classifier");
  ok(() => assert.ok(withClassifier.classifier.available && withClassifier.classifier.horizons.length === 2, "fixture classifier phải có child thật"));
  for (const field of ["coverage", "compatibility", "classMapping", "limitations", "bundleSha256", "trainedEnvironment", "validatedRuntime"]) {
    const missing = clone(withClassifier); delete missing.classifier.horizons[1][field];
    ok(() => assert.equal(validateBody("model-info", missing), false, `child classifier thiếu ${field}`));
  }
  const nested = clone(withClassifier); delete nested.classifier.horizons[0].compatibility.maxAbsProbabilityDiff;
  ok(() => assert.equal(validateBody("model-info", nested), false));
  const emptyChildren = clone(withClassifier); emptyChildren.classifier.horizons = [];
  ok(() => assert.equal(validateBody("model-info", emptyChildren), true));             // mảng rỗng hợp lệ về cấu trúc; có/không child do hợp đồng backend (test API) chứ không do guard
}

const args = process.argv.slice(2);
await apiTests();
pureTests();
const idx = args.indexOf("--responses");
if (idx >= 0) {
  const dir = path.resolve(args[idx + 1]);
  responseTests(dir);
  // đối chứng ÂM của chính bộ đột biến: một guard chỉ kiểm khóa cấp trên (kiểu cũ, GPT 16 §3) PHẢI bị bắt — nếu không, bộ test này vô hiệu
  const topLevelOnly = (endpoint, body) => isObject(body) && RENDER_CRITICAL[endpoint].every((k) => body[k] !== null && body[k] !== undefined);
  assert.throws(() => responseTests(dir, topLevelOnly), assert.AssertionError);
  assertions += 1;
}
console.log(`ui-pure-tests: OK (${assertions} assertions${idx >= 0 ? ", gồm phản hồi API thật" : ""})`);
