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
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const response = await fetch("/api/readiness", { credentials: "same-origin" });
    if (!response.ok) throw new Error("Could not load setup status");
    setReadiness((await response.json()) as Readiness);
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
        return Promise.all([load(), loadPairing()]);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [load, loadPairing]);

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
      </>}
    </main>
  );
}
