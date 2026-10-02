import { useEffect, useState } from "react";

type Readiness = {
  status: string;
  missing_configuration: string[];
  database: string;
  gemini: string;
  telegram: string;
  linkedin: string;
};

export default function App() {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/readiness")
      .then((response) => {
        if (!response.ok) throw new Error("Could not load setup status");
        return response.json() as Promise<Readiness>;
      })
      .then(setReadiness)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  return (
    <main>
      <header>
        <p className="eyebrow">LOCAL WORKSPACE · V1 SCAFFOLD</p>
        <h1>LinkedIn Post Agent</h1>
        <p>Your local setup overview. Every LinkedIn post requires your exact approval in Telegram.</p>
      </header>
      <section className="card">
        <h2>Setup status</h2>
        {error && <p role="alert">{error}</p>}
        {!readiness && !error && <p>Checking local application…</p>}
        {readiness && (
          <>
            <p className="status">{readiness.status.replaceAll("_", " ")}</p>
            <ul>
              {(["database", "gemini", "telegram", "linkedin"] as const).map((name) => (
                <li key={name}><span>{name}</span><strong>{readiness[name].replaceAll("_", " ")}</strong></li>
              ))}
            </ul>
            {readiness.missing_configuration.length > 0 && (
              <p>Configuration needed: {readiness.missing_configuration.join(", ")}</p>
            )}
            <p>This dashboard currently shows connection setup. Posting controls remain in your paired Telegram chat.</p>
          </>
        )}
      </section>
    </main>
  );
}
