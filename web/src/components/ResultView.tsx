import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ChatResult } from "../api";

const COLORS = ["#2f6fed", "#f28c28", "#2bb673", "#b452cd"];

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toLocaleString();
    return Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return String(v);
}

function compact(v: number): string {
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(0)}k`;
  return v.toFixed(Math.abs(v) < 10 ? 1 : 0);
}

export function ResultView({ r }: { r: ChatResult }) {
  const [tab, setTab] = useState<"chart" | "table" | "sql" | "trace">("chart");
  const chart = r.analysis?.chart;
  const data = r.rows.map((row) => Object.fromEntries(r.columns.map((c, i) => [c, row[i]])));
  const x = chart?.x && r.columns.includes(chart.x) ? chart.x : r.columns[0];
  const ys = (chart?.y || []).filter((c) => r.columns.includes(c));
  const yCols = ys.length ? ys : r.columns.filter((c) => c !== x && typeof data[0]?.[c] === "number");
  const canChart = chart && chart.type !== "table" && data.length > 0;

  return (
    <div className="result">
      <div className="tabs">
        {(["chart", "table", "sql", "trace"] as const).map((t) => (
          <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
            {t === "table" ? `table (${r.row_count}${r.truncated ? "+" : ""})` : t}
          </button>
        ))}
        <span className="meta">
          {r.total_ms} ms · {r.attempts.length} attempt{r.attempts.length === 1 ? "" : "s"} · critic {r.critic?.verdict ?? "—"}
        </span>
      </div>

      {tab === "chart" && (
        <div className="panel">
          {!canChart && <p className="muted">No chart for this result — see the table.</p>}
          {canChart && chart.type === "kpi" && (
            <div className="kpis">
              {yCols.map((c, i) => (
                <div className="kpi" key={c}>
                  <div className="kpi-label">{c.replace(/_/g, " ")}</div>
                  <div className="kpi-value" style={{ color: COLORS[i % COLORS.length] }}>
                    {fmt(data[0][c])}
                  </div>
                </div>
              ))}
            </div>
          )}
          {canChart && chart.type === "bar" && (
            <ResponsiveContainer width="100%" height={Math.min(420, 120 + data.length * 28)}>
              <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 8, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" tickFormatter={compact} />
                <YAxis type="category" dataKey={x} width={150} tick={{ fontSize: 12 }} />
                <Tooltip formatter={(v) => fmt(v as number)} />
                {yCols.length > 1 && <Legend />}
                {yCols.map((c, i) => (
                  <Bar key={c} dataKey={c} fill={COLORS[i % COLORS.length]} radius={[0, 4, 4, 0]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          )}
          {canChart && chart.type === "line" && (
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={data} margin={{ left: 8, right: 24, top: 8, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey={x} tick={{ fontSize: 12 }} />
                <YAxis tickFormatter={compact} width={64} />
                <Tooltip formatter={(v) => fmt(v as number)} />
                {yCols.length > 1 && <Legend />}
                {yCols.map((c, i) => (
                  <Line key={c} type="monotone" dataKey={c} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={data.length < 40} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      )}

      {tab === "table" && (
        <div className="panel table-wrap">
          <table>
            <thead>
              <tr>
                {r.columns.map((c) => (
                  <th key={c}>{c}</th>
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
          {r.rows.length > 200 && <p className="muted">Showing 200 of {r.row_count} rows.</p>}
        </div>
      )}

      {tab === "sql" && (
        <div className="panel">
          <pre className="sql">{r.sql}</pre>
          {r.attempts.filter((a) => a.error).length > 0 && (
            <details>
              <summary>{r.attempts.filter((a) => a.error).length} failed attempt(s) repaired</summary>
              {r.attempts
                .filter((a) => a.error)
                .map((a, i) => (
                  <div key={i} className="attempt">
                    <div className="err">{a.error}</div>
                    <pre className="sql small">{a.sql}</pre>
                  </div>
                ))}
            </details>
          )}
        </div>
      )}

      {tab === "trace" && (
        <div className="panel">
          <div className="plan">
            <strong>Plan</strong>
            <pre className="small">{JSON.stringify(r.plan, null, 2)}</pre>
          </div>
          <div className="trace">
            <strong>Agents</strong>
            <ol>
              {r.trace.map((e, i) => {
                const { node, ms, ...rest } = e;
                return (
                  <li key={i}>
                    <code>{node}</code> <span className="muted">{ms} ms</span>{" "}
                    {Object.keys(rest).length > 0 && <span className="small muted">{JSON.stringify(rest)}</span>}
                  </li>
                );
              })}
            </ol>
          </div>
          {r.tool_steps && r.tool_steps.length > 0 && (
            <div className="trace">
              <strong>SQL agent tool calls</strong> <span className="muted">(ReAct: reason → call → observe)</span>
              <ol>
                {r.tool_steps.map((t, i) => (
                  <li key={i}>
                    <code>
                      {t.tool}({Object.values(t.args).map(String).join(", ")})
                    </code>
                    <pre className="small observation">{t.observation.slice(0, 400)}</pre>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {r.retrieval && (
            <div className="trace">
              <strong>Retrieval</strong>{" "}
              <span className="muted">
                ({r.retrieval.backend} + BM25, RRF-fused{r.retrieval.reranker && r.retrieval.reranker !== "none" ? `, ${r.retrieval.reranker} rerank` : ""})
              </span>
              <ul>
                {r.retrieval.hits.map((h) => (
                  <li key={h.id}>
                    <code>{h.id}</code>{" "}
                    <span className="muted small">
                      score {h.score} · bm25 #{h.bm25_rank ?? "—"} · dense #{h.dense_rank ?? "—"}
                      {h.structured ? " · structured" : ""}
                      {h.rerank ? ` · rerank #${h.rerank.before}→#${h.rerank.after} (${h.rerank.score})` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {r.critic && (
            <div className="trace">
              <strong>Critic</strong> <code>{r.critic.verdict}</code> <span className="muted">{r.critic.reason}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
