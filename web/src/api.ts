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
};

export type Catalog = {
  metrics: { id: string; label: string; description: string; format: string; aliases: string[] }[];
  dimensions: { id: string; column: string; aliases: string[]; values: string[] }[];
  rules: string[];
  as_of: string;
};

export async function ask(question: string, pending_question: string, history: { role: string; content: string }[], session_id: string): Promise<ChatResult> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, pending_question, history, session_id }),
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
