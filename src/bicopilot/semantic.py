"""Semantic layer: metric/dimension definitions, field mapping, ambiguity detection, and a
deterministic SQL compiler.

Two jobs:
1. `resolve(question)` maps natural-language phrases to governed metric and dimension ids
   (field mapping / indicator definition) and flags ambiguous phrases that need a clarifying question.
2. `compile_sql(plan)` turns a structured Plan into SQL from the YAML definitions alone. The LLM
   SQL writer receives this as a draft and refines it; with no LLM (fake provider) it is the answer.
   Because every metric expression comes from metrics.yaml, "revenue" can never silently include
   cancelled orders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class Filter(BaseModel):
    dimension: str
    op: Literal["=", "!=", "in"] = "="
    value: str | list[str]


class TimeFilter(BaseModel):
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    year_month_from: str | None = None  # 'YYYY-MM'
    year_month_to: str | None = None
    label: str = ""  # how the question phrased it, e.g. "last quarter"


class Plan(BaseModel):
    """Output of the planner agent (Plan-and-Execute)."""

    metrics: list[str] = Field(default_factory=list, description="metric ids from the semantic layer")
    dimensions: list[str] = Field(default_factory=list, description="dimension ids to group by")
    filters: list[Filter] = Field(default_factory=list)
    time: TimeFilter | None = None
    top_n: int | None = None
    order: Literal["desc", "asc"] = "desc"
    compare: Literal["yoy", "qoq"] | None = None
    needs_clarification: bool = False
    clarification: str = ""
    options: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list, description="execution steps the supervisor will follow")
    unsupported_reason: str = ""


@dataclass
class Resolution:
    metrics: list[str] = field(default_factory=list)
    residual: list[str] = field(default_factory=list)  # question words the resolver could not account for
    dimensions: list[str] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    time: TimeFilter | None = None
    top_n: int | None = None
    order: str = "desc"
    compare: str | None = None
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    matched_phrases: dict[str, str] = field(default_factory=dict)


_WORD = r"(?<![a-z0-9]){}(?![a-z0-9])"
TIME_PHRASES = [
    r"\b(last|previous|prior|this|current)\s+(fiscal\s+)?(quarter|year)\b",
    r"\bfy\s?(20)?\d{2}\b",
    r"\b(fiscal\s+)?q[1-4]\b",
    r"\bytd\b|\byear to date\b",
    r"\b(last|past|trailing)\s+(12|twelve)\s+months\b",
    r"\b(calendar\s+)?20\d{2}(-(0[1-9]|1[0-2]))?\b",
    r"\b(last|previous|prior|this|current)\s+month\b",
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+20\d{2}\b",
    r"\b(yoy|qoq|year[- ]over[- ]year|quarter[- ]over[- ]quarter)\b",
    r"\b(vs\.?|versus|compared (to|with)|against)\s+(last|previous|prior)\s+(year|quarter)\b",
]
MONTHS = {
    m.lower(): i
    for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)
}
MONTHS_FULL = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        1,
    )
}


class SemanticLayer:
    def __init__(self, spec: dict[str, Any]):
        self.spec = spec
        self.metrics: dict[str, dict] = spec["metrics"]
        self.dimensions: dict[str, dict] = spec["dimensions"]
        self.tables: dict[str, dict] = spec["tables"]
        self.rules: list[dict] = spec.get("rules", [])
        self.ambiguous: dict[str, list[str]] = spec.get("ambiguous", {})
        self.fy_start = 1 if spec.get("calendar") == "calendar" else int(spec.get("fiscal_year_start_month", 4))
        self.calendar = self.fy_start == 1
        self.year_col, self.quarter_col = (
            ("d.year", "d.quarter") if self.calendar else ("d.fiscal_year", "d.fiscal_quarter")
        )
        self.bases: dict[str, dict] = spec.get("bases", {})
        self.as_of_default: str = str(spec.get("as_of_default", ""))
        self.spec_dir: Path = Path(".")
        self._metric_phrases = self._phrase_index(self.metrics)
        self._dimension_phrases = self._phrase_index(self.dimensions)
        self._value_index = self._build_value_index()

    # ----- loading ---------------------------------------------------------------------------
    @classmethod
    def load(cls, semantic_dir: Path) -> SemanticLayer:
        layer = cls(yaml.safe_load((semantic_dir / "metrics.yaml").read_text(encoding="utf-8")))
        layer.spec_dir = Path(semantic_dir)
        return layer

    def _phrase_index(self, items: dict[str, dict]) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for key, item in items.items():
            phrases = {
                key.replace("_", " "),
                item.get("label", "").lower(),
                *[a.lower() for a in item.get("aliases", [])],
            }
            for p in list(phrases):  # plural forms: rep -> reps, industry -> industries, country -> countries
                if p and not p.endswith(("s", "%")):
                    phrases.add(p[:-1] + "ies" if p.endswith("y") and not p.endswith("ay") else p + "s")
            pairs.extend((p, key) for p in phrases if p)
        return sorted(pairs, key=lambda p: -len(p[0]))  # longest phrase first

    def _build_value_index(self) -> list[tuple[str, str, str]]:
        idx = []
        for dim, item in self.dimensions.items():
            for v in item.get("values", []):
                idx.append((str(v).lower(), dim, str(v)))
        return sorted(idx, key=lambda p: -len(p[0]))

    # ----- rendering for prompts -------------------------------------------------------------
    def rules_text(self) -> str:
        return "\n".join(f"- {r['text']}" for r in self.rules)

    def metric_card(self, metric_id: str) -> str:
        m = self.metrics[metric_id]
        filt = " AND ".join(m.get("filters", [])) or "none"
        return f"{metric_id} ({m['label']}): {m['sql']}; required filter: {filt}; {m['description']}"

    def dimension_card(self, dim_id: str) -> str:
        d = self.dimensions[dim_id]
        values = f"; values: {', '.join(map(str, d['values']))}" if d.get("values") else ""
        return f"{dim_id}: column {d['column']} (table {d['table']}){values}"

    def aliases_text(self) -> str:
        parts = []
        for base, spec in self.bases.items():
            parts.append(f"{base} {spec['alias']}")
            for tbl, join in spec.get("joins", {}).items():
                alias = join.split(" ON ")[0].split()[-1]
                if f"{tbl} {alias}" not in parts:
                    parts.append(f"{tbl} {alias}")
        return ", ".join(parts) or "fact_orders o, dim_date d, dim_customer c, dim_product p"

    def catalog_text(self) -> str:
        metrics = "\n".join(f"- {self.metric_card(m)}" for m in self.metrics)
        dims = "\n".join(f"- {self.dimension_card(d)}" for d in self.dimensions)
        return f"METRICS\n{metrics}\n\nDIMENSIONS\n{dims}"

    # ----- fiscal calendar -------------------------------------------------------------------
    def fiscal_of(self, d: date) -> tuple[int, int]:
        """(period year, period quarter) for a date: calendar when the layer says so, else fiscal."""
        if self.calendar:
            return d.year, (d.month - 1) // 3 + 1
        fy = d.year + 1 if d.month >= self.fy_start else d.year
        fq = ((d.month - self.fy_start) % 12) // 3 + 1
        return fy, fq

    def period_label(self, year: int | None, quarter: int | None = None) -> str:
        if self.calendar:
            return (f"{year}" if year else "") + (f" Q{quarter}" if quarter else "")
        return (f"FY{year}" if year else "") + (f" Q{quarter}" if quarter else "")

    def previous_quarter(self, fy: int, fq: int) -> tuple[int, int]:
        return (fy, fq - 1) if fq > 1 else (fy - 1, 4)

    def method_detail(self, plan: Plan) -> dict[str, Any]:
        """How a plan's figure is computed, straight from the semantic layer: definition, formula, mandatory
        filters, the period it resolved to, the grouping and the question's own filters. Deterministic, so a
        business reader can audit the number even when the model is unavailable."""
        metrics = []
        for mid in plan.metrics:
            m = self.metrics.get(mid)
            if not m:
                continue
            metrics.append(
                {
                    "id": mid,
                    "label": m.get("label", mid),
                    "definition": m.get("description", ""),
                    "formula": m.get("sql", ""),
                    "base": m.get("base", ""),
                    "filters": list(m.get("filters", [])),
                    "format": m.get("format", "number"),
                }
            )
        period = "all dates in the data"
        if plan.time:
            t = plan.time
            if t.year_month_from or t.year_month_to:
                period = f"{t.year_month_from or 'start'} to {t.year_month_to or 'end'}"
            elif t.fiscal_year:
                period = self.period_label(t.fiscal_year, t.fiscal_quarter).strip()
            if t.label and t.label not in period:
                period = f"{period} (“{t.label}”)"
        if plan.compare:
            period += (
                "; compared with the same window one " + ("year" if plan.compare == "yoy" else "quarter") + " earlier"
            )
        return {
            "metrics": metrics,
            "period": period,
            "grouping": [
                self.dimensions.get(d, {}).get("label", d.replace("_", " "))
                if isinstance(self.dimensions.get(d), dict)
                else d
                for d in plan.dimensions
            ],
            "filters": [
                f"{f.dimension.replace('_', ' ')} {f.op} {f.value if isinstance(f.value, str) else ', '.join(f.value)}"
                for f in plan.filters
            ],
            "top_n": plan.top_n,
            "order": plan.order,
            "calendar": "calendar periods" if self.calendar else "fiscal periods",
            "as_of": self.as_of_default,
        }

    # ----- field mapping ---------------------------------------------------------------------
    def resolve(self, question: str, as_of: date) -> Resolution:
        q = " " + re.sub(r"\s+", " ", question.lower().strip()) + " "
        res = Resolution()
        res.time = self._parse_time(q, as_of)
        consumed = q
        # time phrases are parsed separately; strip them so "last quarter" / "this year" do not match
        # the fiscal_quarter / fiscal_year dimension aliases ("by quarter" and "monthly" still do)
        for pat in TIME_PHRASES:
            consumed = re.sub(pat, " ", consumed)

        # numeric value filters first ("review score 5", "5 stars") so the dimension words are consumed before
        # metric aliases such as "review score" can claim them
        for value_lc, dim, value in self._value_index:
            if not value_lc.isdigit():
                continue
            dim_words = [
                dim.replace("_", " "),
                *[a.lower() for a in self.dimensions[dim].get("aliases", [])],
                "star",
                "stars",
            ]
            near = "|".join(re.escape(w) for w in dim_words)
            pat = rf"(?:(?:{near})\s*(?:of|=|:)?\s*{value_lc}\b|\b{value_lc}\s*[- ]?(?:{near}))"
            if re.search(pat, consumed):
                res.filters.append(Filter(dimension=dim, value=value))
                consumed = re.sub(pat, " ", consumed)

        for phrase, key in self._metric_phrases:
            if key in res.metrics:
                continue
            if re.search(_WORD.format(re.escape(phrase)), consumed):
                res.metrics.append(key)
                res.matched_phrases[key] = phrase
                consumed = re.sub(_WORD.format(re.escape(phrase)), " ", consumed)

        # ambiguity: a phrase still present after metric matching, with none of its candidate metrics matched
        for phrase, key in self.ambiguous.items():
            if re.search(_WORD.format(re.escape(phrase)), consumed) and not any(k in res.metrics for k in key):
                res.ambiguous[phrase] = list(key)

        for phrase, key in self._dimension_phrases:
            if key in res.dimensions:
                continue
            if re.search(_WORD.format(re.escape(phrase)), consumed):
                res.dimensions.append(key)
                res.matched_phrases[key] = phrase
                consumed = re.sub(_WORD.format(re.escape(phrase)), " ", consumed)

        for value_lc, dim, value in self._value_index:
            if value_lc.isdigit():
                continue  # handled above
            if len(value_lc) <= 2:
                # two-letter codes (state UFs): require an uppercase token in the original question or a location cue
                upper_hit = re.search(_WORD.format(re.escape(value)), question) is not None and value.isupper()
                cue = re.search(rf"\b(in|from|for|state|uf)\s+{re.escape(value_lc)}\b", consumed) is not None
                if not (upper_hit or cue):
                    continue
            if re.search(_WORD.format(re.escape(value_lc)), consumed):
                res.filters.append(Filter(dimension=dim, value=value))
                consumed = re.sub(_WORD.format(re.escape(value_lc)), " ", consumed)

        # a filter on a dimension removes it from group-by unless the question says "by <dim>"
        for f in res.filters:
            phrase = re.escape(res.matched_phrases.get(f.dimension, f.dimension))
            if f.dimension in res.dimensions and not re.search(rf"\b(by|per|each|across)\s+{phrase}", q):
                res.dimensions.remove(f.dimension)
        # "Enterprise accounts", "Laptop products", "Gold tier customers": the noun after a filter value is
        # descriptive, not a group-by
        for f in res.filters:
            value = (f.value if isinstance(f.value, str) else f.value[0]).lower()
            for phrase, key in self._dimension_phrases:
                if key in res.dimensions and re.search(
                    _WORD.format(re.escape(value)) + r"\s+(\w+\s+)?" + re.escape(phrase) + r"(?![a-z0-9])", q
                ):
                    if not re.search(rf"\b(by|per|each|across)\s+{re.escape(phrase)}", q):
                        res.dimensions.remove(key)

        # a breakdown BY status with the delivered-only order count is meaningless: use the any-status count
        if "status" in res.dimensions and "order_count" in res.metrics and "all_orders" in self.metrics:
            res.metrics[res.metrics.index("order_count")] = "all_orders"
        # "revenue from orders with ..." : a second counting metric pulled in by a connective phrase is noise
        if len(res.metrics) > 1:
            for m in list(res.metrics):
                phrase = res.matched_phrases.get(m, "")
                if phrase and re.search(rf"\b(from|with|of|for|per)\s+{re.escape(phrase)}\b", q):
                    res.metrics.remove(m)
        # a phrase that is ambiguous between a metric and a dimension (e.g. "customers", "sellers") is a dimension
        # whenever another metric is present: "top 5 sellers by revenue" needs no clarification
        for phrase, options in self.ambiguous.items():
            dims = [o for o in options if o in self.dimensions]
            mets = [o for o in options if o in self.metrics]
            if not (dims and mets):
                continue
            present = re.search(_WORD.format(re.escape(phrase)), q) is not None
            if present and any(m not in mets for m in res.metrics):
                res.ambiguous.pop(phrase, None)
                for m in mets:
                    if m in res.metrics:
                        res.metrics.remove(m)
                for d in dims:
                    if d not in res.dimensions:
                        res.dimensions.append(d)
        m = re.search(r"\b(top|bottom|lowest|worst|best|largest|smallest|highest)\s+(\d{1,3})\b", q)
        if m:
            res.top_n = int(m.group(2))
            if m.group(1) in ("bottom", "lowest", "worst", "smallest"):
                res.order = "asc"
        elif re.search(r"\b(top|best|largest|highest|most|biggest|leading)\b", q):
            res.top_n = 10
        elif re.search(r"\b(bottom|lowest|smallest|fewest|least)\b", q):
            res.top_n, res.order = 10, "asc"
        elif re.search(r"\bworst\b", q):
            res.top_n, res.order = 10, "asc"
        if m and m.group(1) == "worst" and res.metrics and self.metrics[res.metrics[0]].get("bad_is_high"):
            res.order = "desc"
        elif (
            m is None and re.search(r"\bworst\b", q) and res.metrics and self.metrics[res.metrics[0]].get("bad_is_high")
        ):
            res.order = "desc"
        if (
            m
            and m.group(1) in ("best", "top", "highest", "largest")
            and res.metrics
            and self.metrics[res.metrics[0]].get("bad_is_high")
            and re.search(r"\bbest\b", q)
        ):
            res.order = "asc"
        fy_now, fq_now = self.fiscal_of(as_of)
        if re.search(
            r"\b(yoy|year[- ]over[- ]year|vs\.? last year|versus last year|compared (to|with) last year|against last year)\b",
            q,
        ):
            res.compare = "yoy"
            if res.time is None or res.time.label in ("last fiscal year", "last year"):
                res.time = TimeFilter(fiscal_year=fy_now, label="this year vs last")
            if res.time.fiscal_year == fy_now and not res.time.fiscal_quarter and not re.search(r"\b20\d{2}\b", q):
                # the current year is incomplete: compare year-to-date with the same window last year
                res.time.year_month_to = as_of.strftime("%Y-%m")
                res.time.label = "year to date vs same period last year"
        elif re.search(
            r"\b(qoq|quarter[- ]over[- ]quarter|vs\.? last quarter|versus last quarter|compared (to|with) last quarter)\b",
            q,
        ):
            res.compare = "qoq"
            if res.time is None or res.time.label == "last quarter":
                res.time = TimeFilter(fiscal_year=fy_now, fiscal_quarter=fq_now, label="this quarter vs last")
        # "customers" alone is ambiguous between the dimension and active_customers; the metric wins
        # only if no other metric is present.
        if "customer" in res.dimensions and "active_customers" in res.metrics and len(res.metrics) > 1:
            res.metrics.remove("active_customers")
        res.residual = self._residual(consumed, res)
        return res

    _GLUE = frozenset(
        "what is are was were the a an of in by per for on to and or how much many show me give us list "
        "top bottom best worst highest lowest largest smallest most least fewest biggest leading rate total "
        "overall across all each every with our my your please do did does we i you want need see tell about "
        "trend over time breakdown split compare comparison versus vs against from between as at which who "
        "where when number count amount figure figures value values stats statistics data report chart "
        "table please thanks hi hello".split()
    )

    def _residual(self, consumed: str, res: Resolution) -> list[str]:
        """Words of the question that no metric, dimension, value, time or top-N phrase explained.
        Empty means the semantic layer alone understood the question (the planner can skip the model)."""
        text = consumed
        for f in res.filters:
            text = re.sub(_WORD.format(re.escape(str(f.value).lower())), " ", text)
        text = re.sub(r"\b(top|bottom|lowest|worst|best|largest|smallest|highest)\s+\d{1,3}\b", " ", text)
        text = re.sub(
            r"\b(yoy|qoq|year[- ]over[- ]year|quarter[- ]over[- ]quarter|vs\.?|versus|compared (to|with)|against)\b",
            " ",
            text,
        )
        text = re.sub(r"\b(last|previous|prior|this|current)\s+(year|quarter|month)\b", " ", text)
        words = [w for w in re.findall(r"[a-z][a-z0-9_%-]*", text) if w not in self._GLUE and len(w) > 1]
        return words

    def _parse_time(self, q: str, as_of: date) -> TimeFilter | None:
        fy_now, fq_now = self.fiscal_of(as_of)
        t = TimeFilter()
        m = re.search(r"\bfy\s?(20)?(\d{2})\b", q)
        if m:
            t.fiscal_year = int("20" + m.group(2))
            t.label = m.group(0).strip()
        elif re.search(r"\b(this|current) (fiscal )?year\b", q):
            t.fiscal_year, t.label = fy_now, "this year" if self.calendar else "this fiscal year"
        elif re.search(r"\b(last|previous|prior) (fiscal )?year\b", q):
            t.fiscal_year, t.label = fy_now - 1, "last year" if self.calendar else "last fiscal year"
        elif re.search(r"\bytd\b|year to date", q):
            t.fiscal_year, t.label = fy_now, "year to date"
            t.year_month_to = as_of.strftime("%Y-%m")
        mq = re.search(r"\b(?:fiscal\s+)?q([1-4])\b", q)
        if mq:
            t.fiscal_quarter = int(mq.group(1))
            year_near = re.search(r"\b(20\d{2})\b", q)
            if t.fiscal_year is None and year_near and self.calendar:
                t.fiscal_year = int(year_near.group(1))
            if t.fiscal_year is None:
                t.fiscal_year = fy_now if int(mq.group(1)) <= fq_now else fy_now - 1
            t.label = (t.label + " " if t.label else "") + f"Q{mq.group(1)}"
        elif re.search(r"\b(last|previous|prior) quarter\b", q):
            t.fiscal_year, t.fiscal_quarter = self.previous_quarter(fy_now, fq_now)
            t.label = "last quarter"
        elif re.search(r"\b(this|current) quarter\b", q):
            t.fiscal_year, t.fiscal_quarter, t.label = fy_now, fq_now, "this quarter"
        if t.fiscal_year is None and t.fiscal_quarter is None and re.search(r"\b(last|previous|prior) month\b", q):
            prev = as_of.replace(day=1) - timedelta(days=1)  # the month before the as-of month
            t.year_month_from = t.year_month_to = prev.strftime("%Y-%m")
            t.label = "last month"
        elif t.fiscal_year is None and t.fiscal_quarter is None and re.search(r"\b(this|current) month\b", q):
            t.year_month_from = t.year_month_to = as_of.strftime("%Y-%m")
            t.label = "this month"
        if t.fiscal_year is None and t.fiscal_quarter is None and not t.year_month_from:
            ym = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", q)
            mon = re.search(r"\b(" + "|".join(list(MONTHS_FULL) + list(MONTHS)) + r")\s+(20\d{2})\b", q)
            if ym:
                t.year_month_from = t.year_month_to = ym.group(0)
                t.label = ym.group(0)
            elif mon:
                month = MONTHS_FULL.get(mon.group(1)) or MONTHS[mon.group(1)]
                t.year_month_from = t.year_month_to = f"{mon.group(2)}-{month:02d}"
                t.label = mon.group(0)
            elif re.search(r"\b(last|past|trailing) (12|twelve) months\b", q):
                start = date(as_of.year - 1, as_of.month, 1)
                t.year_month_from, t.year_month_to = start.strftime("%Y-%m"), as_of.strftime("%Y-%m")
                t.label = "last 12 months"
            else:
                cal = re.search(r"\b(?:calendar )?(20\d{2})\b", q)
                if cal:
                    y = int(cal.group(1))
                    if self.calendar:
                        t.fiscal_year, t.label = y, str(y)
                    else:
                        t.year_month_from, t.year_month_to, t.label = f"{y}-01", f"{y}-12", f"calendar {y}"
        return t if (t.fiscal_year or t.fiscal_quarter or t.year_month_from) else None

    # ----- deterministic compiler ------------------------------------------------------------
    def compile_sql(self, plan: Plan) -> tuple[str, list[str]]:
        """Plan -> SQL using only semantic-layer definitions. Returns (sql, notes)."""
        if plan.compare and plan.time and plan.time.fiscal_year:
            return self._compile_comparison(plan)
        return self._compile_simple(plan)

    def _compile_comparison(self, plan: Plan) -> tuple[str, list[str]]:
        """YoY / QoQ as two compiled periods joined on the dimensions, with absolute and % change."""
        t = plan.time
        assert t is not None and t.fiscal_year
        if plan.compare == "qoq" and t.fiscal_quarter:
            pfy, pfq = self.previous_quarter(t.fiscal_year, t.fiscal_quarter)
            prev_time = TimeFilter(fiscal_year=pfy, fiscal_quarter=pfq)
        else:
            prev_time = TimeFilter(fiscal_year=t.fiscal_year - 1, fiscal_quarter=t.fiscal_quarter)
            if t.year_month_to:  # year-to-date window shifted back 12 months
                prev_time.year_month_to = f"{int(t.year_month_to[:4]) - 1}{t.year_month_to[4:]}"
            if t.year_month_from:
                prev_time.year_month_from = f"{int(t.year_month_from[:4]) - 1}{t.year_month_from[4:]}"
        base = plan.model_copy(update={"compare": None, "top_n": None})
        cur_sql, notes = self._compile_simple(base.model_copy(update={"time": t}))
        prev_sql, _ = self._compile_simple(base.model_copy(update={"time": prev_time}))
        dims = [d for d in plan.dimensions if d in self.dimensions]
        metric = plan.metrics[0]
        cur_sql, prev_sql = _strip_limit(cur_sql), _strip_limit(prev_sql)
        if dims:
            on = " AND ".join(f"cur.{d} = prev.{d}" for d in dims)
            select_dims = ", ".join(f"cur.{d} AS {d}" for d in dims)
            sql = (
                f"SELECT {select_dims}, cur.{metric} AS current_period, prev.{metric} AS previous_period, "
                f"cur.{metric} - prev.{metric} AS change, "
                f"(cur.{metric} - prev.{metric}) * 100.0 / NULLIF(prev.{metric}, 0) AS change_pct\n"
                f"FROM ({cur_sql}) cur\nLEFT JOIN ({prev_sql}) prev ON {on}\n"
                f"ORDER BY current_period {plan.order.upper()}\nLIMIT {plan.top_n or 100}"
            )
        else:
            sql = (
                f"SELECT cur.{metric} AS current_period, prev.{metric} AS previous_period, "
                f"cur.{metric} - prev.{metric} AS change, "
                f"(cur.{metric} - prev.{metric}) * 100.0 / NULLIF(prev.{metric}, 0) AS change_pct\n"
                f"FROM ({cur_sql}) cur, ({prev_sql}) prev"
            )
        label = self.period_label(t.fiscal_year, t.fiscal_quarter) + (
            f" to {t.year_month_to}" if t.year_month_to else ""
        )
        plabel = self.period_label(prev_time.fiscal_year, prev_time.fiscal_quarter) + (
            f" to {prev_time.year_month_to}" if prev_time.year_month_to else ""
        )
        notes.append(f"{plan.compare.upper()}: current = {label}, previous = {plabel}")
        return sql, notes

    def _compile_simple(self, plan: Plan) -> tuple[str, list[str]]:
        notes: list[str] = []
        metrics = [m for m in plan.metrics if m in self.metrics]
        if not metrics:
            raise ValueError("plan has no known metric")
        dims = [d for d in plan.dimensions if d in self.dimensions]
        needed_tables: set[str] = set()
        select: list[str] = []
        group: list[str] = []
        for d in dims:
            spec = self.dimensions[d]
            select.append(f"{spec['column']} AS {d}")
            group.append(spec["column"])
            needed_tables.add(spec["table"])
        for m in metrics:
            select.append(f"{self.metrics[m]['sql']} AS {m}")
            for tbl in self.metrics[m].get("requires", []):
                needed_tables.add(tbl)
        base = self.metrics[metrics[0]].get("base") or self.metrics[metrics[0]].get("base_table", "fact_orders")
        for m in metrics[1:]:
            if (self.metrics[m].get("base") or self.metrics[m].get("base_table", "fact_orders")) != base:
                notes.append(
                    f"metric {m} aggregates over a different base table than {metrics[0]}; the SQL agent must combine them"
                )
        where: list[str] = list(self.metrics[metrics[0]].get("filters", []))
        for m in metrics[1:]:
            if list(self.metrics[m].get("filters", [])) != where:
                notes.append(f"metric {m} has a different status filter than {metrics[0]}; WHERE follows {metrics[0]}")
        for f in plan.filters:
            if f.dimension not in self.dimensions:
                continue
            spec = self.dimensions[f.dimension]
            needed_tables.add(spec["table"])
            if f.op == "in" and isinstance(f.value, list):
                vals = ", ".join(_quote(v) for v in f.value)
                where.append(f"{spec['column']} IN ({vals})")
            else:
                value = f.value if isinstance(f.value, str) else f.value[0]
                where.append(f"{spec['column']} {f.op} {_quote(value)}")
        if plan.time:
            t = plan.time
            needed_tables.add("dim_date")
            if t.fiscal_year:
                where.append(f"{self.year_col} = {int(t.fiscal_year)}")
            if t.fiscal_quarter:
                where.append(f"{self.quarter_col} = {int(t.fiscal_quarter)}")
            if t.year_month_from:
                where.append(f"d.year_month >= '{t.year_month_from}'")
            if t.year_month_to:
                where.append(f"d.year_month <= '{t.year_month_to}'")
        base_spec = self.bases.get(base) or {
            "alias": "o",
            "joins": {k: v["join"] for k, v in self.tables.items() if "join" in v},
        }
        join_map: dict[str, str] = dict(base_spec.get("joins", {}))
        needed_tables.discard(base)
        if (
            any(tbl not in join_map for tbl in needed_tables)
            and base == "fact_orders"
            and "fact_order_items" in self.bases
        ):
            # order-grain metric sliced by a product/seller dimension: pivot to the item grain through the orders join
            alt = self.bases["fact_order_items"]
            if all(tbl in alt["joins"] for tbl in needed_tables):
                base, base_spec, join_map = "fact_order_items", alt, dict(alt["joins"])
                needed_tables.add("fact_orders")
                notes.append(
                    "order-level metric evaluated at item grain (joined through fact_orders) to slice by product/seller"
                )
        # a dimension on fact_orders reached from the items base needs the orders join first
        if base == "fact_order_items" and any(tbl == "fact_orders" for tbl in needed_tables):
            pass
        ordered = [tbl for tbl in join_map if tbl in needed_tables]
        missing = [tbl for tbl in needed_tables if tbl not in join_map]
        if missing:
            notes.append(f"no join from {base} to {', '.join(sorted(missing))}; the SQL agent must write it")
        joins = [join_map[tbl] for tbl in ordered]
        sql = (
            "SELECT "
            + ", ".join(select)
            + f"\nFROM {base} {base_spec['alias']}\n"
            + ("\n".join(joins) + "\n" if joins else "")
        )
        if where:
            sql += "WHERE " + " AND ".join(where) + "\n"
        if group:
            sql += "GROUP BY " + ", ".join(group) + "\n"
            time_dims = [d for d in dims if self.dimensions[d].get("time")]
            if plan.top_n or not time_dims:
                sql += f"ORDER BY {metrics[0]} {plan.order.upper()}\n"
            else:
                sql += "ORDER BY " + ", ".join(self.dimensions[d]["column"] for d in time_dims) + "\n"
            sql += f"LIMIT {plan.top_n or 100}"
        if plan.compare:
            notes.append("period comparison is not compiled deterministically; the SQL agent handles YoY/QoQ")
        return sql.strip(), notes

    def plan_from_resolution(self, res: Resolution) -> Plan:
        plan = Plan(
            metrics=res.metrics,
            dimensions=res.dimensions,
            filters=res.filters,
            time=res.time,
            top_n=res.top_n,
            order=res.order,  # type: ignore[arg-type]
            compare=res.compare,  # type: ignore[arg-type]
        )
        if res.ambiguous:
            phrase, options = next(iter(res.ambiguous.items()))
            plan.needs_clarification = True
            plan.clarification = f'"{phrase}" can mean more than one metric. Which one?'
            plan.options = [self.metrics[o]["label"] if o in self.metrics else o for o in options]
        elif not res.metrics and res.dimensions:
            plan.needs_clarification = True
            plan.clarification = "Which metric should I use?"
            plan.options = [self.metrics[m]["label"] for m in list(self.metrics)[:4]]
        plan.steps = [
            f"resolve metrics {plan.metrics or '?'} and dimensions {plan.dimensions or '[]'}",
            "retrieve definitions and table docs",
            "write and validate SQL",
            "execute (read-only, row cap)",
            "interpret results and propose a chart",
            "critic check",
        ]
        return plan


def _strip_limit(sql: str) -> str:
    return re.sub(r"\s*LIMIT \d+\s*$", "", sql.strip())


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@lru_cache(maxsize=4)
def load_semantic(semantic_dir: str) -> SemanticLayer:
    return SemanticLayer.load(Path(semantic_dir))
