"""LLM access with structured outputs, plus a deterministic fake for tests and the no-key demo.

Every agent call goes through `LLM.structured(task, schema, system, user, context)`.
- OpenAILLM: any OpenAI-compatible chat endpoint, pydantic schema enforced via structured output.
- FakeLLM: no network. Uses the semantic layer directly (the resolver + compiler), so the full
  graph, guard, executor, analyst and critic run end to end in CI with real data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field

from .config import Settings
from .semantic import Plan, SemanticLayer

T = TypeVar("T", bound=BaseModel)


# ----- schemas shared by the agents ---------------------------------------------------------------
class RouterOut(BaseModel):
    intent: Literal["data_question", "clarification_reply", "out_of_scope", "greeting", "meta"]
    reason: str = ""


class SqlOut(BaseModel):
    sql: str = Field(description="one SELECT statement in PostgreSQL syntax")
    rationale: str = ""
    assumptions: list[str] = Field(default_factory=list)


class ChartSpec(BaseModel):
    type: Literal["bar", "line", "table", "kpi"] = "table"
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    title: str = ""


class AnalysisOut(BaseModel):
    summary: str
    insights: list[str] = Field(default_factory=list)
    chart: ChartSpec = Field(default_factory=ChartSpec)
    caveats: list[str] = Field(default_factory=list)


class CriticOut(BaseModel):
    verdict: Literal["pass", "revise"]
    reason: str = ""
    fix_hint: str = ""


# ----- providers ----------------------------------------------------------------------------------
@dataclass
class ToolStep:
    """One ReAct step: which tool the model called, with what, and what it observed."""

    tool: str
    args: dict[str, Any]
    observation: str


class LLM:
    name = "base"

    def structured(
        self, task: str, schema: type[T], system: str, user: str, context: dict[str, Any]
    ) -> T:  # pragma: no cover
        raise NotImplementedError

    def react(
        self, system: str, user: str, tools: list[Any], max_steps: int, context: dict[str, Any]
    ) -> tuple[str, list[ToolStep]]:  # pragma: no cover
        """Tool-calling loop. Returns the model's final text and the tool steps it took."""
        raise NotImplementedError


class OpenAILLM(LLM):
    name = "openai"

    def __init__(self, settings: Settings):
        from langchain_openai import ChatOpenAI

        # Chat Completions, not the Responses API: every OpenAI-compatible endpoint (Groq, vLLM, Azure, ...) speaks it,
        # and ask_wj runs gpt-5.6-sol on it in production.
        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "api_key": settings.llm_api_key,
            "max_retries": 2,
            "use_responses_api": False,
            "timeout": settings.llm_timeout_s,
        }
        if settings.llm_temperature is not None:
            kwargs["temperature"] = settings.llm_temperature
        if settings.llm_reasoning_effort.strip():
            kwargs["reasoning_effort"] = settings.llm_reasoning_effort.strip()
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        self.client = ChatOpenAI(**kwargs)
        self.last_usage: dict[str, int] = {}

    def structured(self, task: str, schema: type[T], system: str, user: str, context: dict[str, Any]) -> T:
        runnable = self.client.with_structured_output(schema, include_raw=True)
        out = runnable.invoke([("system", system), ("user", user)])
        raw = out.get("raw")
        usage = getattr(raw, "usage_metadata", None) or {}
        self.last_usage = {"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0)}
        parsed = out.get("parsed")
        if parsed is None:
            raise RuntimeError(f"model returned no parseable {schema.__name__}: {out.get('parsing_error')}")
        return parsed

    def react(
        self, system: str, user: str, tools: list[Any], max_steps: int, context: dict[str, Any]
    ) -> tuple[str, list[ToolStep]]:
        from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

        by_name = {t.name: t for t in tools}
        model = self.client.bind_tools(tools)
        messages: list[Any] = [SystemMessage(system), HumanMessage(user)]
        steps: list[ToolStep] = []
        for _ in range(max_steps):
            ai = model.invoke(messages)
            messages.append(ai)
            calls = getattr(ai, "tool_calls", None) or []
            if not calls:
                return str(ai.content), steps
            for call in calls:
                tool = by_name.get(call["name"])
                observation = tool.invoke(call["args"]) if tool else f"error: unknown tool {call['name']}"
                steps.append(ToolStep(call["name"], dict(call["args"]), str(observation)))
                messages.append(ToolMessage(content=str(observation), tool_call_id=call["id"]))
        final = self.client.invoke(messages + [HumanMessage("Stop exploring and give your final answer now.")])
        return str(final.content), steps


class FakeLLM(LLM):
    """Deterministic stand-in. `context` carries what each node already computed."""

    name = "fake"

    def __init__(self, semantic: SemanticLayer):
        self.semantic = semantic
        self.last_usage: dict[str, int] = {}

    def react(
        self, system: str, user: str, tools: list[Any], max_steps: int, context: dict[str, Any]
    ) -> tuple[str, list[ToolStep]]:
        by_name = {t.name: t for t in tools}
        steps: list[ToolStep] = []
        plan: Plan = context["plan"]
        tables = {self.semantic.dimensions[d]["table"] for d in plan.dimensions if d in self.semantic.dimensions}
        for table in sorted(tables)[:1]:  # one schema lookup, like a careful model would
            steps.append(
                ToolStep("describe_table", {"table": table}, str(by_name["describe_table"].invoke({"table": table})))
            )
        for f in plan.filters[:1]:
            spec = self.semantic.dimensions.get(f.dimension)
            if spec:
                table, column = spec["table"], spec["column"].split(".")[-1]
                args = {"table": table, "column": column}
                steps.append(ToolStep("sample_values", args, str(by_name["sample_values"].invoke(args))))
        try:
            draft, _ = self.semantic.compile_sql(plan)
        except ValueError:
            return "", steps
        steps.append(ToolStep("validate_sql", {"sql": draft}, str(by_name["validate_sql"].invoke({"sql": draft}))))
        return draft, steps

    def structured(self, task: str, schema: type[T], system: str, user: str, context: dict[str, Any]) -> T:
        if schema is RouterOut:
            q = context["question"].lower().strip()
            if context.get("awaiting_clarification"):
                return RouterOut(intent="clarification_reply")  # type: ignore[return-value]
            if q.rstrip("!.") in {"hi", "hello", "hey", "thanks", "thank you"}:
                return RouterOut(intent="greeting")  # type: ignore[return-value]
            if any(w in q for w in ("weather", "joke", "poem", "capital of")):
                return RouterOut(intent="out_of_scope")  # type: ignore[return-value]
            if q.startswith(("what metrics", "which metrics", "what can you", "help")):
                return RouterOut(intent="meta")  # type: ignore[return-value]
            return RouterOut(intent="data_question")  # type: ignore[return-value]
        if schema is Plan:
            return context["draft_plan"]  # the resolver's plan is the fake planner's plan
        if schema is SqlOut:
            plan: Plan = context["plan"]
            if context.get("error") or context.get("hint"):
                # one deterministic "repair": drop the time filter if the first attempt failed or the critic objected
                plan = plan.model_copy(update={"time": None})
            sql, notes = self.semantic.compile_sql(plan)
            return SqlOut(sql=sql, rationale="compiled from the semantic layer", assumptions=notes)  # type: ignore[return-value]
        if schema is AnalysisOut:
            plan: Plan = context["plan"]
            fmt = self.semantic.metrics.get(plan.metrics[0], {}).get("format", "number") if plan.metrics else "number"
            return _fake_analysis(context, fmt)  # type: ignore[return-value]
        if schema is CriticOut:
            rows = context.get("row_count", 0)
            if rows == 0 and not context.get("critic_looped"):
                return CriticOut(verdict="revise", reason="query returned no rows", fix_hint="loosen the time filter")  # type: ignore[return-value]
            return CriticOut(verdict="pass")  # type: ignore[return-value]
        raise NotImplementedError(schema.__name__)


def _fake_analysis(context: dict[str, Any], fmt: str = "number") -> AnalysisOut:
    plan: Plan = context["plan"]
    columns: list[str] = context.get("columns", [])
    rows: list[list[Any]] = context.get("rows", [])
    metric = plan.metrics[0] if plan.metrics else (columns[-1] if columns else "value")
    label = metric.replace("_", " ")
    if not rows:
        return AnalysisOut(
            summary=f"No completed orders match that filter, so {label} is empty for the period.",
            chart=ChartSpec(type="table"),
        )
    if plan.compare and "change_pct" in columns:
        i_cur, i_prev, i_pct = (
            columns.index("current_period"),
            columns.index("previous_period"),
            columns.index("change_pct"),
        )
        if plan.dimensions:
            top = rows[0]
            return AnalysisOut(
                summary=f"{label.capitalize()} {plan.compare.upper()}: {str(top[0])} is {_fmt(top[i_cur], fmt)} vs {_fmt(top[i_prev], fmt)} ({_pct(top[i_pct])}).",
                insights=[f"{len(rows)} groups compared"],
                chart=ChartSpec(
                    type="bar",
                    x=plan.dimensions[0],
                    y=["current_period", "previous_period"],
                    title=f"{label}: current vs previous",
                ),
            )
        r = rows[0]
        return AnalysisOut(
            summary=f"{label.capitalize()} is {_fmt(r[i_cur], fmt)} vs {_fmt(r[i_prev], fmt)} in the prior period ({_pct(r[i_pct])}).",
            chart=ChartSpec(type="kpi", y=["current_period", "previous_period"], title=label),
        )
    if not plan.dimensions:
        value = rows[0][columns.index(metric)] if metric in columns else rows[0][-1]
        return AnalysisOut(
            summary=f"{label.capitalize()} is {_fmt(value, fmt)} for the requested scope.",
            chart=ChartSpec(type="kpi", y=[metric], title=label),
        )
    dim = plan.dimensions[0]
    idx_m = columns.index(metric) if metric in columns else len(columns) - 1
    idx_d = columns.index(dim) if dim in columns else 0
    top = rows[0]
    total = sum(float(r[idx_m] or 0) for r in rows)
    share = (float(top[idx_m] or 0) / total * 100) if total else 0
    time_dim = any(d in ("month", "fiscal_quarter", "fiscal_year") for d in plan.dimensions)
    chart = ChartSpec(
        type="line" if time_dim else "bar",
        x=dim,
        y=[m for m in plan.metrics if m in columns] or [metric],
        title=f"{label} by {dim}",
    )
    if time_dim:
        first, last = rows[0][idx_m], rows[-1][idx_m]
        trend = "up" if (last or 0) > (first or 0) else "down"
        summary = f"{label.capitalize()} by {dim}: {len(rows)} periods, moving {trend} from {_fmt(first, fmt)} to {_fmt(last, fmt)}."
    elif fmt == "percent":
        summary = f"{str(top[idx_d])} has the highest {label} at {_fmt(top[idx_m], fmt)}; {str(rows[-1][idx_d])} the lowest at {_fmt(rows[-1][idx_m], fmt)}."
    elif plan.order == "asc":
        summary = (
            f"{str(top[idx_d])} has the lowest {label} at {_fmt(top[idx_m], fmt)} among the {len(rows)} groups shown."
        )
    else:
        summary = f"{str(top[idx_d])} leads {label} at {_fmt(top[idx_m], fmt)} ({share:.0f}% of the {len(rows)} groups shown)."
    insights = [f"{len(rows)} groups returned", f"top group: {top[idx_d]}"]
    return AnalysisOut(summary=summary, insights=insights, chart=chart)


def _pct(v: Any) -> str:
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt(v: Any, fmt: str = "number") -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if fmt == "percent":
        return f"{f:.1f}%"
    if fmt == "integer":
        return f"{f:,.0f}"
    prefix = "USD " if fmt == "currency" else ""
    if abs(f) >= 1_000_000:
        return f"{prefix}{f / 1_000_000:.2f}M"
    if abs(f) >= 1_000:
        return f"{prefix}{f:,.0f}"
    return f"{prefix}{f:.2f}"


def make_llm(settings: Settings, semantic: SemanticLayer) -> LLM:
    if settings.llm_provider == "openai" and settings.llm_api_key:
        return OpenAILLM(settings)
    return FakeLLM(semantic)
