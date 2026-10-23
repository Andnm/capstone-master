/**
 * Trích HÌNH DẠNG ĐỆ QUY của các root trong `types.ts` bằng TypeScript compiler thật (không regex tên interface):
 * object{props{optional,nullable,type}} | array{item} | record{value} | string | number | boolean | enum{values}.
 * Dùng chung bởi `ts-contract-shape.mjs` (so với pydantic ở backend) và `gen-validate-schema.mjs` (sinh guard runtime) — MỘT nguồn sự thật là `types.ts`.
 */
import ts from "typescript";

export function shapesFor(typesPath, roots) {
  const program = ts.createProgram([typesPath], { strict: true, noEmit: true, target: ts.ScriptTarget.ES2022, skipLibCheck: true, types: [] });
  const checker = program.getTypeChecker();
  const source = program.getSourceFile(typesPath);
  if (!source) throw new Error(`không đọc được ${typesPath}`);

  function stripNullable(type) {
    if (!type.isUnion()) return { nullable: false, type };
    const kept = type.types.filter((t) => !(t.flags & ts.TypeFlags.Null) && !(t.flags & ts.TypeFlags.Undefined));
    const nullable = type.types.some((t) => t.flags & ts.TypeFlags.Null);
    if (kept.length === 1) return { nullable, type: kept[0] };
    return { nullable, type, kept };
  }

  function describe(type, depth = 0) {
    if (depth > 12) throw new Error("đệ quy quá sâu");
    const { nullable, type: inner, kept } = stripNullable(type);
    const out = (shape) => ({ nullable, ...shape });
    const members = kept ?? (inner.isUnion() ? inner.types : [inner]);
    if (members.length > 1 || inner.isUnion()) {
      const list = members;
      if (list.every((t) => t.flags & ts.TypeFlags.BooleanLiteral)) return list.length === 2 ? out({ kind: "boolean" }) : out({ kind: "enum", values: [list[0].intrinsicName === "true"] });
      if (list.every((t) => t.isStringLiteral())) return out({ kind: "enum", values: list.map((t) => t.value).sort() });
      if (list.every((t) => t.isNumberLiteral())) return out({ kind: "enum", values: list.map((t) => t.value).sort((a, b) => a - b) });
      throw new Error(`union không hỗ trợ: ${checker.typeToString(type)}`);
    }
    if (inner.flags & ts.TypeFlags.BooleanLiteral) return out({ kind: "enum", values: [inner.intrinsicName === "true"] });
    if (inner.flags & ts.TypeFlags.Boolean) return out({ kind: "boolean" });
    if (inner.flags & ts.TypeFlags.String) return out({ kind: "string" });
    if (inner.flags & ts.TypeFlags.Number) return out({ kind: "number" });
    if (inner.isStringLiteral()) return out({ kind: "enum", values: [inner.value] });
    if (inner.isNumberLiteral()) return out({ kind: "enum", values: [inner.value] });
    if (checker.isArrayType(inner)) return out({ kind: "array", item: describe(checker.getTypeArguments(inner)[0], depth + 1) });
    const props = checker.getPropertiesOfType(inner);
    const stringIndex = checker.getIndexInfosOfType(inner).find((i) => i.keyType.flags & ts.TypeFlags.String);
    if (props.length === 0 && stringIndex) return out({ kind: "record", value: describe(stringIndex.type, depth + 1) });
    const result = {};
    for (const prop of props) {
      const decl = prop.valueDeclaration ?? prop.declarations?.[0];
      const propType = checker.getTypeOfSymbolAtLocation(prop, decl);
      result[prop.getName()] = { optional: Boolean(prop.flags & ts.SymbolFlags.Optional), ...describe(propType, depth + 1) };
    }
    return out({ kind: "object", props: result });
  }

  const output = {};
  for (const name of roots) {
    const decl = source.statements.find((s) => (ts.isInterfaceDeclaration(s) || ts.isTypeAliasDeclaration(s)) && s.name.text === name);
    if (!decl) throw new Error(`không có ${name} trong ${typesPath}`);
    output[name] = describe(checker.getTypeAtLocation(decl));
  }
  return output;
}
