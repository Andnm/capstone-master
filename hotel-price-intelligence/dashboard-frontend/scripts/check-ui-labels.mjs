#!/usr/bin/env node
/**
 * Quét NHÃN HIỂN THỊ của frontend (LABEL_POLICY v3, test U6): chuỗi + JSX text trong app/, components/, lib/ — KHÔNG quét comment, tên biến hay bundle minify.
 * Danh sách cụm cấm PHẢI trùng backend `app/dashboard/labels.py::BANNED_PATTERNS` (có test backend so khớp hai danh sách).
 * Kèm negative control (--selftest chạy mặc định): scanner phải CHẶN "Nên đặt ngay" v.v. Thoát mã 1 nếu có vi phạm hoặc còn dấu vết đã gỡ.
 *
 *   node scripts/check-ui-labels.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

export const BANNED_PATTERNS = [
  "chinh xac", "do chinh xac", "dam bao", "toi uu", "nen (dat|mua|cho)", "dat ngay", "cho them", "mua ngay", "nhat dinh", "chac chan",
  "da (trien khai|duyet|kiem chung)", "production", "khong (overfit|ro ri|leak)", "tin cay (cao|thap|trung binh)", "confidence", "ai (du doan|biet)",
  "du lieu mau", "hu cau", "mock",
];
const BANNED = new RegExp("\\b(?:" + BANNED_PATTERNS.join("|") + ")\\b");
// Dấu vết của UI cũ (mock) phải KHÔNG còn trong chuỗi hiển thị / import.
const REMOVED_TRACES = ["Dữ liệu mẫu", "Nên đặt sớm", "Có thể chờ", "confidenceTier", "sold-out", "mock-data", "Đang mô phỏng", "Mô phỏng"];

export const fold = (text) => text.replaceAll("đ", "d").replaceAll("Đ", "d").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
export const bannedHits = (text) => [...new Set([...fold(text).matchAll(new RegExp(BANNED.source, "g"))].map((m) => m[0]))];

function collectStrings(sourceText, fileName) {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(fileName, sourceText, ts.ScriptTarget.ES2022, true, kind);
  const found = [];
  const visit = (node) => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      if (node.moduleSpecifier) found.push({ text: node.moduleSpecifier.text, import: true, node });
      return;
    }
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) found.push({ text: node.text, node });
    else if (ts.isJsxText(node) && node.text.trim()) found.push({ text: node.text.trim(), node });
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found.map((f) => ({ ...f, line: source.getLineAndCharacterOfPosition(f.node.getStart(source)).line + 1 }));
}

export function scanSource(sourceText, fileName) {
  const problems = [];
  for (const item of collectStrings(sourceText, fileName)) {
    if (!item.import) {
      const hits = bannedHits(item.text);
      if (hits.length) problems.push(`${fileName}:${item.line} cụm cấm [${hits.join(", ")}] trong "${item.text.slice(0, 80)}"`);
    }
    for (const trace of REMOVED_TRACES) if (item.text.includes(trace)) problems.push(`${fileName}:${item.line} còn dấu vết đã gỡ "${trace}"`);
  }
  return problems;
}

function selftest() {
  const must = ["Nên đặt ngay", "Chờ thêm sẽ rẻ hơn", "Giá chính xác 95%", "Mô hình đảm bảo không rò rỉ", "Đã triển khai", "Tin cậy cao", "AI dự đoán giá"];
  const allowed = ["Không phải khuyến nghị đặt phòng hay định giá", "Đây không phải trang đặt phòng và không cam kết tiết kiệm.", "không phải dự đoán ổn định của mô hình"];
  const bad = must.filter((t) => bannedHits(t).length === 0);
  const wrong = allowed.filter((t) => bannedHits(t).length > 0);
  const jsx = scanSource('export const A = () => <p>Nên đặt ngay</p>;\nconst x = "Dữ liệu mẫu";', "x.tsx");
  if (bad.length || wrong.length || jsx.length !== 3) {
    console.error("SELFTEST FAIL", { bad, wrong, jsx });
    process.exit(1);
  }
}

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".d.ts")) out.push(full);
  }
  return out;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  selftest();
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const files = ["app", "components", "lib"].flatMap((d) => walk(path.join(root, d)));
  const problems = files.flatMap((f) => scanSource(fs.readFileSync(f, "utf8"), path.relative(root, f)));
  if (problems.length) {
    console.error(problems.join("\n"));
    process.exit(1);
  }
  console.log(`check-ui-labels: OK (${files.length} file, selftest negative control đạt)`);
}
