#!/usr/bin/env node
/**
 * Sinh `lib/validate-schema.ts` (guard cấu trúc runtime) TỪ `types.ts` bằng TypeScript compiler — một nguồn sự thật duy nhất, không viết tay danh sách khóa.
 * `types.ts` đã được so với pydantic ở MỌI tầng bởi `backend/tests/test_dashboard_contract_ts.py`, nên guard phủ đủ mọi trường hợp đồng.
 *
 *   node scripts/gen-validate-schema.mjs            # ghi lib/validate-schema.ts
 *   node scripts/gen-validate-schema.mjs --check    # chỉ so với file hiện có (mã thoát 1 nếu lỗi thời)
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { shapesFor } from "./ts-shape-lib.mjs";

export const ENDPOINT_ROOTS = { config: "DashboardConfig", hotels: "HotelsResponse", forecast: "ForecastResponse", hotelier: "HotelierResponse", "model-info": "ModelInfoResponse" };

export function render(typesPath) {
  const shapes = shapesFor(typesPath, Object.values(ENDPOINT_ROOTS));
  const schema = Object.fromEntries(Object.entries(ENDPOINT_ROOTS).map(([endpoint, root]) => [endpoint, shapes[root]]));
  return `// GENERATED bởi scripts/gen-validate-schema.mjs từ types.ts — KHÔNG sửa tay. Kiểm độ tươi: \`npm run test:ui\` hoặc \`node scripts/gen-validate-schema.mjs --check\`.
import type { Shape } from "./validate";

export const SCHEMA: Record<string, Shape> = ${JSON.stringify(schema, null, 1)};
`;
}

const here = path.dirname(fileURLToPath(import.meta.url));
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const target = path.resolve(here, "..", "lib", "validate-schema.ts");
  const text = render(path.resolve(here, "..", "types.ts"));
  if (process.argv.includes("--check")) {
    const current = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
    if (current !== text) {
      console.error("lib/validate-schema.ts lỗi thời so với types.ts: chạy `node scripts/gen-validate-schema.mjs`");
      process.exit(1);
    }
    console.log("validate-schema: tươi");
  } else {
    fs.writeFileSync(target, text, "utf8");
    console.log(`đã ghi ${target}`);
  }
}
