import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { Aurora, TreeLogo } from "../components/TreeLogo";
import "./Auth.css";

export function Reset({ onAuthed }: { onAuthed: () => void }) {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.resetPassword(token, password);
      onAuthed();
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reset password.");
      setBusy(false);
    }
  }

  return (
    <>
      <Aurora />
      <div className="auth-wrap">
        <main className="auth-card">
          <div className="brand-mark">
            <TreeLogo size={64} />
          </div>
          <h1>Choose a new password</h1>
          {!token ? (
            <>
              <p className="err">This reset link is missing its token.</p>
              <p className="foot">
                <Link to="/forgot">Request a new link</Link>
              </p>
            </>
          ) : (
            <>
              <p className="sub">Enter a new password for your account.</p>
              {error && <p className="err">{error}</p>}
              <form onSubmit={submit}>
                <label htmlFor="p">New password</label>
                <input
                  id="p"
                  type="password"
                  autoComplete="new-password"
                  autoFocus
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
                <label htmlFor="c">Confirm password</label>
                <input
                  id="c"
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  required
                />
                <button className="primary" type="submit" disabled={busy}>
                  {busy ? "Saving…" : "Set new password"}
                </button>
              </form>
              <p className="foot">
                <Link to="/login">Back to sign in</Link>
              </p>
            </>
          )}
        </main>
      </div>
    </>
  );
}
