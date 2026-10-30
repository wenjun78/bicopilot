export const API_BASE: string = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") || "";

export type ChartSpec = { type: "bar" | "line" | "table" | "kpi"; x?: string | null; y: string[]; title?: string };
export type Analysis = { summary: string; insights: string[]; chart: ChartSpec; caveats: string[] };
export type TraceEvent = { node: string; ms: number; [k: string]: unknown };
export type Attempt = { sql: string; error: string; stage: string; rows?: number };
export type ToolStep = { tool: string; args: Record<string, unknown>; observation: string };

export type ChatResult = {
  id: string;
  question: string;
  intent: string | null;
  status: "answered" | "clarify" | "refused" | "error" | "greeting" | "meta";
  answer: string;
  clarification: { question: string; options: string[] } | null;
  pending_question: string;
  plan: Record<string, unknown> | null;
  sql: string | null;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  analysis: Analysis | null;
  critic: { verdict: string; reason: string } | null;
  attempts: Attempt[];
  tool_steps?: ToolStep[];
  retrieval: {
    backend: string;
    reranker?: string;
    hits: { id: string; score: number; bm25_rank: number | null; dense_rank: number | null; structured: boolean; rerank?: { score: number; before: number; after: number } | null }[];
  } | null;
  trace: TraceEvent[];
  total_ms: number;
  as_of: string;
  llm: string;
  cached?: boolean;
  fallback_error?: string;
  questions_left?: number;
  role?: string;
  masked_columns?: string[];
  attachments?: { kind: string; filename: string; chars: number; note?: string }[];
};

export type Catalog = {
  metrics: { id: string; label: string; description: string; format: string; aliases: string[] }[];
  dimensions: { id: string; column: string; aliases: string[]; values: string[] }[];
  rules: string[];
  as_of: string;
};

export type AskOptions = { role?: Role; retain_memory?: boolean; attachments?: Attachment[] };

export async function ask(question: string, pending_question: string, history: { role: string; content: string }[], session_id: string, opts: AskOptions = {}): Promise<ChatResult> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, pending_question, history, session_id, role: opts.role ?? "analyst", retain_memory: opts.retain_memory ?? true, attachments: opts.attachments ?? [] }),
  });
  if (res.status === 429) {
    const body = await res.json().catch(() => ({}));
    throw new LimitError(body.detail || "Too many questions right now.", body.code || "rate", body.questions_left);
  }
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export class LimitError extends Error {
  code: string;
  questionsLeft?: number;
  constructor(message: string, code: string, questionsLeft?: number) {
    super(message);
    this.code = code;
    this.questionsLeft = questionsLeft;
  }
}

export async function getCatalog(): Promise<Catalog> {
  const res = await fetch(`${API_BASE}/catalog`);
  if (!res.ok) throw new Error(`catalog ${res.status}`);
  return res.json();
}

export type Health = { ok: boolean; as_of: string; llm: string; vector_backend: string; reranker?: string; cache?: string; dialect: string };

export async function getHealth(timeoutMs = 8000): Promise<Health> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const res = await fetch(`${API_BASE}/healthz`, { signal: ctrl.signal }).finally(() => clearTimeout(timer));
  if (!res.ok) throw new Error(`health ${res.status}`);
  return res.json();
}

// ----- console, dictionary, export, attachments -------------------------------------------------
export type Role = "analyst" | "viewer";

export type SqlResult = {
  id: string;
  sql: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
  warnings: string[];
  masked_columns: string[];
  role: string;
  total_ms: number;
};

export async function runSql(sql: string, role: Role): Promise<SqlResult> {
  const res = await fetch(`${API_BASE}/sql`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sql, role }) });
  if (res.status === 429) {
    const body = await res.json().catch(() => ({}));
    throw new LimitError(body.detail || "Too many queries right now.", body.code || "rate", body.questions_left);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `${res.status}`);
  }
  return res.json();
}

export async function explainSql(sql: string, columns: string[], rows: unknown[][], role: Role): Promise<{ analysis: Analysis | null }> {
  const res = await fetch(`${API_BASE}/sql/explain`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql, columns, rows: rows.slice(0, 500), role }),
  });
  if (!res.ok) throw new Error(`explain ${res.status}`);
  return res.json();
}

export type DictColumn = {
  name: string;
  type: string;
  primary_key: boolean;
  nullable: boolean;
  description: string;
  unit: string;
  joins_to: string;
  masked_for_viewer: boolean;
  used_by: string[];
  distinct: number | null;
  null_pct: number | null;
  min: unknown;
  max: unknown;
  samples: { value: string; count: number | null }[];
};
export type DictTable = { name: string; kind: "fact" | "dimension"; description: string; grain: string; source: string; row_count: number; columns: DictColumn[] };
export type Dictionary = { dataset: string; currency: string; as_of: string; tables: DictTable[]; relationships: string[]; quality_notes: string[] };

export async function getDictionary(role: Role): Promise<Dictionary> {
  const res = await fetch(`${API_BASE}/dictionary?role=${role}`);
  if (!res.ok) throw new Error(`dictionary ${res.status}`);
  return res.json();
}

export type ExportFormat = "csv" | "xlsx" | "json" | "txt" | "md";
export const EXPORT_FORMATS: ExportFormat[] = ["csv", "xlsx", "json", "txt", "md"];

export async function exportRows(columns: string[], rows: unknown[][], format: ExportFormat, title: string, sql: string): Promise<void> {
  if (!API_BASE && !window.location.hostname.match(/localhost|127\.0\.0\.1/)) {
    // static demo: build the file in the browser for csv/json/txt/md
    return downloadLocal(columns, rows, format, title, sql);
  }
  const res = await fetch(`${API_BASE}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ columns, rows, format, title, sql }),
  });
  if (!res.ok) {
    if (format === "xlsx") throw new Error("Excel export needs the live API");
    return downloadLocal(columns, rows, format, title, sql);
  }
  const blob = await res.blob();
  const name = (res.headers.get("content-disposition")?.match(/filename="([^"]+)"/)?.[1] ?? `result.${format}`).toString();
  triggerDownload(blob, name);
}

export function downloadLocal(columns: string[], rows: unknown[][], format: ExportFormat, title: string, sql: string): void {
  const slug = (title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "result").slice(0, 60);
  const cell = (v: unknown) => (v === null || v === undefined ? "" : String(v));
  let text = "";
  let mime = "text/plain";
  if (format === "csv") {
    const esc = (v: unknown) => {
      const s = cell(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    text = [columns.map(esc).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\n");
    mime = "text/csv";
  } else if (format === "json") {
    text = JSON.stringify({ title, columns, rows: rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]]))), sql }, null, 2);
    mime = "application/json";
  } else if (format === "md") {
    text = [`# ${title}`, "", `| ${columns.join(" | ")} |`, `|${columns.map(() => "---|").join("")}`, ...rows.map((r) => `| ${r.map(cell).join(" | ")} |`), "", "```sql", sql, "```"].join("\n");
    mime = "text/markdown";
  } else if (format === "xlsx") {
    throw new Error("Excel export needs the live API");
  } else {
    const widths = columns.map((c, i) => Math.max(c.length, ...rows.map((r) => cell(r[i]).length)));
    text = [title, "", columns.map((c, i) => c.padEnd(widths[i])).join("  "), widths.map((w) => "-".repeat(w)).join("  "), ...rows.map((r) => r.map((v, i) => cell(v).padEnd(widths[i])).join("  ")), "", `${rows.length} rows`, "", "SQL:", sql].join("\n");
  }
  triggerDownload(new Blob([text], { type: mime }), `${slug}.${format}`);
}

function triggerDownload(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export type Attachment = { kind: "pdf" | "image" | "text"; filename: string; chars: number; text: string; note?: string };

export async function uploadAttachment(file: File): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file, file.name || "pasted");
  const res = await fetch(`${API_BASE}/attachments`, { method: "POST", body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `${res.status}`);
  }
  return res.json();
}
