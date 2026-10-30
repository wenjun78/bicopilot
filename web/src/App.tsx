import { ClipboardEvent, DragEvent, FormEvent, useEffect, useRef, useState } from "react";
import {
  API_BASE,
  ask,
  getCatalog,
  getDictionary,
  getHealth,
  LimitError,
  uploadAttachment,
  type Attachment,
  type Catalog,
  type ChatResult,
  type Dictionary,
  type Health,
  type Role,
} from "./api";
import { DictionaryView } from "./components/DictionaryView";
import { ExportMenu } from "./components/ExportMenu";
import { ResultView } from "./components/ResultView";
import { SqlConsole } from "./components/SqlConsole";
import { demoAsk, loadDemo, type DemoBundle } from "./demo";

type Turn = { role: "user" | "copilot"; text: string; result?: ChatResult; attachments?: Attachment[] };
type Tab = "chat" | "console" | "dictionary";

const DEFAULT_SUGGESTIONS = [
  "revenue by state 2017",
  "top 5 categories by revenue last quarter",
  "late delivery rate by region 2018",
  "average review score by category group",
  "revenue by region year over year 2018",
  "what is the delivery performance",
];
const LINKS = { source: "https://github.com/wenjun78/bicopilot", caseStudy: "https://wenjun78.github.io/portfolio/bicopilot.html" };
const newSessionId = () => `web-${Math.random().toString(36).slice(2, 10)}`;

function readPref<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}
function writePref(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode */
  }
}

function prettyDate(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** Split the copilot's answer into the statement, the bullet insights and the trailing notes. */
function splitAnswer(text: string) {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const statement: string[] = [];
  const insights: string[] = [];
  const notes: string[] = [];
  for (const line of lines) {
    if (line.startsWith("- ") || line.startsWith("• ")) insights.push(line.slice(2));
    else if (/^(Note:|From the data dictionary)/.test(line)) notes.push(line.replace(/^Note:\s*/, ""));
    else if (insights.length === 0 && notes.length === 0) statement.push(line);
    else notes.push(line);
  }
  return { statement: statement.join(" "), insights, notes };
}

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState("");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [dictionary, setDictionary] = useState<Dictionary | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [waking, setWaking] = useState(false);
  const [questionsLeft, setQuestionsLeft] = useState<number | null>(null);
  const [showCatalog, setShowCatalog] = useState(false);
  const [demo, setDemo] = useState<DemoBundle | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [role, setRole] = useState<Role>(() => readPref<Role>("bicopilot.role", "analyst"));
  const [sessionId, setSessionId] = useState(newSessionId);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [consoleSql, setConsoleSql] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const suggestions = demo?.suggestions ?? DEFAULT_SUGGESTIONS;
  const live = !!health;
  const asOf = health?.as_of ?? demo?.as_of;

  useEffect(() => {
    // Anonymous page-load tracking; never send raw IP, user agent, or identity.
    // localStorage identifies this browser; the session id identifies this conversation.
    // Failures are silent: analytics must never prevent the public demo from loading.
    try {
      const origin = API_BASE || window.location.origin;
      const visitorKey = "bicopilot_visitor_v1";
      const identifier = () => {
        const bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
      };
      let visitorId = localStorage.getItem(visitorKey);
      if (!visitorId) {
        visitorId = identifier();
        localStorage.setItem(visitorKey, visitorId);
      }
      const referrer = document.referrer
        ? (() => {
            try {
              return new URL(document.referrer).hostname;
            } catch {
              return "other";
            }
          })()
        : "direct";
      void fetch(origin + "/analytics/visit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visitor_id: visitorId, session_id: sessionId, path: window.location.pathname, referrer }),
        keepalive: true,
      }).catch(() => undefined);
    } catch {
      /* private mode and blocked storage are supported */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Live API when one is configured and answers; the pre-computed bundle covers the gap while a
    // free-tier server wakes up (up to ~90 s) and takes over entirely if the API never answers.
    const useDemo = () => loadDemo(import.meta.env.BASE_URL).then(setDemo).catch(() => setDemo(null));
    if (!API_BASE && window.location.hostname.endsWith(".github.io")) {
      useDemo();
      return;
    }
    let cancelled = false;
    const started = Date.now();
    const attempt = () =>
      getHealth()
        .then((h) => {
          if (cancelled) return;
          setHealth(h);
          setWaking(false);
          getCatalog().then(setCatalog).catch(() => undefined);
        })
        .catch(() => {
          if (cancelled) return;
          if (Date.now() - started < 90_000) {
            setWaking(true);
            if (!demo) useDemo();
            setTimeout(attempt, 5000);
          } else {
            setHealth(null);
            setWaking(false);
            if (!demo) useDemo();
          }
        });
    attempt();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (live) getDictionary(role).then(setDictionary).catch(() => setDictionary(null));
  }, [live, role]);

  useEffect(() => {
    if (turns.length > 0 || busy) bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  useEffect(() => writePref("bicopilot.role", role), [role]);

  async function addFiles(files: FileList | File[]) {
    const list = Array.from(files).slice(0, 4 - attachments.length);
    if (!list.length) return;
    if (!live) {
      setTurns((t) => [...t, { role: "copilot", text: "Attachments need the live API, which is not reachable right now." }]);
      return;
    }
    setUploading(true);
    try {
      for (const f of list) {
        const a = await uploadAttachment(f);
        setAttachments((cur) => [...cur, a]);
      }
    } catch (e) {
      setTurns((t) => [...t, { role: "copilot", text: `The attachment could not be read: ${(e as Error).message}` }]);
    } finally {
      setUploading(false);
    }
  }

  function onPaste(e: ClipboardEvent<HTMLInputElement>) {
    const files = Array.from(e.clipboardData.files || []);
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  }

  function onDrop(e: DragEvent<HTMLFormElement>) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  }

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    const sent = attachments;
    setAttachments([]);
    setTurns((t) => [...t, { role: "user", text: q, attachments: sent }]);
    // The previous turns travel with the question so follow-ups and clarification replies resolve; "New chat" drops them.
    const history = turns.slice(-6).map((t) => ({ role: t.role === "user" ? "user" : "assistant", content: t.text }));
    try {
      const r = live
        ? await ask(q, pending, history, sessionId, { role, attachments: sent })
        : demo
          ? await new Promise<ChatResult>((res) => setTimeout(() => res(demoAsk(demo, q, pending)), 250))
          : (() => {
              throw new Error("the server is not reachable yet; try again in a moment");
            })();
      if (typeof r.questions_left === "number") setQuestionsLeft(r.questions_left);
      setPending(r.status === "clarify" ? r.pending_question : "");
      setTurns((t) => [...t, { role: "copilot", text: r.answer, result: { ...r, id: live ? r.id : `demo-${t.length}` } }]);
    } catch (e) {
      if (e instanceof LimitError) {
        if (typeof e.questionsLeft === "number") setQuestionsLeft(e.questionsLeft);
        setTurns((t) => [...t, { role: "copilot", text: e.message }]);
      } else {
        setTurns((t) => [...t, { role: "copilot", text: `The request failed: ${(e as Error).message}` }]);
      }
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  function newChat() {
    if (busy) return;
    setTurns([]);
    setPending("");
    setAttachments([]);
    setInput("");
    setSessionId(newSessionId());
    setTab("chat");
  }

  function openInConsole(sql: string) {
    setConsoleSql(sql);
    setTab("console");
  }

  function exportConversation() {
    const body = turns.map((t) => ({ role: t.role, text: t.text, result: t.result ? { sql: t.result.sql, columns: t.result.columns, rows: t.result.rows } : undefined }));
    const md = body
      .map((t) => {
        const head = t.role === "user" ? "**You**" : "**Copilot**";
        const lines = [`${head}: ${t.text}`, ""];
        if (t.result?.sql) lines.push("```sql", t.result.sql, "```", "");
        if (t.result?.columns?.length && t.result.rows?.length) {
          lines.push(`| ${t.result.columns.join(" | ")} |`, `|${t.result.columns.map(() => "---|").join("")}`);
          for (const r of t.result.rows.slice(0, 50)) lines.push(`| ${r.map((v) => (v === null ? "" : String(v))).join(" | ")} |`);
          lines.push("");
        }
        return lines.join("\n");
      })
      .join("\n");
    const blob = new Blob([`# BI Copilot conversation\n\n${md}`], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "conversation.md";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  const statusLabel = health ? "Live" : waking ? "Waking the server" : demo ? "Static demo" : "Server unreachable";
  const statusTitle = health
    ? `${health.llm === "fake" ? "Deterministic compiler" : `Model: ${health.llm}`} on ${health.dialect}, ${health.vector_backend} vectors${health.reranker && health.reranker !== "none" ? `, ${health.reranker} rerank` : ""}`
    : waking
      ? "Free-tier server; the first visit after a quiet spell takes up to a minute. Answers come from the pre-computed bundle meanwhile."
      : demo
        ? "Answers were produced by the real pipeline ahead of time; this page has no server behind it."
        : "";

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href={import.meta.env.BASE_URL} onClick={(e) => { e.preventDefault(); setTab("chat"); }}>
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">BI Copilot</span>
          <span className="brand-sub">Olist e-commerce warehouse</span>
        </a>
        <nav className="sections" aria-label="Sections">
          {(["chat", "console", "dictionary"] as Tab[]).map((t) => (
            <button key={t} type="button" className={`section-btn ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
              {t === "chat" ? "Chat" : t === "console" ? "SQL console" : "Data dictionary"}
            </button>
          ))}
        </nav>
        <div className="topbar-right">
          <span className="status" title={statusTitle}>
            <span className={`dot ${health ? "live" : waking ? "waking" : demo ? "" : "off"}`} />
            {statusLabel}
            {asOf && <span className="status-detail">as of {prettyDate(asOf)}</span>}
            {questionsLeft !== null && <span className="status-detail">{questionsLeft} questions left today</span>}
          </span>
          <label className="role" title="Viewers see customer ids and review text masked in SQL, rows and prompts.">
            Role
            <select className="select" value={role} onChange={(e) => setRole(e.target.value as Role)}>
              <option value="analyst">Analyst</option>
              <option value="viewer">Viewer (masked)</option>
            </select>
          </label>
        </div>
      </header>

      {tab === "console" && (
        <main className="canvas wide">
          <SqlConsole role={role} dictionary={dictionary} live={live} initialSql={consoleSql} onConsumedInitial={() => setConsoleSql("")} />
        </main>
      )}
      {tab === "dictionary" && (
        <main className="canvas wide">
          <DictionaryView dictionary={dictionary} onUseInConsole={openInConsole} />
        </main>
      )}

      {tab === "chat" && (
        <main className="canvas">
          {showCatalog && catalog && (
            <section className="catalog" aria-label="Metrics and rules">
              <div>
                <h3>Metrics ({catalog.metrics.length})</h3>
                <ul>
                  {catalog.metrics.map((m) => (
                    <li key={m.id}>
                      <strong>{m.label}</strong> <span className="muted">{m.description}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3>Dimensions ({catalog.dimensions.length})</h3>
                <ul>
                  {catalog.dimensions.map((d) => (
                    <li key={d.id}>
                      <strong>{d.id.replace(/_/g, " ")}</strong>
                      {d.values.length > 0 && <div className="values">{d.values.join(", ")}</div>}
                    </li>
                  ))}
                </ul>
                <h3>Rules</h3>
                <ul>
                  {catalog.rules.map((r, i) => (
                    <li key={i} className="small">
                      {r}
                    </li>
                  ))}
                </ul>
              </div>
            </section>
          )}

          <div className="transcript">
            {turns.length === 0 && (
              <section className="hero">
                <h1>Ask the Olist warehouse.</h1>
                <p className="lede">
                  99,441 real Brazilian e-commerce orders from 2016 to 2018. Every answer comes with the SQL that produced it,
                  the rows, a chart and the trace of the agents that built it.
                </p>
                {demo && !health && !waking && (
                  <p className="lede note">
                    This page has no server behind it: the {demo.questions.length} answers below were produced by the real pipeline
                    ahead of time. The live app answers any question.
                  </p>
                )}
                {waking && <p className="lede note">The server is waking up (free tier, up to a minute). Pre-computed answers are served meanwhile.</p>}
                <div className="facts">
                  <div className="fact"><strong>BRL</strong><span>all amounts</span></div>
                  <div className="fact"><strong>{asOf ? prettyDate(asOf) : "31 Aug 2018"}</strong><span>“last quarter” counts back from here</span></div>
                  <div className="fact"><strong>Delivered orders</strong><span>revenue excludes cancellations</span></div>
                  <div className="fact"><strong>{catalog ? catalog.metrics.length : 20} metrics</strong><span>governed by the semantic layer</span></div>
                </div>
                <div className="starters">
                  <h2>Start with one of these</h2>
                  <div className="starter-list">
                    {(showAll && demo && !health ? demo.questions.map((r) => r.question) : suggestions).map((s) => (
                      <button key={s} type="button" className="starter" onClick={() => send(s)}>
                        {s}
                        <span className="go">Ask</span>
                      </button>
                    ))}
                  </div>
                  <div className="starters-foot">
                    {demo && !health && (
                      <button type="button" className="textlink" onClick={() => setShowAll((v) => !v)}>
                        {showAll ? "Show fewer" : `Show all ${demo.questions.length} questions`}
                      </button>
                    )}
                    {live && catalog && (
                      <button type="button" className="textlink" onClick={() => setShowCatalog((s) => !s)}>
                        {showCatalog ? "Hide the metrics and rules" : "Browse the metrics and rules"}
                      </button>
                    )}
                    {live && <span className="hint">You can also paste a PDF, screenshot or CSV into the box and ask about it.</span>}
                  </div>
                </div>
              </section>
            )}

            {turns.map((t, i) => {
              if (t.role === "user") {
                return (
                  <article key={i} className="exchange">
                    <h2 className="q">
                      <span>{t.text}</span>
                      {t.attachments && t.attachments.length > 0 && (
                        <span className="att-list">
                          {t.attachments.map((a, k) => (
                            <span key={k} className="chip static" title={a.note || `${a.chars} characters read`}>
                              {a.filename}
                            </span>
                          ))}
                        </span>
                      )}
                    </h2>
                    {/* the copilot's reply, if it has arrived, follows as the next turn */}
                  </article>
                );
              }
              const r = t.result;
              const parts = splitAnswer(t.text);
              const isDemo = !!r && r.id.startsWith("demo");
              const badge = !r || r.status === "refused" ? null : isDemo ? ["Pre-computed", ""] : r.cached ? ["Live, cached", "live"] : r.llm.startsWith("fake (fallback)") ? ["Deterministic fallback", "warn"] : ["Live", "live"];
              return (
                <article key={i} className="exchange">
                  <div className="a">
                    {badge && (
                      <span className={`badge ${badge[1]}`}>
                        {badge[0]}
                        {r?.role === "viewer" ? ", viewer" : ""}
                      </span>
                    )}
                    {parts.statement && <p className={`statement ${r?.status === "answered" ? "" : "plain"}`}>{parts.statement}</p>}
                    {parts.insights.length > 0 && (
                      <ul className="insights">
                        {parts.insights.map((s, k) => (
                          <li key={k}>{s}</li>
                        ))}
                      </ul>
                    )}
                    {parts.notes.length > 0 && (
                      <div className="notes">
                        {parts.notes.map((n, k) => (
                          <p key={k} className="note">
                            {n}
                          </p>
                        ))}
                      </div>
                    )}
                    {r?.status === "clarify" && r.clarification && (
                      <div className="options">
                        {r.clarification.options.map((o) => (
                          <button key={o} type="button" className="option" onClick={() => send(o)}>
                            {o}
                          </button>
                        ))}
                      </div>
                    )}
                    {r?.status === "answered" && (
                      <>
                        <ResultView r={r} />
                        <div className="foot">
                          <ExportMenu columns={r.columns} rows={r.rows} title={r.question} sql={r.sql ?? ""} compact />
                          {r.sql && live && (
                            <button type="button" className="chip" onClick={() => openInConsole(r.sql!)}>
                              Open in SQL console
                            </button>
                          )}
                          <span className="spacer" />
                          <span>
                            {(r.total_ms / 1000).toFixed(1)} s, {r.attempts.length} attempt{r.attempts.length === 1 ? "" : "s"}
                            {r.critic ? `, critic ${r.critic.verdict === "pass" ? "passed" : r.critic.verdict}` : ""}
                          </span>
                        </div>
                      </>
                    )}
                  </div>
                </article>
              );
            })}

            {busy && (
              <div className="working" role="status" aria-live="polite">
                <div className="stages">
                  <span><i />Planner</span>
                  <span><i />SQL agent</span>
                  <span><i />Analyst</span>
                  <span><i />Critic</span>
                </div>
                <div className="progress" />
              </div>
            )}
            <div ref={bottom} />
          </div>

          <div className="composer-wrap">
            <form
              className={`composer ${dragOver ? "drag" : ""}`}
              onSubmit={onSubmit}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
            >
              {attachments.length > 0 && (
                <div className="att-list pending">
                  {attachments.map((a, k) => (
                    <span key={k} className="chip static" title={a.note || `${a.chars} characters read`}>
                      {a.filename}
                      {a.note && !a.text ? " (unreadable)" : ""}
                      <button type="button" className="x" aria-label={`Remove ${a.filename}`} onClick={() => setAttachments((cur) => cur.filter((_, i) => i !== k))}>
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <div className="composer-row">
                <button type="button" className="icon-btn" title="Attach a PDF, image or CSV (or paste / drop one)" aria-label="Attach a file" onClick={() => fileInput.current?.click()} disabled={busy || uploading}>
                  {uploading ? (
                    "…"
                  ) : (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M21.4 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.2-9.19a4 4 0 0 1 5.65 5.66l-9.2 9.19a2 2 0 0 1-2.82-2.83l8.49-8.48" />
                    </svg>
                  )}
                </button>
                <input ref={fileInput} type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,.txt,.csv,.md,.json" multiple hidden onChange={(e) => e.target.files && addFiles(e.target.files)} />
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onPaste={onPaste}
                  placeholder={pending ? "Answer the clarifying question…" : demo && !live ? "Type one of the available questions, or pick one above" : "Ask about revenue, deliveries, reviews, sellers…"}
                  disabled={busy}
                  aria-label="Your question"
                  autoFocus={typeof window !== "undefined" && window.matchMedia("(min-width: 640px)").matches}
                />
                <button type="submit" className="send" disabled={busy || !input.trim()}>
                  Ask
                </button>
              </div>
            </form>
            <div className="composer-foot">
              <span className="links">
                {turns.length > 0 && (
                  <>
                    <button type="button" className="textlink" onClick={newChat} disabled={busy} title="Clears the conversation and the context sent with each question">
                      New chat
                    </button>
                    <button type="button" className="textlink" onClick={exportConversation} title="Download the conversation as Markdown">
                      Export chat
                    </button>
                  </>
                )}
              </span>
              <span className="links">
                <a className="textlink" href={LINKS.caseStudy}>Case study</a>
                <a className="textlink" href={LINKS.source} target="_blank" rel="noopener noreferrer">Source</a>
              </span>
            </div>
          </div>
        </main>
      )}
    </div>
  );
}
