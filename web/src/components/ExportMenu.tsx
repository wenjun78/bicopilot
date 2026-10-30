import { useState } from "react";
import { EXPORT_FORMATS, exportRows, type ExportFormat } from "../api";

export function ExportMenu({ columns, rows, title, sql, compact }: { columns: string[]; rows: unknown[][]; title: string; sql: string; compact?: boolean }) {
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [err, setErr] = useState("");
  if (!columns.length || !rows.length) return null;
  async function go(fmt: ExportFormat) {
    setBusy(fmt);
    setErr("");
    try {
      await exportRows(columns, rows, fmt, title, sql);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <span className={`export ${compact ? "compact" : ""}`}>
      <span className="muted small">export</span>
      {EXPORT_FORMATS.map((f) => (
        <button key={f} type="button" className="chip tiny" disabled={busy !== null} onClick={() => go(f)} title={`Download as ${f.toUpperCase()}`}>
          {busy === f ? "…" : f}
        </button>
      ))}
      {err && <span className="err small">{err}</span>}
    </span>
  );
}
