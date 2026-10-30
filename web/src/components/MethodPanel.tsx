import { useState } from "react";
import type { MethodDetail } from "../api";

/** "How this was calculated": the analyst's one-line method plus the semantic layer's own account. */
export function MethodPanel({ method }: { method: MethodDetail }) {
  const [open, setOpen] = useState(false);
  const m = method.metrics[0];
  if (!m && !method.sentence) return null;
  return (
    <div className={`method ${open ? "open" : ""}`}>
      <button type="button" className="method-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="chev" aria-hidden="true" />
        How this was calculated
        {!open && method.sentence && <span className="method-peek">{method.sentence}</span>}
      </button>
      {open && (
        <div className="method-body">
          {method.sentence && <p className="method-sentence">{method.sentence}</p>}
          <dl className="method-grid">
            {method.metrics.map((x) => (
              <div key={x.id} className="method-row">
                <dt>{x.label}</dt>
                <dd>
                  {x.definition && <div>{x.definition}</div>}
                  <code className="formula">{x.formula}</code>
                  {x.filters.length > 0 && <div className="muted small">Only rows where {x.filters.map((f) => f.replace(/^\w+\./, "")).join(" and ")}</div>}
                </dd>
              </div>
            ))}
            <div className="method-row">
              <dt>Period</dt>
              <dd>
                {method.period}
                <span className="muted small"> ({method.calendar}; the data ends {method.as_of})</span>
              </dd>
            </div>
            {method.grouping.length > 0 && (
              <div className="method-row">
                <dt>Grouped by</dt>
                <dd>{method.grouping.join(", ")}</dd>
              </div>
            )}
            {method.filters.length > 0 && (
              <div className="method-row">
                <dt>Filtered to</dt>
                <dd>{method.filters.join("; ")}</dd>
              </div>
            )}
            {method.top_n && (
              <div className="method-row">
                <dt>Shown</dt>
                <dd>
                  {method.order === "asc" ? "lowest" : "highest"} {method.top_n}
                </dd>
              </div>
            )}
          </dl>
        </div>
      )}
    </div>
  );
}
