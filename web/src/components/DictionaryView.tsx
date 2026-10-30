import { useMemo, useState } from "react";
import type { DictColumn, Dictionary } from "../api";

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(v);
}

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
      <div className="panel">
        <p className="muted">The data dictionary is served by the live API; it is not reachable right now.</p>
      </div>
    );
  }

  return (
    <div className="dictionary">
      <div className="dict-head">
        <div>
          <strong>{dictionary.dataset}</strong>
          <div className="muted small">
            {dictionary.tables.length} tables · {dictionary.tables.reduce((n, t) => n + t.columns.length, 0)} columns · {dictionary.currency} · as of {dictionary.as_of}
          </div>
        </div>
        <input className="search" placeholder="search columns, descriptions, metrics…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      {q.trim() ? (
        <div className="panel">
          <p className="muted small">{matches.length} matching columns</p>
          {matches.map(({ table: tn, col }) => (
            <ColumnRow key={`${tn}.${col.name}`} table={tn} col={col} onUseInConsole={onUseInConsole} />
          ))}
        </div>
      ) : (
        <div className="dict-body">
          <nav className="dict-nav">
            {tables.map((t) => (
              <button key={t.name} type="button" className={`dict-tab ${current?.name === t.name ? "active" : ""}`} onClick={() => setTable(t.name)}>
                <span className={`kind ${t.kind}`}>{t.kind === "fact" ? "F" : "D"}</span> {t.name}
                <span className="muted small"> {t.row_count.toLocaleString()}</span>
              </button>
            ))}
            <div className="dict-notes">
              <strong className="small">Relationships</strong>
              <ul className="small muted">
                {dictionary.relationships.map((r, i) => (
                  <li key={i}>
                    <code>{r}</code>
                  </li>
                ))}
              </ul>
              <strong className="small">Data quality notes</strong>
              <ul className="small muted">
                {dictionary.quality_notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </div>
          </nav>
          {current && (
            <div className="dict-table">
              <h3>
                <code>{current.name}</code> <span className="muted small">{current.kind} · {current.row_count.toLocaleString()} rows</span>
              </h3>
              <p>{current.description}</p>
              <p className="small muted">
                Grain: {current.grain || "—"} · Source: {current.source || "—"}
              </p>
              <button type="button" className="chip" onClick={() => onUseInConsole(`SELECT *\nFROM ${current.name}\nLIMIT 20`)}>
                Open in SQL console
              </button>
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

function ColumnRow({ table, col, onUseInConsole }: { table: string; col: DictColumn; onUseInConsole: (sql: string) => void }) {
  const profileSql = `SELECT ${col.name}, COUNT(*) AS n\nFROM ${table}\nGROUP BY ${col.name}\nORDER BY n DESC\nLIMIT 20`;
  return (
    <div className="dict-col">
      <div className="dict-col-head">
        <code>{col.name}</code> <span className="muted small">{col.type.toLowerCase()}</span>
        {col.primary_key && <span className="tag">pk</span>}
        {col.joins_to && <span className="tag">→ {col.joins_to}</span>}
        {col.unit && <span className="tag">{col.unit}</span>}
        {col.masked_for_viewer && <span className="tag warn">masked for viewer</span>}
        <span className="spacer" />
        <button type="button" className="link small" onClick={() => onUseInConsole(profileSql)}>
          profile in console
        </button>
      </div>
      <div className="small">{col.description || <span className="muted">no description</span>}</div>
      <div className="small muted">
        {col.distinct !== null && `${col.distinct?.toLocaleString()} distinct`}
        {col.null_pct !== null && col.null_pct !== undefined && ` · ${col.null_pct}% null`}
        {col.min !== null && col.min !== undefined && ` · ${fmt(col.min)} – ${fmt(col.max)}`}
        {col.used_by.length > 0 && ` · used by ${col.used_by.map((u) => u.split(":")[1]).join(", ")}`}
      </div>
      {col.samples.length > 0 && (
        <div className="samples small">
          {col.samples.map((s) => (
            <span key={s.value} className="chip tiny static" title={s.count !== null ? `${s.count.toLocaleString()} rows` : ""}>
              {s.value}
              {s.count !== null && <span className="muted"> {s.count.toLocaleString()}</span>}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
