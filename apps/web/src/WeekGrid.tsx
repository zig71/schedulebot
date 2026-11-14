import { type CSSProperties } from "react";
import { clock, type BlockView, type PlanView } from "./api";

interface Props {
  plan: PlanView | null;
}

const HOUR_HEIGHT = 46;
const DAY_MS = 86_400_000;

function startOfWeek(reference: Date): Date {
  const date = new Date(reference);
  date.setHours(0, 0, 0, 0);
  const shift = (date.getDay() + 6) % 7; // Monday-first
  date.setDate(date.getDate() - shift);
  return date;
}

function dateKey(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(
    value.getDate(),
  ).padStart(2, "0")}`;
}

function minutesIntoDay(value: Date): number {
  return value.getHours() * 60 + value.getMinutes();
}

/**
 * The week as a time grid, because a list cannot answer "how full is Thursday" or
 * "is that back to back". Columns are days, rows are hours, and blocks sit where
 * their real instants put them — the instants the API returned, never re-derived
 * from a slot number.
 */
export default function WeekGrid({ plan }: Props) {
  const now = new Date();
  const weekStart = startOfWeek(now);
  const days = Array.from(
    { length: 7 },
    (_, index) => new Date(weekStart.getTime() + index * DAY_MS),
  );

  const blocks = plan?.blocks ?? [];
  const times = blocks.flatMap((block) => [
    minutesIntoDay(new Date(block.start)),
    minutesIntoDay(new Date(block.end)),
  ]);
  // Fit the visible hours to the work that exists, so an early or late block is
  // never clipped off the grid.
  const earliest = Math.min(8 * 60, ...times);
  const latest = Math.max(18 * 60, ...times);
  const startHour = Math.max(0, Math.floor(earliest / 60));
  const endHour = Math.min(24, Math.ceil(latest / 60));
  const hours = Array.from({ length: endHour - startHour + 1 }, (_, index) => startHour + index);
  const height = (endHour - startHour) * HOUR_HEIGHT;

  const byDay = new Map<string, BlockView[]>();
  for (const block of blocks) {
    const key = dateKey(new Date(block.start));
    byDay.set(key, [...(byDay.get(key) ?? []), block]);
  }

  const todayKey = dateKey(now);
  const nowOffset = ((minutesIntoDay(now) - startHour * 60) / 60) * HOUR_HEIGHT;
  const showNowLine = nowOffset >= 0 && nowOffset <= height;

  return (
    <section className="panel grid-panel">
      <div className="grid-title">
        <p className="eyebrow">Week</p>
        <span className="muted small">
          {weekStart.toLocaleDateString([], { day: "numeric", month: "short" })} ·{" "}
          {blocks.filter((block) => !block.is_past).length} remaining
        </span>
      </div>

      <div className="grid-wrap">
        <div className="grid-inner">
          <div className="grid-days">
            <div className="gutter-cell" />
            {days.map((day) => (
              <div key={dateKey(day)} className={dateKey(day) === todayKey ? "today" : ""}>
                <span>{day.toLocaleDateString([], { weekday: "short" })}</span>
                <strong>{day.getDate()}</strong>
              </div>
            ))}
          </div>

          <div className="grid" style={{ height }}>
            {hours.map((hour) => (
              <div className="hourline" key={hour} style={{ top: (hour - startHour) * HOUR_HEIGHT }} />
            ))}

            <div className="gutter">
              {hours.map((hour) => (
                <div
                  className="hour-label"
                  key={hour}
                  style={{ top: (hour - startHour) * HOUR_HEIGHT }}
                >
                  {String(hour).padStart(2, "0")}
                </div>
              ))}
            </div>

            {days.map((day) => {
              const key = dateKey(day);
              const items = (byDay.get(key) ?? [])
                .slice()
                .sort((a, b) => a.start.localeCompare(b.start));
              return (
                <div className={`col${key === todayKey ? " today" : ""}`} key={key}>
                  {items.map((block) => {
                    const begin = new Date(block.start);
                    const finish = new Date(block.end);
                    const top = ((minutesIntoDay(begin) - startHour * 60) / 60) * HOUR_HEIGHT;
                    const span = Math.max(
                      20,
                      ((finish.getTime() - begin.getTime()) / 3_600_000) * HOUR_HEIGHT - 2,
                    );
                    return (
                      <article
                        key={`${block.task_id}-${block.start}`}
                        className={`event${block.is_past ? " past" : ""}${block.is_now ? " live" : ""}${
                          span < 34 ? " compact" : ""
                        }`}
                        style={
                          {
                            top,
                            height: span,
                            "--cat": block.colour ?? "#8b96a8",
                          } as CSSProperties
                        }
                        title={`${block.title} — ${block.explanation}`}
                        data-start={block.start}
                        data-end={block.end}
                        data-task={block.task_id}
                      >
                        {span >= 34 && (
                          <span className="event-time">
                            {clock(block.start)}–{clock(block.end)}
                          </span>
                        )}
                        <span className="event-title">{block.title}</span>
                      </article>
                    );
                  })}
                  {key === todayKey && showNowLine && (
                    <div className="now-line" style={{ top: nowOffset }} />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {blocks.length === 0 && <p className="muted small empty-grid">Nothing scheduled this week.</p>}
    </section>
  );
}
