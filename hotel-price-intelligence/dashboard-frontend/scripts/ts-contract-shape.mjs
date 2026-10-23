#!/usr/bin/env node
/**
 * Xuất HÌNH DẠNG ĐỆ QUY của các root trong `types.ts` (xem `ts-shape-lib.mjs`). Dùng bởi `backend/tests/test_dashboard_contract_ts.py` để so với JSON schema của pydantic
 * (khóa/required/nullable/kiểu ở MỌI tầng, kể cả inline/array/record).
 *
 *   node scripts/ts-contract-shape.mjs [đường-dẫn-types.ts] Root1 Root2 ...
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { shapesFor } from "./ts-shape-lib.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const typesPath = args[0] && args[0].endsWith(".ts") ? path.resolve(args.shift()) : path.resolve(here, "..", "types.ts");
process.stdout.write(JSON.stringify(shapesFor(typesPath, args)));
