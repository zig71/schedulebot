import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { clock, minutesToWords, type NowView } from "./api";

interface Props {
  now: NowView | null;
  onDone: (taskId: string) => Promise<void>;
  busy: boolean;
}

/**
 * The whole product in one panel: what to do, why, how long is left.
 *
 * The countdown is computed from the server's instants on every tick, not
 * decremented locally — a laptop that slept for an hour must not show a stale
 * clock, and the server already decided when the block ends.
 */
export default function Now({ now, onDone, busy }: Props) {
  const [today, setToday] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setToday(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const remaining = useMemo(() => {
    if (!now) return 0;
    const end = new Date(now.end).getTime();
    return Math.max(0, Math.round((end - today) / 1000));
  }, [now, today]);

  const startsIn = useMemo(() => {
    if (!now) return 0;
    return Math.max(0, Math.round((new Date(now.start).getTime() - today) / 1000));
  }, [now, today]);

  if (!now) {
    return (
      <section className="panel now now-card empty">
        <p className="eyebrow">Nothing scheduled</p>
        <h2>Your day is clear</h2>
        <p className="muted">Add something below, or re-plan once there is work to place.</p>
      </section>
    );
  }

  const total = Math.max(1, (new Date(now.end).getTime() - new Date(now.start).getTime()) / 1000);
  const progress = Math.min(100, Math.max(0, ((total - remaining) / total) * 100));
  const running = now.state === "now";

  const countdown =
    remaining >= 3600
      ? `${Math.floor(remaining / 3600)}:${String(Math.floor((remaining % 3600) / 60)).padStart(2, "0")}:${String(remaining % 60).padStart(2, "0")}`
      : `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;

  return (
    <section className="panel now now-card" style={{ "--accent": now.colour ?? "#4C6FFF" } as CSSProperties}>
      <header className="now-head">
        <span className={`pill ${running ? "live" : "next"}`}>
          {running ? "Now" : `Next in ${minutesToWords(startsIn / 60)}`}
        </span>
        <span className="chip" style={{ borderColor: now.colour ?? undefined }}>
          {now.category_name}
        </span>
      </header>

      <h1 className="now-title">{now.title}</h1>

      <div className="now-times">
        <span>{clock(now.start)}</span>
        <span className="dash">→</span>
        <span>{clock(now.end)}</span>
        <span className="muted">· {minutesToWords(now.minutes)}</span>
      </div>

      <div className="countdown" aria-live="off">
        <div className="countdown-value">{running ? countdown : minutesToWords(startsIn / 60)}</div>
        <div className="countdown-label">{running ? "left" : "until it starts"}</div>
        <div className="bar" aria-hidden="true">
          <div className="bar-fill" style={{ width: `${running ? progress : 0}%` }} />
        </div>
      </div>

      <p className="why">{now.explanation}</p>

      {now.context && (now.context.people.length > 0 || now.context.place || now.context.requires.length > 0) && (
        <dl className="context">
          {now.context.people.length > 0 && (
            <>
              <dt>With</dt>
              <dd>{now.context.people.join(", ")}</dd>
            </>
          )}
          {now.context.place && (
            <>
              <dt>Where</dt>
              <dd>{now.context.place}</dd>
            </>
          )}
          {now.context.requires.length > 0 && (
            <>
              <dt>Needs</dt>
              <dd>{now.context.requires.join(", ")}</dd>
            </>
          )}
        </dl>
      )}

      {now.blocking.length > 0 && (
        <p className="blocking">
          Blocks while this runs: <strong>{now.blocking.join(", ")}</strong>
        </p>
      )}

      <div className="actions">
        <button className="primary" onClick={() => onDone(now.task_id)} disabled={busy}>
          {busy ? "Saving…" : "Done"}
        </button>
      </div>
    </section>
  );
}
