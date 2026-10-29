// Static demo mode: pre-computed outputs of the real pipeline (eval/export_demo.py), served from
// public/demo/results.json when no API is configured. Every answer shown is a real run; only the
// set of questions is fixed.

import type { ChatResult } from "./api";

export type DemoBundle = {
  as_of: string;
  llm: string;
  suggestions: string[];
  questions: ChatResult[];
  follow_ups: Record<string, ChatResult>;
};

const STOP = new Set(["the", "a", "an", "of", "in", "by", "for", "and", "or", "to", "is", "are", "what", "which", "show", "me", "per", "was", "were"]);

function norm(s: string): string {
  return s.toLowerCase().replace(/\s+/g, " ").trim();
}

function tokens(s: string): Set<string> {
  return new Set(
    norm(s)
      .replace(/[^a-z0-9% ]/g, " ")
      .split(" ")
      .filter((t) => t && !STOP.has(t)),
  );
}

export async function loadDemo(base: string): Promise<DemoBundle> {
  const res = await fetch(`${base}demo/results.json`);
  if (!res.ok) throw new Error(`demo bundle ${res.status}`);
  return res.json();
}

export function demoAsk(bundle: DemoBundle, question: string, pending: string): ChatResult {
  const q = norm(question);
  if (pending) {
    const hit = bundle.follow_ups[`${norm(pending)}||${q}`];
    if (hit) return hit;
  }
  const exact = bundle.questions.find((r) => norm(r.question) === q);
  if (exact) return exact;

  // A near match must contain every token the visitor typed (so no filter or period is silently
  // dropped) and add at most one token of its own.
  const qt = tokens(question);
  let best: { r: ChatResult; extra: number } | null = null;
  for (const r of bundle.questions) {
    const rt = tokens(r.question);
    if (![...qt].every((t) => rt.has(t))) continue;
    const extra = rt.size - qt.size;
    if (extra <= 1 && (!best || extra < best.extra)) best = { r, extra };
  }
  if (best) return best.r;

  const closest = [...bundle.questions]
    .map((r) => {
      const rt = tokens(r.question);
      let overlap = 0;
      for (const t of qt) if (rt.has(t)) overlap++;
      return { q: r.question, s: overlap };
    })
    .sort((a, b) => b.s - a.s)
    .slice(0, 3)
    .map((x) => x.q);
  return {
    id: "demo-miss",
    question,
    intent: null,
    status: "refused",
    answer:
      "This static demo serves pre-computed answers for a fixed set of questions, so that one is not available here. " +
      "Closest ones you can try: " +
      closest.map((c) => `"${c}"`).join(", ") +
      ". Run the repository locally to ask anything.",
    clarification: null,
    pending_question: "",
    plan: null,
    sql: null,
    columns: [],
    rows: [],
    row_count: 0,
    truncated: false,
    analysis: null,
    critic: null,
    attempts: [],
    retrieval: null,
    trace: [],
    total_ms: 0,
    as_of: bundle.as_of,
    llm: bundle.llm,
  };
}
