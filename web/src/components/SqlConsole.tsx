import { useEffect, useMemo, useState } from "react";
import { explainSql, runSql, type Analysis, type DictTable, type Dictionary, type Role, type SqlResult } from "../api";
import { ExportMenu } from "./ExportMenu";
import { fmt } from "./ResultView";

const EXAMPLES: { name: string; sql: string }[] = [
  {
    name: "Customers and orders by state",
    sql: "SELECT customer_state, COUNT(*) AS customers, SUM(order_count) AS orders\nFROM dim_customer\nGROUP BY customer_state\nORDER BY customers DESC",
  },
  {
    name: "Monthly revenue and freight, 2018",
    sql: "SELECT d.year_month, SUM(oi.price) AS revenue, SUM(oi.freight_value) AS freight\nFROM fact_order_items oi\nJOIN dim_date d ON oi.purchase_date = d.date_key\nWHERE oi.status = 'delivered' AND d.year = 2018\nGROUP BY d.year_month\nORDER BY d.year_month",
  },
  {
    name: "Review score by category group",
    sql: "SELECT p.category_group, AVG(o.review_score) AS avg_score, COUNT(*) AS reviewed\nFROM fact_order_items oi\nJOIN fact_orders o ON oi.order_id = o.order_id\nJOIN dim_product p ON oi.product_key = p.product_key\nWHERE o.review_score IS NOT NULL\nGROUP BY p.category_group\nORDER BY avg_score",
  },
];

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
  const [sql, setSql] = useState(EXAMPLES[0].sql);
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
      setErr("The SQL console needs the live server, which is not reachable right now.");
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

  const numeric = new Set(result ? result.columns.filter((c, i) => typeof result.rows[0]?.[i] === "number") : []);

  return (
    <div className="console">
      <aside className="schema" aria-label="Schema">
        <div className="schema-head">
          <strong>Schema</strong>
          {dictionary && (
            <span className="muted small">
              {dictionary.tables.length} tables, amounts in {dictionary.currency}. Click a column to insert it.
            </span>
          )}
        </div>
        {!dictionary && <p className="muted small">The schema loads from the live server.</p>}
        {tables.map((t: DictTable) => (
          <div key={t.name} className="schema-table">
            <button type="button" className="schema-toggle" onClick={() => setOpen(open === t.name ? null : t.name)} aria-expanded={open === t.name}>
              <span className={`kind ${t.kind}`} aria-hidden="true" /> <code>{t.name}</code>
              <span className="n">{t.row_count.toLocaleString()}</span>
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
                    {c.primary_key && <span className="tag">key</span>}
                    {c.joins_to && <span className="tag">joins {c.joins_to.split(".")[0]}</span>}
                    {c.masked_for_viewer && <span className="tag warn">masked</span>}
                    {c.samples.length > 0 && c.samples[0].count !== null && (
                      <div className="samples small muted">{c.samples.slice(0, 5).map((s) => s.value).join(", ")}</div>
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
          <select className="select" value="" onChange={(e) => e.target.value !== "" && setSql(EXAMPLES[Number(e.target.value)].sql)} aria-label="Example queries">
            <option value="">Examples</option>
            {EXAMPLES.map((ex, i) => (
              <option key={i} value={i}>
                {ex.name}
              </option>
            ))}
          </select>
          {history.length > 0 && (
            <select className="select" value="" onChange={(e) => e.target.value !== "" && setSql(history[Number(e.target.value)])} aria-label="Query history">
              <option value="">History</option>
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
          aria-label="SQL"
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") run();
          }}
        />
        <div className="editor-bar">
          <button type="button" className="primary" onClick={run} disabled={busy || !sql.trim()}>
            {busy ? "Running…" : "Run"}
          </button>
          <span className="muted small">Ctrl+Enter runs it. Read-only, one SELECT, checked by the same guard the agents use.</span>
          <span className="spacer" />
          {result && (
            <button type="button" className="chip" onClick={doExplain} disabled={explaining}>
              {explaining ? "Explaining…" : "Explain with the analyst agent"}
            </button>
          )}
        </div>
        {err && <p className="err">{err}</p>}
        {result && (
          <div className="result-card">
            <div className="tabs">
              <span className="meta">
                {result.row_count} row{result.row_count === 1 ? "" : "s"}
                {result.truncated ? " (capped)" : ""} in {result.elapsed_ms} ms
                {result.warnings.length > 0 ? `. ${result.warnings.join("; ")}` : ""}
                {result.masked_columns.length > 0 ? `. Masked: ${result.masked_columns.join(", ")}` : ""}
              </span>
            </div>
            {explain && (
              <div className="explain">
                <p className="statement">{explain.summary}</p>
                {explain.insights.map((i, k) => (
                  <p key={k} className="insight">
                    {i}
                  </p>
                ))}
              </div>
            )}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {result.columns.map((c) => (
                      <th key={c} className={numeric.has(c) ? "num" : ""}>
                        {c}
                      </th>
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
              {result.rows.length > 300 && <p className="muted small">Showing 300 of {result.row_count}; download for the rest.</p>}
            </div>
            <div className="foot">
              <ExportMenu columns={result.columns} rows={result.rows} title="sql-console" sql={result.sql} />
            </div>
            <details>
              <summary>SQL as executed</summary>
              <pre className="sql">{result.sql}</pre>
            </details>
          </div>
        )}
      </section>
    </div>
  );
}
