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

  useEffect(() => {
    // Anonymous page-load tracking; never send raw IP, user agent, or identity.
    // localStorage identifies this browser; sessionStorage identifies the current tab.
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
      const referrer = document.referrer ? (() => {
        try { return new URL(document.referrer).hostname; }
        catch { return "other"; }
      })() : "direct";
      void fetch(origin + "/analytics/visit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          visitor_id: visitorId,
          session_id: sessionId,
          path: window.location.pathname,
          referrer,
        }),
        keepalive: true,
      }).catch(() => undefined);
    } catch { /* private mode and blocked storage are supported */ }
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
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  useEffect(() => writePref("bicopilot.role", role), [role]);

  async function addFiles(files: FileList | File[]) {
    const list = Array.from(files).slice(0, 4 - attachments.length);
    if (!list.length) return;
    if (!live) {
      setTurns((t) => [...t, { role: "copilot", text: "Attachments need the live API; it is not reachable right now." }]);
      return;
    }
    setUploading(true);
    try {
      for (const f of list) {
        const a = await uploadAttachment(f);
        setAttachments((cur) => [...cur, a]);
      }
    } catch (e) {
      setTurns((t) => [...t, { role: "copilot", text: `Attachment failed: ${(e as Error).message}` }]);
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
              throw new Error("the API is not reachable yet - please try again in a moment");
            })();
      if (typeof r.questions_left === "number") setQuestionsLeft(r.questions_left);
      setPending(r.status === "clarify" ? r.pending_question : "");
      setTurns((t) => [...t, { role: "copilot", text: r.answer, result: { ...r, id: live ? r.id : `demo-${t.length}` } }]);
    } catch (e) {
      if (e instanceof LimitError) {
        if (typeof e.questionsLeft === "number") setQuestionsLeft(e.questionsLeft);
        setTurns((t) => [...t, { role: "copilot", text: e.message }]);
      } else {
        setTurns((t) => [...t, { role: "copilot", text: `Request failed: ${(e as Error).message}` }]);
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

  return (
    <div className="app">
      <header>
        <div>
          <h1>BI Copilot</h1>
          <p className="tagline">Ask a real e-commerce warehouse in plain language. Governed metrics, inspectable SQL, multi-agent trace.</p>
        </div>
        <div className="status">
          {health ? (
            <>
              <span className="live">● live</span>
              <span>{health.llm === "fake" ? "deterministic compiler" : `llm: ${health.llm}`}</span>
              <span>
                {health.dialect} · {health.vector_backend}
                {health.reranker && health.reranker !== "none" ? ` · ${health.reranker} rerank` : ""}
              </span>
              <span>as of {health.as_of}</span>
              {questionsLeft !== null && <span>{questionsLeft} questions left today</span>}
            </>
          ) : waking ? (
            <>
              <span title="Free-tier server; the first visit after idle takes up to a minute.">waking the server…</span>
              {demo && <span>answering from the pre-computed bundle meanwhile</span>}
            </>
          ) : demo ? (
            <>
              <span title="Outputs were produced by the real pipeline ahead of time; this page has no backend.">static demo · pre-computed</span>
              <span>as of {demo.as_of}</span>
            </>
          ) : (
            <span className="err">API unreachable</span>
          )}
          <a className="link" href={LINKS.caseStudy}>
            case study
          </a>
          <a className="link" href={LINKS.source} target="_blank" rel="noopener noreferrer">
            source ↗
          </a>
        </div>
      </header>

      <nav className="tabbar" aria-label="Sections">
        {(["chat", "console", "dictionary"] as Tab[]).map((t) => (
          <button key={t} type="button" className={`tabbtn ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t === "chat" ? "Chat" : t === "console" ? "SQL console" : "Data dictionary"}
          </button>
        ))}
        <span className="spacer" />
        <label className="toggle" title="Viewer: personal and free-text columns are masked in SQL, rows and prompts (three-layer enforcement).">
          role
          <select className="select" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="analyst">analyst</option>
            <option value="viewer">viewer (masked)</option>
          </select>
        </label>
        {(turns.length > 0 || pending) && (
          <button className="link" onClick={newChat} disabled={busy} title="Start over: clears the conversation and the context sent with each question">
            new chat
          </button>
        )}
        {live && (
          <button className="link" onClick={() => setShowCatalog((s) => !s)}>
            {showCatalog ? "hide metrics" : "metrics & rules"}
          </button>
        )}
        {turns.length > 0 && tab === "chat" && (
          <button className="link" onClick={exportConversation} title="Download the conversation as Markdown">
            export chat
          </button>
        )}
      </nav>

      {showCatalog && catalog && tab === "chat" && (
        <section className="catalog">
          <div>
            <h3>Metrics</h3>
            <ul>
              {catalog.metrics.map((m) => (
                <li key={m.id}>
                  <strong>{m.label}</strong> <span className="muted">{m.description}</span>
                  <div className="small muted">aliases: {m.aliases.join(", ")}</div>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3>Dimensions</h3>
            <ul>
              {catalog.dimensions.map((d) => (
                <li key={d.id}>
                  <strong>{d.id}</strong> <span className="muted">{d.column}</span>
                  {d.values.length > 0 && <div className="small muted">{d.values.join(" · ")}</div>}
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

      {tab === "console" && (
        <SqlConsole role={role} dictionary={dictionary} live={live} initialSql={consoleSql} onConsumedInitial={() => setConsoleSql("")} />
      )}
      {tab === "dictionary" && <DictionaryView dictionary={dictionary} onUseInConsole={openInConsole} />}

      {tab === "chat" && (
        <>
          <main>
            {turns.length === 0 && (
              <div className="empty">
                {demo && !health && !waking && (
                  <p className="small">
                    Static demo: every answer below was produced by the real planner → SQL → analyst → critic pipeline over the
                    Olist e-commerce warehouse (100k real Brazilian orders) and saved ahead of time, so this page needs no server.{" "}
                    {demo.questions.length} questions are available; the clarifying-question flow works too.
                  </p>
                )}
                <p>Try one of these:</p>
                <div className="chips">
                  {(showAll && demo && !health ? demo.questions.map((r) => r.question) : suggestions).map((s) => (
                    <button key={s} className="chip" onClick={() => send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
                {demo && !health && (
                  <button className="link small" onClick={() => setShowAll((v) => !v)} style={{ marginTop: 10 }}>
                    {showAll ? "show fewer" : `show all ${demo.questions.length} questions`}
                  </button>
                )}
                {live && (
                  <p className="small muted" style={{ marginTop: 12 }}>
                    Paste or drop a PDF, screenshot or CSV into the box below and ask about it.
                  </p>
                )}
              </div>
            )}
            {turns.map((t, i) => (
              <div key={i} className={`turn ${t.role}`}>
                {t.role === "user" ? (
                  <div className="bubble user">
                    {t.text}
                    {t.attachments && t.attachments.length > 0 && (
                      <div className="att-list">
                        {t.attachments.map((a, k) => (
                          <span key={k} className="chip tiny static" title={a.note || `${a.chars} characters extracted`}>
                            📎 {a.filename}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="bubble copilot">
                    {t.result && t.result.status !== "refused" && (
                      <span className={`mode ${t.result.id.startsWith("demo") ? "" : "mode-live"}`}>
                        {t.result.id.startsWith("demo") ? "pre-computed" : t.result.cached ? "live · cached" : t.result.llm.startsWith("fake (fallback)") ? "live · deterministic fallback" : "live"}
                        {t.result.role === "viewer" ? " · viewer" : ""}
                      </span>
                    )}
                    <div className="answer">
                      {t.text.split("\n").map((line, j) => (
                        <p key={j} className={line.startsWith("Note:") || line.startsWith("From the data dictionary") ? "muted small" : line.startsWith("- ") ? "insight" : ""}>
                          {line.replace(/^- /, "• ")}
                        </p>
                      ))}
                    </div>
                    {t.result?.status === "clarify" && t.result.clarification && (
                      <div className="chips">
                        {t.result.clarification.options.map((o) => (
                          <button key={o} className="chip" onClick={() => send(o)}>
                            {o}
                          </button>
                        ))}
                      </div>
                    )}
                    {t.result?.status === "answered" && (
                      <>
                        <ResultView r={t.result} />
                        <div className="result-actions">
                          <ExportMenu columns={t.result.columns} rows={t.result.rows} title={t.result.question} sql={t.result.sql ?? ""} compact />
                          {t.result.sql && live && (
                            <button type="button" className="chip tiny" onClick={() => openInConsole(t.result!.sql!)}>
                              open in SQL console
                            </button>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            ))}
            {busy && (
              <div className="turn copilot">
                <div className="bubble copilot thinking">planner → sql agent → analyst → critic…</div>
              </div>
            )}
            <div ref={bottom} />
          </main>

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
                  <span key={k} className="chip tiny static" title={a.note || `${a.chars} characters extracted`}>
                    📎 {a.filename}
                    {a.note && !a.text ? " (unreadable)" : ""}
                    <button type="button" className="x" aria-label="remove" onClick={() => setAttachments((cur) => cur.filter((_, i) => i !== k))}>
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="composer-row">
              <button type="button" className="attach" title="Attach a PDF, image or CSV (or paste / drop one)" onClick={() => fileInput.current?.click()} disabled={busy || uploading}>
                {uploading ? "…" : "📎"}
              </button>
              <input ref={fileInput} type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,.txt,.csv,.md,.json" multiple hidden onChange={(e) => e.target.files && addFiles(e.target.files)} />
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPaste={onPaste}
                placeholder={
                  pending
                    ? "Answer the clarifying question…"
                    : demo && !live
                      ? "Type one of the available questions, or pick a chip"
                      : "e.g. top 10 categories by revenue in SP last quarter — or paste a screenshot / PDF"
                }
                disabled={busy}
                autoFocus
              />
              <button type="submit" disabled={busy || !input.trim()}>
                Ask
              </button>
            </div>
          </form>
        </>
      )}
    </div>
  );
}
