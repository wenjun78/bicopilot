import { useState } from "react";
import { EXPORT_FORMATS, exportRows, type ExportFormat } from "../api";

const NAMES: Record<ExportFormat, string> = { csv: "CSV", xlsx: "Excel", json: "JSON", txt: "Text", md: "Markdown" };

export function ExportMenu({ columns, rows, title, sql }: { columns: string[]; rows: unknown[][]; title: string; sql: string; compact?: boolean }) {
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
    <span className="export">
      <span className="label">Download</span>
      {EXPORT_FORMATS.map((f) => (
        <button key={f} type="button" className="chip" disabled={busy !== null} onClick={() => go(f)} title={`Download these rows as ${NAMES[f]}`}>
          {busy === f ? "…" : NAMES[f]}
        </button>
      ))}
      {err && <span className="err small">{err}</span>}
    </span>
  );
}
