import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { Aurora, TreeLogo } from "../components/TreeLogo";
import "./Auth.css";

export function Signup({ isFirst, onAuthed }: { isFirst: boolean; onAuthed: () => void }) {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.signup(username, password, email, code);
      onAuthed();
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign up failed.");
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
          <h1>{isFirst ? "Create the owner account" : "Create your account"}</h1>
          <p className="sub">
            {isFirst
              ? "This first account is the administrator."
              : "Choose a username and password."}
          </p>
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
            <label htmlFor="e">Recovery email</label>
            <input
              id="e"
              type="email"
              autoComplete="email"
              placeholder="so you can reset your password"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <label htmlFor="p">Password</label>
            <input
              id="p"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            {!isFirst && (
              <>
                <label htmlFor="c">Invite code</label>
                <input id="c" value={code} onChange={(e) => setCode(e.target.value)} />
              </>
            )}
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "Creating…" : "Create account"}
            </button>
          </form>
          <p className="foot">
            Already have an account? <Link to="/login">Sign in</Link>
          </p>
        </main>
      </div>
    </>
  );
}
