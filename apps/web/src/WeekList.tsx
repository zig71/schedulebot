import { dayLabel, minutesToWords, type PlanView } from "./api";

interface Props {
  plan: PlanView | null;
}

/**
 * The written version of the grid: what is coming, day by day, with the totals.
 * Late and unplaceable work leads, because that is the part the plan owes an
 * explanation for.
 */
export default function WeekList({ plan }: Props) {
  if (!plan) {
    return (
      <section className="panel list-panel">
        <p className="eyebrow">Coming up</p>
        <p className="muted">No plan yet. Add work and it will be placed.</p>
      </section>
    );
  }

  const groups = new Map<string, typeof plan.blocks>();
  for (const block of plan.blocks) {
    const day = dayLabel(block.start);
    groups.set(day, [...(groups.get(day) ?? []), block]);
  }

  return (
    <section className="panel list-panel">
      <p className="eyebrow">Coming up</p>

      {plan.late.length > 0 && (
        <ul className="flag-list">
          {plan.late.map((item) => (
            <li key={`${item.task_id}-${item.due}`}>
              <strong>{item.title}</strong> finishes {minutesToWords(item.minutes_late)} after its
              deadline
            </li>
          ))}
        </ul>
      )}

      {plan.shortfalls.length > 0 && (
        <ul className="flag-list">
          {plan.shortfalls.map((item) => (
            <li key={item.task_id}>
              <strong>{item.title}</strong>: {minutesToWords(item.placed_minutes)} of{" "}
              {minutesToWords(item.required_minutes)} placed
            </li>
          ))}
        </ul>
      )}

      <div className="list-days">
        {[...groups.entries()].map(([day, blocks]) => (
          <div className="list-day" key={day}>
            <h3>
              {day}
              <span>{minutesToWords(blocks.reduce((total, block) => total + block.minutes, 0))}</span>
            </h3>
            <ul>
              {blocks.map((block) => (
                <li
                  key={`${block.task_id}-${block.start}`}
                  className={block.is_now ? "current" : block.is_past ? "past" : ""}
                >
                  <span className="dot" style={{ background: block.colour ?? "#888" }} />
                  <span className="time">
                    {new Date(block.start).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                  <span className="title">{block.title}</span>
                  <span className="mins">{block.minutes}m</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <footer className="plan-meta">
        {plan.solve_ms} ms · disturbance {plan.disturbance_minutes}m
        {plan.complete ? " · complete" : " · has warnings"}
      </footer>
    </section>
  );
}
