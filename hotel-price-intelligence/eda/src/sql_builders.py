"""Bo sinh SQL THUAN (khong cham MySQL) cho cac aggregate bounded-memory (GPT review 12 eda file 11 muc 4:
"khong keo full observation frame chi de GROUP BY; observation-level chi duoc giu duoi dang sample audit
co gioi han").

Moi ham tra ve CHUOI SQL (+ tham so bind neu co) - khong thuc thi gi. Tach rieng de unit-test cau truc SQL
khong can MySQL; ket qua SO HOC duoc kiem chung rieng bang integration test tren fixture that
(`src/tests/test_sql_aggregates.py`, danh dau `mysql`).

Quantile dung NOI SUY TUYEN TINH (numpy/pandas default `linear`): vi tri `h = 1 + (n-1)*q` (1-based), gia tri
`x[floor(h)] + frac(h) * (x[floor(h)+1] - x[floor(h)])`. Khop chinh xac `Series.quantile(q)` cua pandas, nen
cac bang moi so sanh duoc voi ket qua cu/oracle numpy ma khong doi dinh nghia percentile.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

# Ten cot -> quantile (chuoi thap phan, KHONG float - SQL literal DECIMAL de floor() chinh xac).
STANDARD_QUANTILES: Mapping[str, str] = {
    "p1": "0.01", "p5": "0.05", "p50": "0.50", "p75": "0.75", "p95": "0.95", "p99": "0.99",
}
BOX_QUANTILES: Mapping[str, str] = {"p5": "0.05", "p25": "0.25", "p50": "0.50", "p75": "0.75", "p95": "0.95"}


def quantile_expr(q: str, *, value: str = "g.v", rank: str = "g.rn", count: str = "g.cnt") -> str:
    """Bieu thuc SQL (chay trong GROUP BY) tinh quantile `q` bang noi suy tuyen tinh tu 2 cot cua window:
    `rank` (ROW_NUMBER theo thu tu tang dan cua `value`) va `count` (so dong cua nhom)."""
    position = f"(1+({count}-1)*{q})"
    lower = f"MAX(CASE WHEN {rank} = FLOOR{position} THEN {value} END)"
    upper = f"MAX(CASE WHEN {rank} = LEAST({count}, FLOOR{position}+1) THEN {value} END)"
    fraction = f"((1+(MAX({count})-1)*{q}) - FLOOR(1+(MAX({count})-1)*{q}))"
    return f"({lower} + {fraction} * ({upper} - {lower}))"


def grouped_quantile_sql(
    *, value_expr: str, from_where: str, group_exprs: Mapping[str, str] | None = None,
    quantiles: Mapping[str, str] = STANDARD_QUANTILES, extra_aggregates: Mapping[str, str] | None = None,
    min_group_size: int = 1, order_by: str | None = None,
) -> str:
    """SELECT <group cols>, n_obs, min_price, max_price, mean_price, <quantile cols>, <extra> ... GROUP BY <group cols>.

    - `value_expr`: bieu thuc gia tri (vd `po.price_per_night`); `from_where`: FROM ... JOIN ... WHERE ... (chua
      cac placeholder `%s` cua chinh no - caller truyen tham so theo dung thu tu).
    - `group_exprs`: `{alias: sql_expr}` - rong => 1 dong tong the.
    - `extra_aggregates`: `{alias: expr_tren_g.v}` vd `{"std_price": "STDDEV_SAMP(g.v)"}`.
    - `min_group_size`: giu nhom co >= n dong (HAVING).
    3 tang subquery vi MySQL khong cho PARTITION BY tham chieu alias cung tang: k (tinh alias nhom + gia tri)
    -> g (window rn/cnt) -> ngoai (aggregate + quantile).
    """
    group_exprs = dict(group_exprs or {})
    aliases = list(group_exprs)
    inner_select = ", ".join([f"{expr} {alias}" for alias, expr in group_exprs.items()] + [f"{value_expr} v"])
    partition = f"PARTITION BY {', '.join(f'k.{a}' for a in aliases)} " if aliases else ""
    window_select = ", ".join(
        [f"k.{a}" for a in aliases]
        + ["k.v", f"ROW_NUMBER() OVER ({partition}ORDER BY k.v) rn", f"COUNT(*) OVER ({partition.strip()}) cnt"]
    )
    outer_select = ", ".join(
        [f"g.{a}" for a in aliases]
        + ["COUNT(*) n_obs", "MIN(g.v) min_price", "MAX(g.v) max_price", "AVG(g.v) mean_price"]
        + [f"{quantile_expr(q)} {name}" for name, q in quantiles.items()]
        + [f"{expr} {name}" for name, expr in (extra_aggregates or {}).items()]
    )
    group_by = f"GROUP BY {', '.join(f'g.{a}' for a in aliases)}" if aliases else ""
    having = f"HAVING COUNT(*) >= {int(min_group_size)}" if min_group_size > 1 else ""
    order = f"ORDER BY {order_by}" if order_by else (f"ORDER BY {', '.join(f'g.{a}' for a in aliases)}" if aliases else "")
    return (
        f"SELECT {outer_select}\n"
        f"FROM (\n  SELECT {window_select}\n  FROM (\n    SELECT {inner_select}\n    {from_where}\n  ) k\n) g\n"
        f"{group_by} {having} {order}"
    )


def inline_calendar_derived_table(calendar_rows: Sequence[Mapping[str, Any]], *, alias: str = "cal") -> tuple[str, list[Any]]:
    """Derived table inline (UNION ALL) tu cac dong (checkin_date, city, 4 co) cua `holidays.checkin_calendar_flags`
    de JOIN trong SQL ma KHONG can bang tam (ket noi la READ ONLY). Chi can truyen cac dong CO IT NHAT 1 co True -
    caller LEFT JOIN + COALESCE(co, 0) cho ngay/city con lai. Tra ve `(fragment, params)`, params theo thu tu
    placeholder. Rong => derived table 0 dong (van hop le de LEFT JOIN)."""
    flag_cols = ("is_public_holiday", "is_tet", "is_festival_period", "is_major_event")
    if not calendar_rows:
        empty = ", ".join(f"0 AS {c}" for c in flag_cols)
        return f"(SELECT CAST(NULL AS DATE) AS checkin_date, CAST(NULL AS CHAR(50)) AS city, {empty} FROM DUAL WHERE FALSE) {alias}", []
    selects: list[str] = []
    params: list[Any] = []
    for index, row in enumerate(calendar_rows):
        if index == 0:
            head = ", ".join(
                ["CAST(%s AS DATE) AS checkin_date", "%s AS city"] + [f"%s AS {c}" for c in flag_cols]
            )
        else:
            head = ", ".join(["CAST(%s AS DATE)", "%s"] + ["%s" for _ in flag_cols])
        selects.append(f"SELECT {head}")
        params.extend([row["checkin_date"], row["city"]] + [int(bool(row[c])) for c in flag_cols])
    return "(" + " UNION ALL ".join(selects) + f") {alias}", params
