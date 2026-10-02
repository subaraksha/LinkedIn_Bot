import { useCallback, useEffect, useState } from "react";

type Readiness = {
  status: string;
  missing_configuration: string[];
  database: string;
  gemini: string;
  telegram: string;
  linkedin: string;
  worker: string;
  linkedin_account: string | null;
  telegram_account: { bot_id_suffix: string; owner_id_suffix: string } | null;
};

type Source = { _id: string; kind: string; label: string; content: string; extraction_status?: string; created_at: string };
type Fact = { _id: string; type: string; claim: string; experience_context: string; status: string; publication_permission: string; evidence: { source_id: string; kind: string; quote?: string }[]; revision: number };
type Knowledge = { profile_revision: number; sources: Source[]; facts: Fact[] };

type Pairing = {
  status: string;
  candidate?: { sender_id: string; first_name?: string; username?: string } | null;
  error?: string | null;
};

function label(value: string) {
  return value.replaceAll("_", " ");
}

export default function App() {
  const [csrf, setCsrf] = useState<string | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [pairingUrl, setPairingUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [knowledge, setKnowledge] = useState<Knowledge | null>(null);
  const [sourceLabel, setSourceLabel] = useState("LinkedIn profile text");
  const [sourceText, setSourceText] = useState("");
  const [factType, setFactType] = useState("work");
  const [factClaim, setFactClaim] = useState("");
  const [factContext, setFactContext] = useState("");
  const [factPermission, setFactPermission] = useState("private");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const response = await fetch("/api/readiness", { credentials: "same-origin" });
    if (!response.ok) throw new Error("Could not load setup status");
    setReadiness((await response.json()) as Readiness);
  }, []);

  const loadKnowledge = useCallback(async () => {
    const response = await fetch("/api/v1/knowledge", { credentials: "same-origin" });
    if (!response.ok) throw new Error("Could not load professional information");
    setKnowledge((await response.json()) as Knowledge);
  }, []);

  const loadPairing = useCallback(async () => {
    const response = await fetch("/api/connections/telegram/pairing", { credentials: "same-origin" });
    if (response.ok) setPairing((await response.json()) as Pairing);
  }, []);

  useEffect(() => {
    fetch("/api/session", { credentials: "same-origin" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Open the current local dashboard link from the launcher");
        return response.json() as Promise<{ csrf: string }>;
      })
      .then(({ csrf: value }) => {
        setCsrf(value);
        return Promise.all([load(), loadPairing(), loadKnowledge()]);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [load, loadPairing, loadKnowledge]);

  useEffect(() => {
    if (pairing?.status !== "waiting" || !csrf) return;
    const timer = window.setInterval(() => { void loadPairing(); }, 2500);
    return () => window.clearInterval(timer);
  }, [csrf, pairing?.status, loadPairing]);

  async function post(path: string, body?: object) {
    if (!csrf) throw new Error("Local session is unavailable");
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({})) as { detail?: string };
      throw new Error(payload.detail || "Setup action failed");
    }
    return response.json();
  }

  async function mutate(path: string, method: string, body: object) {
    if (!csrf) throw new Error("Local session is unavailable");
    const response = await fetch(path, {
      method, credentials: "same-origin",
      headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({})) as { detail?: string };
      throw new Error(payload.detail || "Could not save professional information");
    }
    return response.json();
  }

  async function saveSource(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      await mutate("/api/v1/sources/text", "POST", { label: sourceLabel, content: sourceText });
      setSourceText(""); await loadKnowledge();
    } catch (cause) { setError((cause as Error).message); } finally { setBusy(false); }
  }

  async function extractSource(source: Source) {
    setBusy(true); setError(null);
    try { await mutate(`/api/v1/sources/${source._id}/suggestions`, "POST", {}); await loadKnowledge(); }
    catch (cause) { setError((cause as Error).message); } finally { setBusy(false); }
  }

  async function saveFact(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      await mutate("/api/v1/knowledge", "POST", { type: factType, claim: factClaim, experience_context: factContext, publication_permission: factPermission });
      setFactClaim(""); setFactContext(""); setFactPermission("private"); await loadKnowledge();
    } catch (cause) { setError((cause as Error).message); } finally { setBusy(false); }
  }

  async function changeFact(fact: Fact, action: "edit" | "toggle" | "review" | "delete") {
    let body: object; let method = "PATCH";
    if (action === "delete") {
      if (!window.confirm("Delete this fact from active knowledge?")) return;
      body = { expected_revision: fact.revision }; method = "DELETE";
    } else {
      const claim = action === "edit" ? window.prompt("Edit this fact", fact.claim) : fact.claim;
      if (claim === null || !claim.trim()) return;
      body = { expected_revision: fact.revision, claim, experience_context: fact.experience_context,
        publication_permission: action === "toggle" ? (fact.publication_permission === "private" ? "public" : "private") : fact.publication_permission,
        status: action === "review" ? (fact.status === "confirmed" ? "disputed" : "confirmed") : fact.status };
    }
    setBusy(true); setError(null);
    try { await mutate(`/api/v1/knowledge/${fact._id}`, method, body); await loadKnowledge(); }
    catch (cause) { setError((cause as Error).message); await loadKnowledge(); }
    finally { setBusy(false); }
  }

  async function startTelegram() {
    setBusy(true); setError(null);
    try {
      const result = await post("/api/connections/telegram/pairing/start") as { url: string };
      setPairingUrl(result.url);
      await loadPairing();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function confirmTelegram(senderId: string) {
    setBusy(true); setError(null);
    try {
      await post("/api/connections/telegram/pairing/confirm", { sender_id: senderId });
      setPairingUrl(null);
      await Promise.all([load(), loadPairing()]);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function startLinkedIn() {
    setBusy(true); setError(null);
    try {
      const result = await post("/api/connections/linkedin/start") as { url: string };
      window.location.assign(result.url);
    } catch (cause) { setError((cause as Error).message); setBusy(false); }
  }

  return (
    <main>
      <header>
        <p className="eyebrow">LOCAL WORKSPACE · SETUP</p>
        <h1>LinkedIn Post Agent</h1>
        <p>Connect your accounts and check that the local worker is ready. Publishing still requires your exact approval in Telegram.</p>
      </header>
      {error && <p className="notice error" role="alert">{error}</p>}
      {!readiness && !error && <p>Checking local application…</p>}
      {readiness && <>
        <section className="card">
          <h2>Local setup</h2>
          <ul>
            <li><span>Database</span><strong>{label(readiness.database)}</strong></li>
            <li><span>Gemini</span><strong>{label(readiness.gemini)}</strong></li>
            <li><span>Telegram receiver</span><strong>{label(readiness.worker)}</strong></li>
          </ul>
          {readiness.missing_configuration.length > 0 &&
            <p className="muted">Configuration needed: {readiness.missing_configuration.join(", ")}</p>}
          {readiness.telegram === "paired" && readiness.worker === "stopped" &&
            <p className="muted">Start the local receiver to accept Telegram messages.</p>}
          <button className="secondary" onClick={() => void load()} disabled={busy}>Refresh status</button>
        </section>
        <section className="card">
          <h2>Telegram</h2>
          <p>Status: <strong>{label(readiness.telegram)}</strong></p>
          {readiness.telegram_account && <p className="muted">Bot ID ending {readiness.telegram_account.bot_id_suffix} · Paired account ending {readiness.telegram_account.owner_id_suffix}</p>}
          {readiness.telegram === "webhook_conflict" && <p className="notice error">This bot has a webhook configured. Remove it before starting the local receiver; pending updates must be preserved.</p>}
          {readiness.telegram === "bot_mismatch" && <p className="notice error">The configured bot token belongs to a different bot. Check the local token before continuing.</p>}
          {readiness.telegram === "unavailable" && <p className="notice error">Telegram could not be verified. Check the bot token and connection.</p>}
          {readiness.telegram === "token_missing" && <p className="notice error">Add the Telegram bot token to the local configuration, then refresh.</p>}
          {readiness.telegram === "not_paired" && <>
            <button onClick={() => void startTelegram()} disabled={!csrf || busy || pairing?.status === "waiting"}>Start pairing</button>
            {pairingUrl && <p><a href={pairingUrl} target="_blank" rel="noreferrer">Open your bot in Telegram</a> and send the Start message.</p>}
            {pairing?.status === "waiting" && <p className="muted">Waiting for your private message. This link expires after ten minutes.</p>}
            {pairing?.status === "candidate" && pairing.candidate && <div className="candidate">
              <p>Check this is your Telegram account before confirming:</p>
              <p><strong>{pairing.candidate.first_name || "Unknown name"}</strong> · {pairing.candidate.username ? `@${pairing.candidate.username}` : "No username"} · numeric ID {pairing.candidate.sender_id}</p>
              <button onClick={() => void confirmTelegram(pairing.candidate!.sender_id)} disabled={busy}>Confirm this account</button>
            </div>}
            {pairing?.error && <p className="notice error">{pairing.error}</p>}
          </>}
        </section>
        <section className="card">
          <h2>LinkedIn</h2>
          <p>Status: <strong>{label(readiness.linkedin)}</strong></p>
          {readiness.linkedin_account && <p className="muted">Account: {readiness.linkedin_account}</p>}
          <button onClick={() => void startLinkedIn()} disabled={!csrf || busy || readiness.database !== "connected"}>
            {readiness.linkedin === "connected" ? "Reconnect LinkedIn" : "Connect LinkedIn"}
          </button>
          <p className="muted">Connecting an account does not publish a post.</p>
        </section>
        <section className="card">
          <p className="eyebrow">PROFESSIONAL INFORMATION</p>
          <h2>Your sources and facts</h2>
          <p className="muted">Saved locally in your database. New facts are private by default. Pasted profile text stays a source until you ask for suggestions. That sends the saved text to Gemini and creates private facts for your review; nothing is published.</p>
          <form onSubmit={(event) => void saveSource(event)} className="stack">
            <h3>Save LinkedIn profile text</h3>
            <label>Source label<input value={sourceLabel} maxLength={120} onChange={event => setSourceLabel(event.target.value)} required /></label>
            <label>Profile text<textarea value={sourceText} maxLength={50000} rows={6} onChange={event => setSourceText(event.target.value)} required /></label>
            <button disabled={busy || !csrf}>Save source</button>
          </form>
          <form onSubmit={(event) => void saveFact(event)} className="stack">
            <h3>Add a fact</h3>
            <label>Type<select value={factType} onChange={event => setFactType(event.target.value)}>{["work", "education", "project", "tool", "achievement", "other"].map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label>
            <label>Fact<textarea value={factClaim} maxLength={2000} rows={3} onChange={event => setFactClaim(event.target.value)} required /></label>
            <label>Context, optional<input value={factContext} maxLength={2000} onChange={event => setFactContext(event.target.value)} /></label>
            <label>Can this be used in public posts?<select value={factPermission} onChange={event => setFactPermission(event.target.value)}><option value="private">Private</option><option value="public">Public</option></select></label>
            <button disabled={busy || !csrf}>Save fact</button>
          </form>
          {knowledge && <>
            <h3>Sources ({knowledge.sources.length})</h3>
            {knowledge.sources.length === 0 && <p className="muted">No sources saved yet.</p>}
            {knowledge.sources.map(source => <article className="record" key={source._id}>
              <strong>{source.label}</strong> <span className="muted">· {label(source.kind)}</span>
              <p>{source.content.length > 240 ? `${source.content.slice(0, 240)}…` : source.content}</p>
              {source.kind === "linkedin_profile_text" && <p className="muted">Suggestions: {label(source.extraction_status || "not_started")}</p>}
              {source.kind === "linkedin_profile_text" && source.extraction_status !== "completed" && <button className="secondary" disabled={busy} onClick={() => void extractSource(source)}>Suggest facts from this text</button>}
            </article>)}
            <h3>Facts ({knowledge.facts.length})</h3>
            {knowledge.facts.length === 0 && <p className="muted">No facts saved yet.</p>}
            {knowledge.facts.map(fact => <article className="record" key={fact._id}>
              <p>{fact.claim}</p>
              {fact.experience_context && <p className="muted">{fact.experience_context}</p>}
              <p className="muted">{label(fact.type)} · {label(fact.status)} · {label(fact.publication_permission)} · Source: {knowledge.sources.find(source => source._id === fact.evidence[0]?.source_id)?.label || "Owner statement"}</p>
              {fact.evidence[0]?.quote && <p className="evidence">From your source: “{fact.evidence[0].quote}”</p>}
              <div className="actions"><button className="secondary" disabled={busy} onClick={() => void changeFact(fact, "edit")}>Edit</button><button className="secondary" disabled={busy} onClick={() => void changeFact(fact, "toggle")}>Set {fact.publication_permission === "private" ? "public" : "private"}</button><button className="secondary" disabled={busy} onClick={() => void changeFact(fact, "review")}>Mark {fact.status === "confirmed" ? "disputed" : "confirmed"}</button><button className="secondary" disabled={busy} onClick={() => void changeFact(fact, "delete")}>Delete</button></div>
            </article>)}
          </>}
        </section>
      </>}
    </main>
  );
}
