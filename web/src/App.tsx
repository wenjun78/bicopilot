import { FormEvent, useEffect, useRef, useState } from "react";
import { API_BASE, ask, getCatalog, getHealth, LimitError, type Catalog, type ChatResult, type Health } from "./api";
import { ResultView } from "./components/ResultView";
import { demoAsk, loadDemo, type DemoBundle } from "./demo";

type Turn = { role: "user" | "copilot"; text: string; result?: ChatResult };

const DEFAULT_SUGGESTIONS = [
  "revenue by region FY2026",
  "top 5 customers by gross margin last quarter",
  "monthly revenue trend in APAC this year",
  "gross margin % by product category FY26",
  "revenue by region year over year FY2026",
  "what was the margin in EMEA",
];
const LINKS = { source: "https://github.com/wenjun78/bicopilot", caseStudy: "https://wenjun78.github.io/portfolio/bi-copilot.html" };

const sessionId = `web-${Math.random().toString(36).slice(2, 10)}`;

export default function App() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState("");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [waking, setWaking] = useState(false);
  const [questionsLeft, setQuestionsLeft] = useState<number | null>(null);
  const [showCatalog, setShowCatalog] = useState(false);
  const [demo, setDemo] = useState<DemoBundle | null>(null);
  const [showAll, setShowAll] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const suggestions = demo?.suggestions ?? DEFAULT_SUGGESTIONS;

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
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    setTurns((t) => [...t, { role: "user", text: q }]);
    const history = turns.slice(-6).map((t) => ({ role: t.role === "user" ? "user" : "assistant", content: t.text }));
    try {
      const live = !!health;
      const r = live
        ? await ask(q, pending, history, sessionId)
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

  return (
    <div className="app">
      <header>
        <div>
          <h1>BI Copilot</h1>
          <p className="tagline">Ask the sales warehouse in plain language. Governed metrics, inspectable SQL, multi-agent trace.</p>
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
          {!demo && (
            <button className="link" onClick={() => setShowCatalog((s) => !s)}>
              {showCatalog ? "hide catalog" : "metrics & rules"}
            </button>
          )}
          <a className="link" href={LINKS.caseStudy}>case study</a>
          <a className="link" href={LINKS.source} target="_blank" rel="noopener noreferrer">
            source ↗
          </a>
        </div>
      </header>

      {showCatalog && catalog && (
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

      <main>
        {turns.length === 0 && (
          <div className="empty">
            {demo && !health && !waking && (
              <p className="small">
                Static demo: every answer below was produced by the real planner → SQL → analyst → critic pipeline over a
                synthetic warehouse and saved ahead of time, so this page needs no server. {demo.questions.length} questions
                are available; the clarifying-question flow works too.
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
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`turn ${t.role}`}>
            {t.role === "user" ? (
              <div className="bubble user">{t.text}</div>
            ) : (
              <div className="bubble copilot">
                {t.result && t.result.status !== "refused" && (
                  <span className={`mode ${t.result.id.startsWith("demo") ? "" : "mode-live"}`}>
                    {t.result.id.startsWith("demo") ? "pre-computed" : t.result.cached ? "live · cached" : t.result.llm.startsWith("fake (fallback)") ? "live · deterministic fallback" : "live"}
                  </span>
                )}
                <div className="answer">
                  {t.text.split("\n").map((line, j) => (
                    <p key={j} className={line.startsWith("Note:") ? "muted small" : line.startsWith("- ") ? "insight" : ""}>
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
                {t.result?.status === "answered" && <ResultView r={t.result} />}
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

      <form className="composer" onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            pending ? "Answer the clarifying question…" : demo ? "Type one of the available questions, or pick a chip" : "e.g. top 10 products by revenue in APAC last quarter"
          }
          disabled={busy}
          autoFocus
        />
        <button type="submit" disabled={busy || !input.trim()}>
          Ask
        </button>
      </form>
    </div>
  );
}
