import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { Aurora, TreeLogo } from "../components/TreeLogo";
import "./Auth.css";

export function Login({ signupOpen, onAuthed }: { signupOpen: boolean; onAuthed: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.login(username, password);
      onAuthed();
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign in failed.");
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
          <h1>Taseer's Agent</h1>
          <p className="sub">Sign in to continue</p>
          {error && <p className="err">{error}</p>}
          <form onSubmit={submit}>
            <label htmlFor="u">Username</label>
            <input
              id="u"
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
            <label htmlFor="p">Password</label>
            <input
              id="p"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
          <p className="foot">
            <Link to="/forgot">Forgot password?</Link>
            {signupOpen && (
              <>
                {" · "}
                No account? <Link to="/signup">Create one</Link>
              </>
            )}
          </p>
        </main>
      </div>
    </>
  );
}
