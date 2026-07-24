import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api, type Me } from "./api";
import { Aurora, TreeLogo } from "./components/TreeLogo";
import { Login } from "./pages/Login";
import { Signup } from "./pages/Signup";
import { Forgot } from "./pages/Forgot";
import { Reset } from "./pages/Reset";
import { Chat } from "./pages/Chat";

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = () =>
    api
      .me()
      .then(setMe)
      .catch(() => setMe({ authenticated: false, signup_open: false, first_run: false }));

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <>
        <Aurora />
        <div style={{ display: "grid", placeItems: "center", height: "100%" }}>
          <TreeLogo size={72} thinking />
        </div>
      </>
    );
  }

  const authed = !!me?.authenticated;

  return (
    <Routes>
      <Route
        path="/login"
        element={
          authed ? <Navigate to="/" replace /> : <Login signupOpen={!!me?.signup_open} onAuthed={refresh} />
        }
      />
      <Route
        path="/signup"
        element={
          authed ? (
            <Navigate to="/" replace />
          ) : me?.signup_open ? (
            <Signup isFirst={!!me?.first_run} onAuthed={refresh} />
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
      <Route path="/forgot" element={authed ? <Navigate to="/" replace /> : <Forgot />} />
      <Route
        path="/reset"
        element={authed ? <Navigate to="/" replace /> : <Reset onAuthed={refresh} />}
      />
      <Route
        path="/"
        element={
          authed ? (
            <Chat
              key={me!.username}
              me={{ username: me!.username!, is_admin: !!me!.is_admin, email: me!.email || "" }}
              onSignedOut={refresh}
            />
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
      <Route path="*" element={<Navigate to={authed ? "/" : "/login"} replace />} />
    </Routes>
  );
}
