import { useEffect, useMemo, useState } from "react";
import { explainSql, runSql, type Analysis, type DictTable, type Dictionary, type Role, type SqlResult } from "../api";
import { ExportMenu } from "./ExportMenu";

const EXAMPLES = [
  "SELECT customer_state, COUNT(*) AS customers, SUM(order_count) AS orders\nFROM dim_customer\nGROUP BY customer_state\nORDER BY customers DESC",
  "SELECT d.year_month, SUM(oi.price) AS revenue, SUM(oi.freight_value) AS freight\nFROM fact_order_items oi\nJOIN dim_date d ON oi.purchase_date = d.date_key\nWHERE oi.status = 'delivered' AND d.year = 2018\nGROUP BY d.year_month\nORDER BY d.year_month",
  "SELECT p.category_group, AVG(o.review_score) AS avg_score, COUNT(*) AS reviewed\nFROM fact_order_items oi\nJOIN fact_orders o ON oi.order_id = o.order_id\nJOIN dim_product p ON oi.product_key = p.product_key\nWHERE o.review_score IS NOT NULL\nGROUP BY p.category_group\nORDER BY avg_score",
];

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(v);
}

export function SqlConsole({
  role,
  dictionary,
  live,
  initialSql,
  onConsumedInitial,
}: {
  role: Role;
  dictionary: Dictionary | null;
  live: boolean;
  initialSql: string;
  onConsumedInitial: () => void;
}) {
  const [sql, setSql] = useState(EXAMPLES[0]);
  const [result, setResult] = useState<SqlResult | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [explain, setExplain] = useState<Analysis | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);

  useEffect(() => {
    if (initialSql) {
      setSql(initialSql);
      onConsumedInitial();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSql]);

  useEffect(() => {
    try {
      const saved = localStorage.getItem("bicopilot.sqlHistory");
      if (saved) setHistory(JSON.parse(saved));
    } catch {
      /* private mode etc. */
    }
  }, []);

  const tables = useMemo(() => dictionary?.tables ?? [], [dictionary]);

  async function run() {
    if (!live) {
      setErr("The SQL console needs the live API. It is not reachable right now.");
      return;
    }
    setBusy(true);
    setErr("");
    setExplain(null);
    try {
      const r = await runSql(sql, role);
      setResult(r);
      const next = [sql, ...history.filter((h) => h !== sql)].slice(0, 20);
      setHistory(next);
      try {
        localStorage.setItem("bicopilot.sqlHistory", JSON.stringify(next));
      } catch {
        /* ignore */
      }
    } catch (e) {
      setErr((e as Error).message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  async function doExplain() {
    if (!result) return;
    setExplaining(true);
    try {
      const r = await explainSql(result.sql, result.columns, result.rows, role);
      setExplain(r.analysis);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setExplaining(false);
    }
  }

  function insert(text: string) {
    setSql((s) => (s.trim() ? `${s}\n${text}` : text));
  }

  return (
    <div className="console">
      <aside className="schema">
        <div className="schema-head">
          <strong>Schema</strong>
          {dictionary && (
            <span className="muted small">
              {dictionary.tables.length} tables · {dictionary.currency} · as of {dictionary.as_of}
            </span>
          )}
        </div>
        {!dictionary && <p className="muted small">Dictionary loads from the live API.</p>}
        {tables.map((t: DictTable) => (
          <div key={t.name} className="schema-table">
            <button type="button" className="schema-toggle" onClick={() => setOpen(open === t.name ? null : t.name)}>
              <span className={`kind ${t.kind}`}>{t.kind === "fact" ? "F" : "D"}</span> <code>{t.name}</code>
              <span className="muted small"> {t.row_count.toLocaleString()}</span>
            </button>
            {open === t.name && (
              <div className="schema-cols">
                <p className="small muted">{t.description}</p>
                {t.columns.map((c) => (
                  <div key={c.name} className="schema-col" title={c.description}>
                    <button type="button" className="link mono" onClick={() => insert(`${t.name}.${c.name}`)}>
                      {c.name}
                    </button>
                    <span className="muted small"> {c.type.toLowerCase()}</span>
                    {c.primary_key && <span className="tag">pk</span>}
                    {c.joins_to && <span className="tag">→ {c.joins_to.split(".")[0]}</span>}
                    {c.masked_for_viewer && <span className="tag warn">masked</span>}
                    {c.samples.length > 0 && c.samples[0].count !== null && (
                      <div className="samples small muted">{c.samples.slice(0, 5).map((s) => s.value).join(" · ")}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </aside>

      <section className="editor">
        <div className="editor-bar">
          <span className="muted small">Read-only · one SELECT · guarded · {role === "viewer" ? "viewer (masked)" : "analyst"}</span>
          <span className="spacer" />
          <select className="select" value="" onChange={(e) => e.target.value && setSql(EXAMPLES[Number(e.target.value)])} aria-label="Example queries">
            <option value="">examples…</option>
            {EXAMPLES.map((_, i) => (
              <option key={i} value={i}>
                example {i + 1}
              </option>
            ))}
          </select>
          {history.length > 0 && (
            <select className="select" value="" onChange={(e) => e.target.value !== "" && setSql(history[Number(e.target.value)])} aria-label="Query history">
              <option value="">history…</option>
              {history.map((h, i) => (
                <option key={i} value={i}>
                  {h.replace(/\s+/g, " ").slice(0, 60)}
                </option>
              ))}
            </select>
          )}
        </div>
        <textarea
          className="sql-input"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          spellCheck={false}
          rows={9}
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") run();
          }}
        />
        <div className="editor-bar">
          <button type="button" className="primary" onClick={run} disabled={busy || !sql.trim()}>
            {busy ? "running…" : "Run (Ctrl+Enter)"}
          </button>
          {result && (
            <button type="button" className="chip" onClick={doExplain} disabled={explaining}>
              {explaining ? "explaining…" : "Explain with the analyst agent"}
            </button>
          )}
          {result && <ExportMenu columns={result.columns} rows={result.rows} title="sql-console" sql={result.sql} compact />}
        </div>
        {err && <p className="err">{err}</p>}
        {result && (
          <div className="result">
            <div className="tabs">
              <span className="meta">
                {result.row_count} row{result.row_count === 1 ? "" : "s"}
                {result.truncated ? " (capped)" : ""} · {result.elapsed_ms} ms
                {result.warnings.length > 0 ? ` · ${result.warnings.join("; ")}` : ""}
                {result.masked_columns.length > 0 ? ` · masked: ${result.masked_columns.join(", ")}` : ""}
              </span>
            </div>
            {explain && (
              <div className="explain">
                <p>{explain.summary}</p>
                {explain.insights.map((i, k) => (
                  <p key={k} className="insight">
                    • {i}
                  </p>
                ))}
              </div>
            )}
            <div className="panel table-wrap">
              <table>
                <thead>
                  <tr>
                    {result.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.slice(0, 300).map((row, i) => (
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
              {result.rows.length > 300 && <p className="muted small">Showing 300 of {result.row_count}; export for the rest.</p>}
            </div>
            <details>
              <summary className="small muted">SQL as executed</summary>
              <pre className="sql">{result.sql}</pre>
            </details>
          </div>
        )}
      </section>
    </div>
  );
}
