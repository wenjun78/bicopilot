import { useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ChatResult } from "../api";

// Categorical series in fixed order (validated with the dataviz six checks for both colour schemes);
// a value is never coloured by rank, and a fifth-plus series folds into the last slot.
export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)"];
const AXIS = { fill: "var(--muted)", fontSize: 12 } as const;

export function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toLocaleString();
    return Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return String(v);
}

function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(0)}k`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  if (a >= 100 || Number.isInteger(v)) return v.toFixed(0);
  return v.toFixed(1);
}

function label(c: string): string {
  return c.replace(/_/g, " ");
}

type TipProps = { active?: boolean; label?: string | number; payload?: { name: string; value: number; color?: string }[] };
function Tip({ active, label: l, payload }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tip">
      <div className="k">{String(l)}</div>
      {payload.map((p) => (
        <div key={p.name} className="v">
          <span className="swatch" style={{ background: p.color }} />
          <span>{label(p.name)}</span>
          <strong>{fmt(p.value)}</strong>
        </div>
      ))}
    </div>
  );
}

export function ResultView({ r }: { r: ChatResult }) {
  const [tab, setTab] = useState<"chart" | "table" | "sql" | "trace">("chart");
  const chart = r.analysis?.chart;
  const data = r.rows.map((row) => Object.fromEntries(r.columns.map((c, i) => [c, row[i]])));
  const x = chart?.x && r.columns.includes(chart.x) ? chart.x : r.columns[0];
  const ys = (chart?.y || []).filter((c) => r.columns.includes(c));
  const yCols = ys.length ? ys : r.columns.filter((c) => c !== x && typeof data[0]?.[c] === "number");
  const canChart = chart && chart.type !== "table" && data.length > 0;
  const numeric = new Set(r.columns.filter((c) => typeof data[0]?.[c] === "number"));

  return (
    <div className="result">
      <div className="tabs" role="tablist">
        {(["chart", "table", "sql", "trace"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
            {t === "table" ? `Table (${r.row_count}${r.truncated ? "+" : ""})` : t === "sql" ? "SQL" : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
        {r.masked_columns && r.masked_columns.length > 0 && <span className="meta">masked: {r.masked_columns.join(", ")}</span>}
      </div>

      {tab === "chart" && (
        <div className="panel">
          {!canChart && <p className="muted">There is no chart for this result; the table has the rows.</p>}
          {canChart && chart.type === "kpi" && (
            <div className="kpis">
              {yCols.map((c) => (
                <div className="kpi" key={c}>
                  <div className="kpi-value">{fmt(data[0][c])}</div>
                  <div className="kpi-label">{label(c)}</div>
                </div>
              ))}
            </div>
          )}
          {canChart && chart.type === "bar" && (
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height={Math.min(440, 90 + data.length * (yCols.length > 1 ? 34 : 26))}>
                <BarChart data={data} layout="vertical" margin={{ left: 4, right: 56, top: 4, bottom: 4 }} barCategoryGap={yCols.length > 1 ? 8 : 6} barGap={2}>
                  <CartesianGrid horizontal={false} stroke="var(--line)" />
                  <XAxis type="number" tickFormatter={compact} tick={AXIS} axisLine={false} tickLine={false} />
                  <YAxis type="category" dataKey={x} width={Math.min(170, 40 + Math.max(...data.map((d) => String(d[x]).length)) * 7)} tick={AXIS} axisLine={false} tickLine={false} />
                  <Tooltip content={<Tip />} cursor={{ fill: "var(--raised)" }} />
                  {yCols.length > 1 && <Legend formatter={(v) => <span style={{ color: "var(--ink-2)", fontSize: 12.5 }}>{label(String(v))}</span>} />}
                  {yCols.map((c, i) => (
                    <Bar key={c} dataKey={c} name={c} fill={SERIES[Math.min(i, SERIES.length - 1)]} radius={[0, 4, 4, 0]} barSize={yCols.length > 1 ? 10 : 14} isAnimationActive={false}>
                      {yCols.length === 1 && data.length <= 12 && <LabelList dataKey={c} position="right" formatter={(v: number) => compact(v)} style={{ fill: "var(--ink-2)", fontSize: 12 }} />}
                    </Bar>
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          {canChart && chart.type === "line" && (
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height={320}>
                <LineChart data={data} margin={{ left: 4, right: 24, top: 12, bottom: 4 }}>
                  <CartesianGrid vertical={false} stroke="var(--line)" />
                  <XAxis dataKey={x} tick={AXIS} axisLine={{ stroke: "var(--line)" }} tickLine={false} minTickGap={24} />
                  <YAxis tickFormatter={compact} width={56} tick={AXIS} axisLine={false} tickLine={false} />
                  <Tooltip content={<Tip />} cursor={{ stroke: "var(--line-strong)" }} />
                  {yCols.length > 1 && <Legend formatter={(v) => <span style={{ color: "var(--ink-2)", fontSize: 12.5 }}>{label(String(v))}</span>} />}
                  {yCols.map((c, i) => (
                    <Line
                      key={c}
                      type="monotone"
                      dataKey={c}
                      name={c}
                      stroke={SERIES[Math.min(i, SERIES.length - 1)]}
                      strokeWidth={2}
                      dot={data.length < 40 ? { r: 3, strokeWidth: 0, fill: SERIES[Math.min(i, SERIES.length - 1)] } : false}
                      activeDot={{ r: 5, stroke: "var(--surface)", strokeWidth: 2 }}
                      isAnimationActive={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}

      {tab === "table" && (
        <div className="panel table-wrap">
          <table>
            <thead>
              <tr>
                {r.columns.map((c) => (
                  <th key={c} className={numeric.has(c) ? "num" : ""}>
                    {label(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {r.rows.slice(0, 200).map((row, i) => (
                <tr key={i}>
                  {row.map((v, j) => (
                    <td key={j} className={typeof v === "number" ? "num" : ""}>
                      {fmt(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {r.rows.length > 200 && <p className="muted small">Showing 200 of {r.row_count} rows; export for the rest.</p>}
        </div>
      )}

      {tab === "sql" && (
        <div className="panel">
          <pre className="sql">{r.sql}</pre>
          {r.attempts.filter((a) => a.error).length > 0 && (
            <details>
              <summary>{r.attempts.filter((a) => a.error).length} earlier attempt(s) were repaired</summary>
              {r.attempts
                .filter((a) => a.error)
                .map((a, i) => (
                  <div key={i} className="attempt">
                    <div className="err small">{a.error}</div>
                    <pre className="sql small">{a.sql}</pre>
                  </div>
                ))}
            </details>
          )}
        </div>
      )}

      {tab === "trace" && (
        <div className="panel trace-grid">
          <div className="trace-block">
            <strong>Agents</strong>
            <span className="lead">what each node did and how long it took</span>
            <ol className="steps">
              {r.trace.map((e, i) => {
                const { node, ms, ...rest } = e;
                return (
                  <li key={i}>
                    <code>{node}</code>
                    <span className="ms">{ms} ms</span>
                    <span className="detail">{Object.keys(rest).length > 0 ? JSON.stringify(rest) : ""}</span>
                  </li>
                );
              })}
            </ol>
          </div>
          {r.tool_steps && r.tool_steps.length > 0 && (
            <div className="trace-block">
              <strong>SQL agent tool calls</strong>
              <span className="lead">reason, call, observe</span>
              <ol className="steps">
                {r.tool_steps.map((t, i) => (
                  <li key={i} style={{ gridTemplateColumns: "1fr" }}>
                    <code>
                      {t.tool}({Object.values(t.args).map(String).join(", ")})
                    </code>
                    <pre className="observation">{t.observation.slice(0, 400)}</pre>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {r.retrieval && (
            <div className="trace-block">
              <strong>Retrieval</strong>
              <span className="lead">
                {r.retrieval.backend} vectors and BM25, fused with RRF{r.retrieval.reranker && r.retrieval.reranker !== "none" ? `, ${r.retrieval.reranker} rerank` : ""}
              </span>
              <ul className="hits">
                {r.retrieval.hits.map((h) => (
                  <li key={h.id}>
                    <code>{h.id}</code>
                    <span className="detail">
                      score {h.score}, bm25 #{h.bm25_rank ?? "—"}, dense #{h.dense_rank ?? "—"}
                      {h.structured ? ", structured" : ""}
                      {h.rerank ? `, rerank #${h.rerank.before} to #${h.rerank.after} (${h.rerank.score})` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="trace-block">
            <strong>Plan</strong>
            <pre className="plan-json">{JSON.stringify(r.plan, null, 2)}</pre>
          </div>
          {r.critic && (
            <div className="trace-block">
              <strong>Critic</strong> <span className="lead">{r.critic.verdict === "pass" ? "passed" : r.critic.verdict}</span>
              {r.critic.reason && <div className="small muted">{r.critic.reason}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
