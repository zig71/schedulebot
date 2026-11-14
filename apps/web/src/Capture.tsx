import { useState, type FormEvent } from "react";
import { api, type PlanView } from "./api";

interface Props {
  onCaptured: (plan: PlanView) => void;
}

/**
 * One textbox. The point is that you can dump a messy sentence about your week
 * and see the parsed result and the plan it produced — including the warnings,
 * which are the interesting part.
 */
export default function Capture({ onCaptured }: Props) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [created, setCreated] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!note.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.capture(note.trim());
      setWarnings(result.warnings);
      setCreated(result.created);
      setNote("");
      onCaptured(result.plan);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "capture failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <p className="eyebrow">Add work</p>
      <form onSubmit={submit}>
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="gym twice this week, an hour each. investor update before friday, 3h. call sam thursday afternoon"
          rows={4}
          spellCheck
        />
        <div className="row">
          <button className="primary" type="submit" disabled={busy || !note.trim()}>
            {busy ? "Parsing…" : "Add and schedule"}
          </button>
          {created.length > 0 && <span className="muted">added {created.length}</span>}
        </div>
      </form>

      {error && <p className="error">{error}</p>}

      {warnings.length > 0 && (
        <ul className="warnings">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
