import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type Account } from "../api";
import { useToast } from "./Toasts";

interface Props {
  me: { username: string; is_admin: boolean; email: string };
  onClose: () => void;
  onSignedOut: () => void;
}

export function AccountModal({ me, onClose, onSignedOut }: Props) {
  const [tab, setTab] = useState<"password" | "admin">("password");
  const toast = useToast();

  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Account · {me.username}</h3>
        {me.is_admin && (
          <div className="tabs2">
            <button className={tab === "password" ? "on" : ""} onClick={() => setTab("password")}>
              Password
            </button>
            <button className={tab === "admin" ? "on" : ""} onClick={() => setTab("admin")}>
              Accounts
            </button>
          </div>
        )}
        {tab === "password" ? (
          <>
            <RecoveryEmailForm email={me.email} onDone={() => toast("Recovery email saved", "ok")} />
            <div style={{ height: "1px", background: "var(--line)", margin: "1.25rem 0" }} />
            <PasswordForm onDone={() => toast("Password changed", "ok")} />
          </>
        ) : (
          <AdminPanel me={me} />
        )}
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
          <button
            className="ghost"
            style={{ flex: 1 }}
            onClick={async () => {
              await api.logout();
              onSignedOut();
            }}
          >
            Sign out
          </button>
          <button className="ghost" style={{ flex: 1 }} onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function RecoveryEmailForm({ email, onDone }: { email: string; onDone: () => void }) {
  const [value, setValue] = useState(email);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.setRecoveryEmail(value);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save email.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      {error && <p className="err">{error}</p>}
      <label>Recovery email <span style={{ color: "var(--fg-3)", fontWeight: 400 }}>— for password reset</span></label>
      <input type="email" value={value} onChange={(e) => setValue(e.target.value)} placeholder="you@example.com" />
      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Saving…" : "Save recovery email"}
      </button>
    </form>
  );
}

function PasswordForm({ onDone }: { onDone: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.changePassword(current, next);
      setCurrent("");
      setNext("");
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      {error && <p className="err">{error}</p>}
      <label>Current password</label>
      <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
      <label>New password</label>
      <input type="password" value={next} onChange={(e) => setNext(e.target.value)} required />
      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Saving…" : "Change password"}
      </button>
    </form>
  );
}

function AdminPanel({ me }: { me: { username: string } }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [error, setError] = useState("");
  const toast = useToast();

  const load = () => api.listAccounts().then((r) => setAccounts(r.accounts)).catch(() => {});
  useEffect(() => {
    load();
  }, []);

  async function add(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api.createAccount(username, password, isAdmin);
      setUsername("");
      setPassword("");
      setIsAdmin(false);
      toast("Account created", "ok");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create account.");
    }
  }

  async function remove(name: string) {
    try {
      await api.deleteAccount(name);
      toast(`Removed ${name}`, "ok");
      load();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Could not remove account.", "err");
    }
  }

  return (
    <div>
      <div style={{ maxHeight: "180px", overflowY: "auto", marginBottom: "1rem" }}>
        {accounts.map((a) => (
          <div className="acct-row" key={a.id}>
            <span className="name">{a.username}</span>
            {a.is_admin ? <span className="tag">admin</span> : null}
            {a.username.toLowerCase() !== me.username.toLowerCase() && (
              <button className="del" aria-label={`Remove ${a.username}`} onClick={() => remove(a.username)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        ))}
      </div>
      <form onSubmit={add}>
        {error && <p className="err">{error}</p>}
        <label>New account</label>
        <input placeholder="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        <input
          type="password"
          placeholder="password (min 8 chars)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontWeight: 400, margin: 0 }}>
          <input
            type="checkbox"
            style={{ width: "auto" }}
            checked={isAdmin}
            onChange={(e) => setIsAdmin(e.target.checked)}
          />
          Administrator
        </label>
        <button className="primary" type="submit">
          Create account
        </button>
      </form>
    </div>
  );
}
