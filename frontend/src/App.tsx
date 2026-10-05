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
type Fact = { _id: string; type: string; claim: string; experience_context: string; status: string; publication_permission: string; evidence: { source_id: string; kind: string; quote?: string; location?: string }[]; revision: number };
type Knowledge = { profile_revision: number; sources: Source[]; facts: Fact[] };
type ExportStatus = { profile_revision: number; full_export_revision: number | null; full_status: string; public_export_revision: number | null; public_status: string };

type OwnerProfile = {
  revision: number; updated_at: string | null; target_roles: string[]; audience: string;
  interests: string[]; content_goals: string[];
  tone: "conversational" | "professional" | "technical" | "reflective" | "mixed";
  length: "short" | "medium" | "long"; technical_depth: "introductory" | "balanced" | "deep";
  use_emojis: boolean; use_hashtags: boolean;
  avoid_phrases: string[]; avoid_styles: string[]; avoid_topics: string[];
  confidential_details: string[]; writing_samples: string[];
};
type ListField = "target_roles" | "interests" | "content_goals" | "avoid_phrases" | "avoid_styles" | "avoid_topics" | "confidential_details" | "writing_samples";
type Question = { id: string; kind: string; prompt: string; fact_id: string | null; status: string; answer: string | null; revision: number };
type Topic = { trend_score?: number; title: string; why_now: string; why_you: string; angle: string; sources: { url: string; title: string }[] };
type DiscoveryStatus = { timezone?: string; job: { _id: string; status: string; updated_at: string; error_code?: string | null } | null };
type TopicStatus = { schedule: { enabled: boolean; paused: boolean; weekday: number; local_time: string; timezone: string; next_at: string | null; revision: number };
  telegram_delivery_uncertain: boolean;
  publishing_enabled: boolean;
  drafts: { _id: string; version: number; body: string; restored_from?: number; created_at: string }[];
  recent_outcomes: { _id: string; state: string; selected_topic?: { title: string };
    post_url?: string | null; post_evidence_source?: string | null; updated_at?: string;
    draft_version?: number }[];
  recovery: { attempt_id: string; workflow_revision: number; result_reason?: string;
    quiesced: boolean; error?: string } | null;
  workflow: { _id: string; revision: number; state: string; shortlist_revision: number; shortlist: Topic[]; selected_topic: Topic | null;
    perspective: string | null; experience: string; draft_error?: string | null;
    publication_error?: string | null; post_url?: string | null;
    draft_version?: number; error?: string | null } | null };
type HistoryDetail = { workflow: { _id: string; revision: number; state: string; perspective?: string | null;
  selected_topic?: { title: string }; post_url?: string | null };
  drafts: { _id: string; version: number; body: string; feedback?: string | null;
    restored_from?: number }[] };

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
  const [exportStatus, setExportStatus] = useState<ExportStatus | null>(null);
  const [includeOriginals, setIncludeOriginals] = useState(false);
  const [profileDraft, setProfileDraft] = useState<OwnerProfile | null>(null);
  const [profileNotice, setProfileNotice] = useState<string | null>(null);
  const [questionNotice, setQuestionNotice] = useState<string | null>(null);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [questionAnswers, setQuestionAnswers] = useState<Record<string, string>>({});
  const [discoveryStatus, setDiscoveryStatus] = useState<DiscoveryStatus | null>(null);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [topicStatus, setTopicStatus] = useState<TopicStatus | null>(null);
  const [scheduleDraft, setScheduleDraft] = useState<TopicStatus["schedule"] | null>(null);
  const [topicNotice, setTopicNotice] = useState<string | null>(null);
  const [historyDetail, setHistoryDetail] = useState<HistoryDetail | null>(null);
  const [recoveryAcknowledged, setRecoveryAcknowledged] = useState(false);
  const [recoveryPostUrl, setRecoveryPostUrl] = useState("");
  const [sourceLabel, setSourceLabel] = useState("LinkedIn profile text");
  const [sourceText, setSourceText] = useState("");
  const [resumeFile, setResumeFile] = useState<File | null>(null);
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
    const fetchAll = () => Promise.all([
      fetch("/api/v1/knowledge", { credentials: "same-origin" }),
      fetch("/api/v1/knowledge/export/status", { credentials: "same-origin" }),
      fetch("/api/v1/clarifications", { credentials: "same-origin" }),
    ]);
    let [response, statusResponse, questionsResponse] = await fetchAll();
    if ([response, statusResponse, questionsResponse].some(item => item.status >= 500)) {
      await new Promise(resolve => window.setTimeout(resolve, 400));
      [response, statusResponse, questionsResponse] = await fetchAll();
    }
    if ([response, statusResponse, questionsResponse].some(item => item.status === 401)) {
      throw new Error("Your dashboard session expired. Open the latest local dashboard tab.");
    }
    if (!response.ok || !statusResponse.ok || !questionsResponse.ok) {
      throw new Error("Professional information is temporarily unavailable. Refresh the page to try again.");
    }
    setKnowledge((await response.json()) as Knowledge);
    setExportStatus((await statusResponse.json()) as ExportStatus);
    setQuestions(((await questionsResponse.json()) as { questions: Question[] }).questions);
  }, []);

  const loadOwnerProfile = useCallback(async () => {
    const response = await fetch("/api/v1/owner/profile", { credentials: "same-origin" });
    if (!response.ok) throw new Error("Could not load goals and preferences");
    setProfileDraft((await response.json()) as OwnerProfile);
  }, []);

  const loadDiscovery = useCallback(async () => {
    try {
      const response = await fetch("/api/v1/topics/discovery", { credentials: "same-origin" });
      if (!response.ok) throw new Error("Could not check source refresh progress. Try refreshing this page.");
      setDiscoveryStatus(await response.json() as DiscoveryStatus);
      setDiscoveryError(null);
    } catch (cause) { setDiscoveryError((cause as Error).message); }
  }, []);

  const loadTopics = useCallback(async () => {
    const response = await fetch("/api/v1/topics/status", { credentials: "same-origin" });
    if (!response.ok) throw new Error("Could not load weekly topics");
    const result = await response.json() as TopicStatus;
    setTopicStatus(result);
    setScheduleDraft(current => current?.revision === result.schedule.revision ? current : result.schedule);
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
        return Promise.all([load(), loadPairing(), loadKnowledge(), loadOwnerProfile(), loadTopics(), loadDiscovery()]);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [load, loadPairing, loadKnowledge, loadOwnerProfile, loadTopics, loadDiscovery]);

  useEffect(() => {
    if (!csrf) return;
    const timer = window.setInterval(() => { void loadTopics().catch(() => undefined); void loadDiscovery(); }, 15000);
    return () => window.clearInterval(timer);
  }, [csrf, loadTopics, loadDiscovery]);

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

  async function uploadResume(event: React.FormEvent) {
    event.preventDefault();
    if (!resumeFile || !csrf) return;
    const kind = resumeFile.name.toLowerCase().endsWith(".pdf") ? "pdf"
      : resumeFile.name.toLowerCase().endsWith(".docx") ? "docx"
      : resumeFile.name.toLowerCase().endsWith(".txt") ? "txt" : null;
    if (!kind) { setError("Choose a PDF, DOCX, or plain text file"); return; }
    setBusy(true); setError(null);
    try {
      const response = await fetch(`/api/v1/sources/resume?kind=${kind}`, {
        method: "POST", credentials: "same-origin", body: resumeFile,
        headers: { "X-CSRF-Token": csrf, "Content-Type": "application/octet-stream" },
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(payload.detail || "Resume upload failed");
      }
      setResumeFile(null); await loadKnowledge();
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

  async function downloadExport(scope: "full" | "public") {
    if (!csrf) return;
    setBusy(true); setError(null);
    try {
      const response = await fetch("/api/v1/knowledge/exports", {
        method: "POST", credentials: "same-origin",
        headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
        body: JSON.stringify({ scope, include_originals: scope === "full" && includeOriginals }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(payload.detail || "Download could not be created");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = scope === "full" ? "full-knowledge.zip" : "public-profile.zip";
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      await loadKnowledge();
    } catch (cause) { setError((cause as Error).message); } finally { setBusy(false); }
  }

  function listField(title: string, key: ListField, rows = 2) {
    return <label>{title}<textarea rows={rows} value={(profileDraft?.[key] || []).join("\n")}
      onChange={event => setProfileDraft(current => current ? { ...current, [key]: event.target.value.split("\n") } : null)} /></label>;
  }

  async function saveOwnerProfile(event: React.FormEvent) {
    event.preventDefault(); if (!profileDraft) return;
    setBusy(true); setError(null); setProfileNotice(null);
    try {
      const { revision: _revision, updated_at: _updatedAt, ...data } = profileDraft;
      for (const key of ["target_roles", "interests", "content_goals", "avoid_phrases", "avoid_styles", "avoid_topics", "confidential_details", "writing_samples"] as ListField[]) {
        data[key] = data[key].map(value => value.trim()).filter(Boolean);
      }
      const result = await mutate("/api/v1/owner/profile", "PATCH", { expected_revision: profileDraft.revision, data });
      setProfileDraft(result as OwnerProfile);
      setProfileNotice("Goals and preferences saved.");
      await loadKnowledge();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function respondToQuestion(question: Question, skip: boolean) {
    const answer = questionAnswers[question.id] || "";
    if (!skip && !answer.trim()) { setQuestionNotice("Enter an answer or choose Skip for now."); return; }
    setBusy(true); setError(null); setQuestionNotice(null);
    try {
      await mutate(`/api/v1/clarifications/${encodeURIComponent(question.id)}/response`, "POST",
        { expected_revision: question.revision, answer: skip ? null : answer, skip });
      setQuestionNotice(skip ? "Skipped for now. You can answer this later." : "Answer saved.");
      setQuestionAnswers(current => ({ ...current, [question.id]: "" }));
      await Promise.all([loadKnowledge(), loadOwnerProfile()]);
    } catch (cause) { setError((cause as Error).message); await loadKnowledge(); }
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

  async function saveSchedule(event: React.FormEvent) {
    event.preventDefault(); if (!scheduleDraft) return;
    setBusy(true); setError(null); setTopicNotice(null);
    try {
      await mutate("/api/v1/topics/schedule", "PUT", {
        expected_revision: scheduleDraft.revision, enabled: scheduleDraft.enabled,
        weekday: scheduleDraft.weekday, local_time: scheduleDraft.local_time,
        timezone: scheduleDraft.timezone,
      });
      await loadTopics(); setTopicNotice("Weekly invitation schedule saved.");
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function refreshDiscovery() {
    setBusy(true); setError(null);
    try {
      await post("/api/v1/topics/discovery/refresh");
      await loadDiscovery();
      setTopicNotice(null);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function regenerateTopics() {
    if (!topicStatus?.workflow) return;
    setBusy(true); setError(null); setTopicNotice(null);
    try {
      await post("/api/v1/topics/regenerate", { expected_revision: topicStatus.workflow.revision });
      await loadTopics();
      setTopicNotice(null);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function startTopics() {
    setBusy(true); setError(null); setTopicNotice(null);
    try {
      await post("/api/v1/topics/start");
      await loadTopics(); setTopicNotice("Topic research started. The shortlist will appear here and in Telegram when ready.");
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function changeSchedulePause(paused: boolean) {
    if (!topicStatus) return;
    setBusy(true); setError(null); setTopicNotice(null);
    try {
      await mutate("/api/v1/topics/schedule/pause", "PUT", {
        expected_revision: topicStatus.schedule.revision, paused,
      });
      await loadTopics();
      setTopicNotice(paused ? "Weekly invitations paused. Your current conversation is saved." :
        "Weekly invitations resumed. Your next invitation is scheduled.");
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function toggleHistory(workflowId: string) {
    if (historyDetail?.workflow._id === workflowId) { setHistoryDetail(null); return; }
    setBusy(true); setError(null);
    try {
      const response = await fetch(`/api/v1/topics/history/${encodeURIComponent(workflowId)}`,
        { credentials: "same-origin" });
      if (!response.ok) throw new Error("Could not load this conversation's history");
      setHistoryDetail(await response.json() as HistoryDetail);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function quiescePublisher() {
    const workflow = topicStatus?.workflow;
    if (!workflow) return;
    setBusy(true); setError(null); setTopicNotice(null);
    try {
      await post(`/api/v1/publications/${workflow._id}/quiesce`);
      await loadTopics(); await load();
      setTopicNotice("The old publisher has stopped. Review your LinkedIn profile before resolving the outcome.");
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  async function resolvePublication(outcome: "published" | "not_published") {
    const workflow = topicStatus?.workflow;
    const recovery = topicStatus?.recovery;
    if (!workflow || !recovery || !recoveryAcknowledged) return;
    setBusy(true); setError(null); setTopicNotice(null);
    try {
      await post(`/api/v1/publications/${workflow._id}/resolve`, {
        attempt_id: recovery.attempt_id,
        expected_workflow_revision: recovery.workflow_revision,
        idempotency_key: crypto.randomUUID(), outcome,
        acknowledgement: true,
        post_url: outcome === "published" ? recoveryPostUrl.trim() : null,
      });
      setRecoveryAcknowledged(false); setRecoveryPostUrl("");
      await loadTopics();
      setTopicNotice(outcome === "published" ? "Published outcome recorded." :
        "Marked not published. Restart the local Telegram worker, then request a new FINAL preview and approval before another send.");
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
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
          <p className="eyebrow">WEEKLY TOPICS</p>
          <h2>Find ideas to discuss</h2>
          <p className="muted">Find sourced ideas, discuss one in Telegram, and review every draft before deciding whether to publish.</p>
          {scheduleDraft && !topicStatus?.schedule.paused && <form className="stack" onSubmit={(event) => void saveSchedule(event)}>
            <label className="checkbox"><input type="checkbox" checked={scheduleDraft.enabled}
              onChange={event => setScheduleDraft({ ...scheduleDraft, enabled: event.target.checked })} /> Send me a weekly invitation in Telegram</label>
            <div className="form-grid">
              <label>Day<select value={scheduleDraft.weekday} onChange={event => setScheduleDraft({ ...scheduleDraft, weekday: Number(event.target.value) })}>
                {["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label>
              <label>Local time<input type="time" value={scheduleDraft.local_time} onChange={event => setScheduleDraft({ ...scheduleDraft, local_time: event.target.value })} /></label>
              <label>Timezone<input value={scheduleDraft.timezone} onChange={event => setScheduleDraft({ ...scheduleDraft, timezone: event.target.value })} /></label>
            </div>
            <button disabled={busy}>Save invitation schedule</button>
            {topicStatus?.schedule.next_at && <p className="muted">Next invitation: {new Intl.DateTimeFormat("en-IN", {
              dateStyle: "medium", timeStyle: "short", timeZone: topicStatus.schedule.timezone,
            }).format(new Date(topicStatus.schedule.next_at))} ({topicStatus.schedule.timezone})</p>}
          </form>}
          {topicStatus?.schedule.paused && <p className="notice" role="status">Weekly invitations are paused. Your current conversation and drafts are saved.</p>}
          {topicStatus?.schedule.enabled && !topicStatus.schedule.paused && <button className="secondary" disabled={busy} onClick={() => void changeSchedulePause(true)}>Pause weekly invitations</button>}
          {topicStatus?.schedule.paused && <button className="secondary" disabled={busy} onClick={() => void changeSchedulePause(false)}>Resume weekly invitations</button>}
          {topicStatus?.workflow && ["AWAITING_TOPIC", "RESEARCH_FAILED"].includes(topicStatus.workflow.state) && !topicStatus.workflow.selected_topic ?
            <button className="secondary" disabled={busy} onClick={() => void regenerateTopics()}>Regenerate suggestions</button> :
            <button className="secondary" disabled={busy || !!topicStatus?.workflow || readiness.telegram !== "paired"} onClick={() => void startTopics()}>Find topics now</button>}
          {topicStatus?.workflow?.state === "RESEARCH_PENDING" && <p className="muted" role="status">{["pending", "running"].includes(discoveryStatus?.job?.status || "") ? "Waiting for source refresh to finish before generating suggestions…" : "Finding topic suggestions… This page updates automatically."}</p>}
          {topicStatus?.workflow?.selected_topic && <p className="muted">Finish or discard your current post in Telegram before starting a new topic conversation.</p>}
          {!topicStatus?.workflow && readiness.telegram !== "paired" && <p className="muted">Pair Telegram to start a topic conversation.</p>}
          <button className="secondary" disabled={busy || ["pending", "running"].includes(discoveryStatus?.job?.status || "")} onClick={() => void refreshDiscovery()}>Refresh discovery sources</button>
          {discoveryError && <p className="notice error" role="alert">{discoveryError}</p>}
          {discoveryStatus?.job && <p className={`notice ${discoveryStatus.job.status === "failed" ? "error" : ""}`} role="status">
            {discoveryStatus.job.status === "pending" && "Source refresh queued. Keep the collector running; suggestions will wait for it."}
            {discoveryStatus.job.status === "running" && "Refreshing discovery sources…"}
            {discoveryStatus.job.status === "succeeded" && "Source refresh complete. Fresh sources are ready for topic suggestions."}
            {discoveryStatus.job.status === "failed" && "Source refresh failed. Retry the source refresh before regenerating suggestions."}
            {" · "}{new Intl.DateTimeFormat("en-IN", {
              timeZone: discoveryStatus.timezone || "Asia/Kolkata",
              day: "numeric", month: "short", year: "numeric",
              hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short",
            }).format(new Date(discoveryStatus.job.updated_at.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(discoveryStatus.job.updated_at)
              ? discoveryStatus.job.updated_at : discoveryStatus.job.updated_at + "Z"))}
          </p>}
          {topicNotice && <p className="notice success" role="status">{topicNotice}</p>}
          {topicStatus?.telegram_delivery_uncertain && <p className="notice error" role="alert">A Telegram topic message may not have arrived. Check your bot chat; the app will not resend it automatically.</p>}
          {topicStatus?.workflow && <div className="record">
            <p><strong>Current conversation: {label(topicStatus.workflow.state)}</strong></p>
            {topicStatus.workflow.error && <p className="notice error">{topicStatus.workflow.error}</p>}
            {topicStatus.workflow.draft_error && <p className="notice error">{topicStatus.workflow.draft_error}</p>}
            {topicStatus.workflow.publication_error && <p className="notice error">Publishing stopped before sending: {topicStatus.workflow.publication_error}. Request a new FINAL preview after fixing the issue.</p>}
            {topicStatus.workflow.selected_topic && <p>Selected: {topicStatus.workflow.selected_topic.title}</p>}
            {topicStatus.workflow.perspective && <p className="muted">Your perspective has been saved.</p>}
            {topicStatus.workflow.shortlist.map((topic, index) => <div className="record" key={`${topic.title}-${index}`}>
              <h3>{index + 1}. {topic.title}</h3>
              <p><strong>Why now:</strong> {topic.why_now}</p>
              {topic.trend_score !== undefined && <p className="muted">Trend ranking: {topic.trend_score}/100 · experimental score</p>}
              <p><strong>Why it fits:</strong> {topic.why_you}</p>
              <p><strong>Possible angle:</strong> {topic.angle}</p>
              <p>Sources: {topic.sources.map((source, sourceIndex) => <span key={source.url}>{sourceIndex > 0 ? ", " : ""}<a href={source.url} target="_blank" rel="noreferrer">{source.title}</a></span>)}</p>
            </div>)}
            {topicStatus.drafts?.length > 0 && <div className="stack">
              <h3>Draft history</h3>
              <p className="muted">Earlier versions remain available. Reply RESTORE &lt;version&gt; in Telegram to copy one into a new version.</p>
              {topicStatus.drafts.map(draft => <div className="record" key={draft._id}>
                <p><strong>Version {draft.version}</strong>{draft.restored_from ? ` · restored from V${draft.restored_from}` : ""}</p>
                <p style={{ whiteSpace: "pre-wrap" }}>{draft.body}</p>
              </div>)}
            </div>}
            {topicStatus.workflow.state === "PUBLISH_UNKNOWN" && <div className="record">
              <h3>Publication outcome uncertain</h3>
              <p>LinkedIn may have created the post. Please check your LinkedIn profile. The app will not automatically send it again.</p>
              {topicStatus.recovery?.error && <p className="notice error">{topicStatus.recovery.error}</p>}
              {topicStatus.recovery && !topicStatus.recovery.quiesced && <button disabled={busy} onClick={() => void quiescePublisher()}>Stop old publisher for recovery</button>}
              {topicStatus.recovery?.quiesced && <div className="stack">
                <p>The old publisher has stopped. Choose the outcome after checking LinkedIn.</p>
                <label>Post URL, if you found the post<input value={recoveryPostUrl} onChange={event => setRecoveryPostUrl(event.target.value)} placeholder="https://www.linkedin.com/feed/update/urn:li:share:..." /></label>
                <label className="checkbox"><input type="checkbox" checked={recoveryAcknowledged} onChange={event => setRecoveryAcknowledged(event.target.checked)} /> I inspected LinkedIn. I understand that a missing post cannot be proved with certainty.</label>
                <div className="form-grid">
                  <button disabled={busy || !recoveryAcknowledged || !recoveryPostUrl.trim()} onClick={() => void resolvePublication("published")}>Record as published</button>
                  <button className="secondary" disabled={busy || !recoveryAcknowledged || !!recoveryPostUrl.trim()} onClick={() => void resolvePublication("not_published")}>Record as not published</button>
                </div>
              </div>}
            </div>}
            <p className="muted">Reply CONTINUE in Telegram for a recap of saved progress. After selecting a topic, reply DRAFT. Send feedback in ordinary words, then reply FINAL for the exact preview. {topicStatus.publishing_enabled ? "Only your new exact Telegram approval can publish the post." : "Live publishing is currently disabled; approvals are checks only."}</p>
          </div>}
          {topicStatus && topicStatus.recent_outcomes.length > 0 && <div className="record">
            <h3>Recent outcomes</h3>
            {topicStatus.recent_outcomes.map(item => <div className="record" key={item._id}>
              <p><strong>{label(item.state)}</strong> · {item.selected_topic?.title || "Post"}
                {item.updated_at ? ` · ${new Date(item.updated_at).toLocaleDateString()}` : ""}
                {item.post_url && <> · <a href={item.post_url} target="_blank" rel="noreferrer">View LinkedIn post</a>{item.post_evidence_source === "owner_reported" ? " (reported by you)" : ""}</>}
              </p>
              <button className="secondary" disabled={busy} onClick={() => void toggleHistory(item._id)}>{historyDetail?.workflow._id === item._id ? "Hide drafts" : "View drafts"}</button>
              {historyDetail?.workflow._id === item._id && <div className="stack">
                {historyDetail.drafts.length === 0 && <p className="muted">No draft was created for this conversation.</p>}
                {historyDetail.drafts.map(draft => <div className="record" key={draft._id}>
                  <p><strong>Version {draft.version}</strong>{draft.restored_from ? ` · restored from V${draft.restored_from}` : ""}</p>
                  <p style={{ whiteSpace: "pre-wrap" }}>{draft.body}</p>
                  {draft.feedback && <p className="muted">Your feedback: {draft.feedback}</p>}
                </div>)}
              </div>}
            </div>)}
          </div>}
          <button className="secondary" disabled={busy} onClick={() => void loadTopics()}>Refresh topics</button>
        </section>
        <section className="card">
          <p className="eyebrow">YOUR DIRECTION</p>
          <h2>Goals and writing preferences</h2>
          <p className="muted">These choices guide future posts. Publication boundaries are checked again before a post is sent.</p>
          {profileDraft && <form className="stack" onSubmit={(event) => void saveOwnerProfile(event)}>
            {listField("Target roles (one per line)", "target_roles")}
            <label>Audience<input value={profileDraft.audience} maxLength={240} onChange={event => setProfileDraft({ ...profileDraft, audience: event.target.value })} placeholder="e.g. backend engineers and recruiters" /></label>
            {listField("Topics you want to discuss (one per line)", "interests")}
            {listField("What you want your posts to achieve (one per line)", "content_goals")}
            <div className="form-grid">
              <label>Tone<select value={profileDraft.tone} onChange={event => setProfileDraft({ ...profileDraft, tone: event.target.value as OwnerProfile["tone"] })}>{["conversational", "professional", "technical", "reflective", "mixed"].map(value => <option key={value}>{value}</option>)}</select></label>
              <label>Length<select value={profileDraft.length} onChange={event => setProfileDraft({ ...profileDraft, length: event.target.value as OwnerProfile["length"] })}>{["short", "medium", "long"].map(value => <option key={value}>{value}</option>)}</select></label>
              <label>Technical depth<select value={profileDraft.technical_depth} onChange={event => setProfileDraft({ ...profileDraft, technical_depth: event.target.value as OwnerProfile["technical_depth"] })}>{["introductory", "balanced", "deep"].map(value => <option key={value}>{value}</option>)}</select></label>
            </div>
            <label className="checkbox"><input type="checkbox" checked={profileDraft.use_emojis} onChange={event => setProfileDraft({ ...profileDraft, use_emojis: event.target.checked })} /> Use emojis</label>
            <label className="checkbox"><input type="checkbox" checked={profileDraft.use_hashtags} onChange={event => setProfileDraft({ ...profileDraft, use_hashtags: event.target.checked })} /> Use hashtags</label>
            {listField("Phrases to avoid (one per line)", "avoid_phrases")}
            {listField("Styles to avoid (one per line)", "avoid_styles")}
            {listField("Topics to avoid (one per line)", "avoid_topics")}
            {listField("Confidential details that must not appear publicly (one per line)", "confidential_details", 3)}
            {listField("Optional writing samples (one per line)", "writing_samples", 3)}
            <button disabled={busy}>Save goals and preferences</button>
            {profileNotice && <p className="notice success" role="status">{profileNotice}</p>}
            {profileDraft.updated_at && <p className="muted">Last saved: {new Date(profileDraft.updated_at).toLocaleString()}</p>}
          </form>}
        </section>
        <section className="card">
          <p className="eyebrow">GUIDED QUESTIONS</p>
          <h2>Fill the gaps</h2>
          <p className="muted">Answer what helps, or skip and return later. Answers about uncertain facts stay private and pending until you review those facts.</p>
          {questionNotice && <p className="notice success" role="status">{questionNotice}</p>}
          {questions.length === 0 && <p className="muted">No questions need your attention right now.</p>}
          {questions.map(question => <article className="record" key={question.id}>
            <p><strong>{question.prompt}</strong></p>
            <p className="muted">{question.status === "skipped" ? "Skipped for now · you can answer later" : label(question.status)}{question.fact_id ? " · linked to a pending fact" : ""}</p>
            {question.status === "answered" && question.answer && <p>{question.answer}</p>}
            {question.status !== "answered" && <div className="stack">
              <label>Your answer<textarea rows={2} maxLength={2000} value={questionAnswers[question.id] || ""} onChange={event => setQuestionAnswers(current => ({ ...current, [question.id]: event.target.value }))} /></label>
              <div className="actions"><button disabled={busy} onClick={() => void respondToQuestion(question, false)}>{question.status === "skipped" ? "Answer now" : "Save answer"}</button>{question.status !== "skipped" && <button className="secondary" disabled={busy} onClick={() => void respondToQuestion(question, true)}>Skip for now</button>}</div>
            </div>}
          </article>)}
        </section>
        <section className="card">
          <p className="eyebrow">PROFESSIONAL INFORMATION</p>
          <h2>Your sources and facts</h2>
          <p className="muted">Saved locally in your database. New facts are private by default. Pasted profile text stays a source until you ask for suggestions. That sends the saved text to Gemini and creates private facts for your review; nothing is published.</p>
          <form onSubmit={(event) => void uploadResume(event)} className="stack">
            <h3>Upload a resume</h3>
            <p className="muted">PDF, DOCX, or UTF-8 text, up to 10 MiB. The original stays in your local app data. Suggestions are created only when you request them.</p>
            <label>Resume file<input type="file" accept=".pdf,.docx,.txt" onChange={event => setResumeFile(event.target.files?.[0] || null)} required /></label>
            <button disabled={busy || !csrf || !resumeFile}>Upload resume</button>
          </form>
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
          {exportStatus && <section className="export-panel">
            <h3>Take your knowledge with you</h3>
            <p className="muted">Current profile revision: {exportStatus.profile_revision}. Full private copy: {label(exportStatus.full_status)}. Public copy: {label(exportStatus.public_status)}.</p>
            <p className="muted">The full ZIP includes your current facts, review status, source references, JSON, and readable Markdown. The public ZIP includes only facts you confirmed and marked public.</p>
            <label className="checkbox"><input type="checkbox" checked={includeOriginals} onChange={event => setIncludeOriginals(event.target.checked)} /> Include original resume files in the private ZIP</label>
            <div className="actions"><button disabled={busy} onClick={() => void downloadExport("full")}>Download full knowledge (private)</button><button className="secondary" disabled={busy} onClick={() => void downloadExport("public")}>Download public profile</button></div>
          </section>}
          {knowledge && <>
            <h3>Sources ({knowledge.sources.length})</h3>
            {knowledge.sources.length === 0 && <p className="muted">No sources saved yet.</p>}
            {knowledge.sources.map(source => <article className="record" key={source._id}>
              <strong>{source.label}</strong> <span className="muted">· {label(source.kind)}</span>
              <p>{source.content.length > 240 ? `${source.content.slice(0, 240)}…` : source.content}</p>
              {(source.kind === "linkedin_profile_text" || source.kind.startsWith("resume_")) && <p className="muted">Suggestions: {label(source.extraction_status || "not_started")}</p>}
              {(source.kind === "linkedin_profile_text" || source.kind.startsWith("resume_")) && source.extraction_status !== "completed" && <button className="secondary" disabled={busy} onClick={() => void extractSource(source)}>Suggest facts from this source</button>}
            </article>)}
            <h3>Facts ({knowledge.facts.length})</h3>
            {knowledge.facts.length === 0 && <p className="muted">No facts saved yet.</p>}
            {knowledge.facts.map(fact => <article className="record" key={fact._id}>
              <p>{fact.claim}</p>
              {fact.experience_context && <p className="muted">{fact.experience_context}</p>}
              <p className="muted">{label(fact.type)} · {label(fact.status)} · {label(fact.publication_permission)} · Source: {knowledge.sources.find(source => source._id === fact.evidence[0]?.source_id)?.label || "Owner statement"}</p>
              {fact.evidence[0]?.quote && <p className="evidence">From your source{fact.evidence[0].location ? ` (${fact.evidence[0].location})` : ""}: “{fact.evidence[0].quote}”</p>}
              <div className="actions"><button className="secondary" disabled={busy} onClick={() => void changeFact(fact, "edit")}>Edit</button><button className="secondary" disabled={busy} onClick={() => void changeFact(fact, "toggle")}>Set {fact.publication_permission === "private" ? "public" : "private"}</button><button className="secondary" disabled={busy} onClick={() => void changeFact(fact, "review")}>Mark {fact.status === "confirmed" ? "disputed" : "confirmed"}</button><button className="secondary" disabled={busy} onClick={() => void changeFact(fact, "delete")}>Delete</button></div>
            </article>)}
          </>}
        </section>
      </>}
    </main>
  );
}
