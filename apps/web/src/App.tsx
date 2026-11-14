import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Me, type NowView, type PlanView } from "./api";
import Capture from "./Capture";
import Now from "./Now";
import Week from "./Week";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(true);
  const [plan, setPlan] = useState<PlanView | null>(null);
  const [now, setNow] = useState<NowView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [nextPlan, nextNow] = await Promise.all([
      api.plan().catch((cause: unknown) => {
        if (cause instanceof ApiError && cause.status === 404) return null;
        throw cause;
      }),
      api.now().catch((cause: unknown) => {
        if (cause instanceof ApiError && cause.status === 404) return null;
        throw cause;
      }),
    ]);
    setPlan(nextPlan);
    setNow(nextNow);
  }, []);

  useEffect(() => {
    api
      .me()
      .then(async (user) => {
        setMe(user);
        await load();
      })
      .catch(() => setMe(null))
      .finally(() => setChecking(false));
  }, [load]);

  // The plan can change underneath a client that is being rescheduled by another
  // device, so poll gently and refetch on focus rather than trusting a snapshot.
  useEffect(() => {
    if (!me) return;
    const timer = window.setInterval(() => {
      void load().catch(() => undefined);
    }, 60_000);
    const onFocus = () => void load().catch(() => undefined);
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [me, load]);

  async function replan() {
    setBusy(true);
    setError(null);
    try {
      setPlan(await api.solve());
      setNow(await api.now().catch(() => null));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "could not re-plan");
    } finally {
      setBusy(false);
    }
  }

  async function done(taskId: string) {
    setBusy(true);
    setError(null);
    try {
      await api.progress(taskId, 0);
      setPlan(await api.solve());
      setNow(await api.now().catch(() => null));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "could not record progress");
    } finally {
      setBusy(false);
    }
  }

  if (checking) {
    return <main className="screen center muted">Checking session…</main>;
  }

  if (!me) {
    return <Login onSignedIn={async (user) => {
      setMe(user);
      await load();
    }} />;
  }

  return (
    <main className="screen">
      <header className="topbar">
        <div>
          <span className="brand">schedulebot</span>
          <span className="muted"> · {me.timezone}</span>
        </div>
        <div className="topbar-actions">
          <button onClick={replan} disabled={busy}>
            {busy ? "Working…" : "Re-plan"}
          </button>
          <button
            className="ghost"
            onClick={async () => {
              await api.logout();
              setMe(null);
              setPlan(null);
              setNow(null);
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      {error && <p className="error banner">{error}</p>}

      <div className="layout">
        <div className="column">
          <Now now={now} onDone={done} busy={busy} />
        </div>
        <div className="column">
          <Capture
            onCaptured={async (nextPlan) => {
              setPlan(nextPlan);
              setNow(await api.now().catch(() => null));
            }}
          />
          <Week plan={plan} />
        </div>
      </div>
    </main>
  );
}

function Login({ onSignedIn }: { onSignedIn: (me: Me) => Promise<void> }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <main className="screen center">
      <form
        className="panel login"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await api.login(username, password);
            await onSignedIn(await api.me());
          } catch (cause) {
            setError(
              cause instanceof ApiError && cause.status === 429
                ? "Too many attempts. Wait a few minutes."
                : "Wrong username or password.",
            );
          } finally {
            setBusy(false);
          }
        }}
      >
        <span className="brand">schedulebot</span>
        <label>
          Username
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoFocus
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
          />
        </label>
        <button className="primary" type="submit" disabled={busy || !username || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {error && <p className="error">{error}</p>}
        <p className="muted small">Accounts are created on the host — there is no sign-up.</p>
      </form>
    </main>
  );
}
