import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { Aurora, TreeLogo } from "../components/TreeLogo";
import "./Auth.css";

export function Forgot() {
  const [identifier, setIdentifier] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.forgotPassword(identifier);
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
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
          <h1>Reset your password</h1>
          {sent ? (
            <>
              <p className="sub">
                If an account with a recovery email matches, a reset link is on its way.
                It expires in 30 minutes.
              </p>
              <p className="foot">
                <Link to="/login">Back to sign in</Link>
              </p>
            </>
          ) : (
            <>
              <p className="sub">Enter your username or recovery email.</p>
              {error && <p className="err">{error}</p>}
              <form onSubmit={submit}>
                <label htmlFor="id">Username or email</label>
                <input
                  id="id"
                  autoFocus
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  required
                />
                <button className="primary" type="submit" disabled={busy}>
                  {busy ? "Sending…" : "Send reset link"}
                </button>
              </form>
              <p className="foot">
                Remembered it? <Link to="/login">Sign in</Link>
              </p>
            </>
          )}
        </main>
      </div>
    </>
  );
}
