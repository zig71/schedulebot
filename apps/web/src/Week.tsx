import { dayLabel, minutesToWords, type PlanView } from "./api";

interface Props {
  plan: PlanView | null;
}

/**
 * The week. Late work is shown in red at the top rather than hidden, and a
 * shortfall says how much of the work could not be placed — the plan is only
 * trustworthy if it admits what it could not do.
 */
export default function Week({ plan }: Props) {
  if (!plan) {
    return (
      <section className="panel">
        <p className="eyebrow">Week</p>
        <p className="muted">No plan yet. Re-plan to place your tasks.</p>
      </section>
    );
  }

  const groups = new Map<string, typeof plan.blocks>();
  for (const block of plan.blocks) {
    const day = dayLabel(block.start);
    groups.set(day, [...(groups.get(day) ?? []), block]);
  }

  return (
    <section className="panel">
      <p className="eyebrow">Week</p>

      {plan.late.length > 0 && (
        <ul className="late-list">
          {plan.late.map((item) => (
            <li key={`${item.task_id}-${item.due}`}>
              <strong>{item.title}</strong> finishes {minutesToWords(item.minutes_late)} after its
              deadline — due {new Date(item.due).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}
            </li>
          ))}
        </ul>
      )}

      {plan.shortfalls.length > 0 && (
        <ul className="late-list">
          {plan.shortfalls.map((item) => (
            <li key={item.task_id}>
              <strong>{item.title}</strong> only {minutesToWords(item.placed_minutes)} of{" "}
              {minutesToWords(item.required_minutes)} could be placed
            </li>
          ))}
        </ul>
      )}

      <div className="days">
        {[...groups.entries()].map(([day, blocks]) => (
          <div className="day" key={day}>
            <h3>{day}</h3>
            <ul>
              {blocks.map((block) => (
                <li
                  key={`${block.task_id}-${block.start}`}
                  className={block.is_past ? "past" : block.is_now ? "current" : ""}
                >
                  <span className="dot" style={{ background: block.colour ?? "#888" }} />
                  <span className="time">
                    {new Date(block.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </span>
                  <span className="title">{block.title}</span>
                  <span className="mins">{minutesToWords(block.minutes)}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {plan.blocks.length === 0 && <p className="muted">Nothing placed.</p>}

      <footer className="plan-meta">
        solved in {plan.solve_ms} ms · disturbance {plan.disturbance_minutes} min
        {plan.complete ? " · complete" : " · has warnings"}
      </footer>
    </section>
  );
}
