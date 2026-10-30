import { useMemo, useState } from "react";
import type { DictColumn, Dictionary } from "../api";
import { fmt } from "./ResultView";

export function DictionaryView({ dictionary, onUseInConsole }: { dictionary: Dictionary | null; onUseInConsole: (sql: string) => void }) {
  const [q, setQ] = useState("");
  const [table, setTable] = useState<string>("");
  const tables = dictionary?.tables ?? [];
  const current = tables.find((t) => t.name === table) ?? tables[0];

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return [];
    const out: { table: string; col: DictColumn }[] = [];
    for (const t of tables) for (const c of t.columns) if (`${t.name} ${c.name} ${c.description} ${c.used_by.join(" ")}`.toLowerCase().includes(needle)) out.push({ table: t.name, col: c });
    return out.slice(0, 40);
  }, [q, tables]);

  if (!dictionary) {
    return (
      <div className="dictionary">
        <div className="dict-head">
          <div>
            <h1>Data dictionary</h1>
            <p className="sub">The dictionary is served by the live server, which is not reachable right now.</p>
          </div>
        </div>
      </div>
    );
  }

  const columnCount = dictionary.tables.reduce((n, t) => n + t.columns.length, 0);

  return (
    <div className="dictionary">
      <div className="dict-head">
        <div>
          <h1>Data dictionary</h1>
          <p className="sub">
            {dictionary.dataset}: {dictionary.tables.length} tables and {columnCount} columns, profiled live. Amounts in {dictionary.currency}; data to {new Date(dictionary.as_of + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}.
          </p>
        </div>
        <input className="search" type="search" placeholder="Search columns, descriptions, metrics" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the dictionary" />
      </div>

      {q.trim() ? (
        <div className="search-results">
          <p className="count">{matches.length === 0 ? "No column matches that." : `${matches.length} matching column${matches.length === 1 ? "" : "s"}`}</p>
          {matches.map(({ table: tn, col }) => (
            <ColumnRow key={`${tn}.${col.name}`} table={tn} col={col} showTable onUseInConsole={onUseInConsole} />
          ))}
        </div>
      ) : (
        <div className="dict-body">
          <nav className="dict-nav" aria-label="Tables">
            {tables.map((t) => (
              <button key={t.name} type="button" className={`dict-tab ${current?.name === t.name ? "active" : ""}`} onClick={() => setTable(t.name)}>
                <span className={`kind ${t.kind}`} aria-hidden="true" /> <code>{t.name}</code>
                <span className="n">{t.row_count.toLocaleString()}</span>
              </button>
            ))}
            <div className="dict-notes">
              <h3>How the tables join</h3>
              <ul>
                {dictionary.relationships.map((r, i) => (
                  <li key={i}>
                    <code>{r}</code>
                  </li>
                ))}
              </ul>
              <h3>Known data-quality issues</h3>
              <ul>
                {dictionary.quality_notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </div>
          </nav>
          {current && (
            <div className="dict-table">
              <h2>
                <span className={`kind ${current.kind}`} aria-hidden="true" />
                <code>{current.name}</code>
                <span className="muted small">
                  {current.kind === "fact" ? "fact table" : "dimension"}, {current.row_count.toLocaleString()} rows
                </span>
              </h2>
              <p className="desc">{current.description}</p>
              <p className="grain">
                One row per {current.grain || "record"}. Source: {current.source || "Olist"}.
              </p>
              <div className="dict-actions">
                <button type="button" className="chip" onClick={() => onUseInConsole(`SELECT *\nFROM ${current.name}\nLIMIT 20`)}>
                  Preview in the SQL console
                </button>
              </div>
              <div className="dict-cols">
                {current.columns.map((c) => (
                  <ColumnRow key={c.name} table={current.name} col={c} onUseInConsole={onUseInConsole} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ColumnRow({ table, col, showTable, onUseInConsole }: { table: string; col: DictColumn; showTable?: boolean; onUseInConsole: (sql: string) => void }) {
  const profileSql = `SELECT ${col.name}, COUNT(*) AS n\nFROM ${table}\nGROUP BY ${col.name}\nORDER BY n DESC\nLIMIT 20`;
  const stats: string[] = [];
  if (col.distinct !== null && col.distinct !== undefined) stats.push(`${col.distinct.toLocaleString()} distinct`);
  if (col.null_pct !== null && col.null_pct !== undefined) stats.push(`${col.null_pct}% empty`);
  if (col.min !== null && col.min !== undefined) stats.push(`${fmt(col.min)} to ${fmt(col.max)}`);
  if (col.used_by.length > 0) stats.push(`used by ${col.used_by.map((u) => u.split(":")[1].replace(/_/g, " ")).join(", ")}`);
  return (
    <div className="dict-col">
      <div className="dict-col-head">
        {showTable && <span className="table-name">{table}.</span>}
        <code>{col.name}</code>
        <span className="type">{col.type.toLowerCase()}</span>
        {col.primary_key && <span className="tag">key</span>}
        {col.joins_to && <span className="tag">joins {col.joins_to}</span>}
        {col.unit && <span className="tag">{col.unit}</span>}
        {col.masked_for_viewer && <span className="tag warn">masked for viewers</span>}
        <span className="spacer" />
        <button type="button" className="link small" onClick={() => onUseInConsole(profileSql)}>
          Profile in console
        </button>
      </div>
      <div className="desc">{col.description || <span className="muted">No description yet.</span>}</div>
      {stats.length > 0 && <div className="stats">{stats.join(". ")}.</div>}
      {col.samples.length > 0 && (
        <div className="samples">
          {col.samples.map((s) => (
            <span key={s.value} className="sample" title={s.count !== null ? `${s.count.toLocaleString()} rows` : ""}>
              {s.value}
              {s.count !== null && <span className="n">{s.count.toLocaleString()}</span>}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
