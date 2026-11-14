// The API client. Thin on purpose: the server already returns everything the UI
// renders — including the explanation — so the client's job is not to compute
// anything, only to ask and display.

export interface BlockView {
  task_id: string;
  title: string;
  category: string;
  category_name: string;
  colour: string | null;
  start: string;
  end: string;
  minutes: number;
  priority: number;
  explanation: string;
  blocking: string[];
  is_now: boolean;
  is_past: boolean;
  moved_from: string | null;
  energy?: string | null;
  context?: { people: string[]; place: string | null; requires: string[]; tags: string[] };
}

export interface Shortfall {
  task_id: string;
  title: string;
  required_minutes: number;
  placed_minutes: number;
}

export interface LateView {
  task_id: string;
  title: string;
  due: string;
  finishes: string;
  minutes_late: number;
}

export interface PlanView {
  origin: string;
  horizon_slots: number;
  solve_ms: number;
  complete: boolean;
  disturbance_minutes: number;
  blocks: BlockView[];
  shortfalls: Shortfall[];
  late: LateView[];
  warnings?: string[];
  solved_at?: string | null;
}

export interface NowView extends BlockView {
  state: "now" | "next";
  starts_in_minutes: number;
  ends_in_minutes: number;
  warnings?: string[];
}

export interface Category {
  id: string;
  name: string;
  colour: string;
  energy: string | null;
  windows: [number, number][];
  blocking: string[];
  description: string;
}

export interface Me {
  username: string;
  timezone: string;
  categories: Category[];
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  me: () => request<Me>("/api/me"),
  login: (username: string, password: string) =>
    request<void>("/api/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<void>("/api/logout", { method: "POST" }),
  plan: () => request<PlanView>("/api/plan"),
  now: () => request<NowView>("/api/now"),
  solve: () => request<PlanView>("/api/plan/solve", { method: "POST" }),
  progress: (taskId: string, remaining: number | null) =>
    request<unknown>(`/api/tasks/${encodeURIComponent(taskId)}/progress`, {
      method: "POST",
      body: JSON.stringify({ remaining }),
    }),
  capture: (note: string) =>
    request<{
      created: string[];
      routines: string[];
      reasoning: Record<string, string>;
      warnings: string[];
      plan: PlanView;
    }>("/api/capture", { method: "POST", body: JSON.stringify({ note }) }),
};

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function dayLabel(iso: string): string {
  const date = new Date(iso);
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (same(date, today)) return "Today";
  if (same(date, tomorrow)) return "Tomorrow";
  return date.toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" });
}

export function minutesToWords(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}
