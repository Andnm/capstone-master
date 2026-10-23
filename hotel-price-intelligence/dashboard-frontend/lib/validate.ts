/**
 * Guard cấu trúc cho phản hồi API, SINH TỪ `types.ts` (xem `scripts/gen-validate-schema.mjs`) — không viết tay danh sách khóa nên không trôi so với hợp đồng.
 *
 * ĐÃ bảo vệ (mọi trường của hợp đồng ở MỌI tầng, kể cả phần tử trong mảng/record/object lồng): khóa bắt buộc có mặt (trừ trường `optional`), `null` chỉ ở trường nullable,
 * kiểu chuỗi / số hữu hạn / boolean, giá trị thuộc enum đã khai báo, mảng/object đúng loại; cộng độ dài cố định của các bộ số mà UI đọc theo vị trí (xem LENGTHS).
 * CHỦ Ý KHÔNG bảo vệ: ràng buộc nghiệp vụ (khoảng giá trị, thứ tự, duy nhất, nội dung chuỗi, sự nhất quán giữa các trường), khóa thừa không có trong hợp đồng, và tính đúng của số liệu.
 * Hợp đồng đầy đủ do pydantic ↔ `types.ts` được kiểm bằng test đệ quy phía backend; phản hồi sai cấu trúc ⇒ `bad_response` (khối lỗi + "Thử lại") thay vì làm vỡ render.
 */
import { SCHEMA } from "./validate-schema";

export type Endpoint = "config" | "hotels" | "forecast" | "hotelier" | "model-info";

type Base = { nullable: boolean; optional?: boolean };
export type Shape =
  | (Base & { kind: "string" | "number" | "boolean" })
  | (Base & { kind: "enum"; values: (string | number | boolean)[] })
  | (Base & { kind: "array"; item: Shape })
  | (Base & { kind: "record"; value: Shape })
  | (Base & { kind: "object"; props: Record<string, Shape> });

/** Bộ số đọc theo vị trí ở UI (`triple`, khoảng tin cậy): `number[]` trong hợp đồng nên phải ghim độ dài tại đây. Khóa = đường dẫn (`[]` = phần tử mảng). */
export const LENGTHS: Record<string, number> = {
  "horizons[].evidence.validationLiftPct": 3,
  "horizons[].evidence.testLiftPct": 3,
  "horizons[].routing[].ci95": 2,
};

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

function check(shape: Shape, value: unknown, path: string): boolean {
  if (value === null) return shape.nullable;
  switch (shape.kind) {
    case "string": return typeof value === "string";
    case "number": return typeof value === "number" && Number.isFinite(value);
    case "boolean": return typeof value === "boolean";
    case "enum": return shape.values.includes(value as string | number | boolean);
    case "array": {
      if (!Array.isArray(value)) return false;
      const fixed = LENGTHS[path];
      if (fixed !== undefined && value.length !== fixed) return false;
      return value.every((item) => check(shape.item, item, `${path}[]`));
    }
    case "record": return isObj(value) && Object.values(value).every((item) => check(shape.value, item, `${path}{}`));
    case "object": {
      if (!isObj(value)) return false;
      for (const [key, prop] of Object.entries(shape.props)) {
        if (!(key in value) || value[key] === undefined) {
          if (prop.optional) continue;
          return false;
        }
        if (!check(prop, value[key], path === "" ? key : `${path}.${key}`)) return false;
      }
      return true;
    }
  }
}

export function validateBody(endpoint: Endpoint, body: unknown): boolean {
  return isObj(body) && check(SCHEMA[endpoint], body, "");
}
