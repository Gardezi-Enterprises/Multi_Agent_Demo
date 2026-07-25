# Deploying for free (no local machine, data persists)

The app runs on **SQLite** by default (a local file) but also speaks **PostgreSQL**.
The trick to a *truly free* deployment is to put the database in a free managed
Postgres (**Neon**) and run the app on any free host. Because the data lives in
Neon, it survives even on hosts that wipe their disk — nothing is stored locally.

```
   Free host (compute)                Free database
   HF Spaces / Koyeb / Render  ─────▶  Neon Postgres
   (the container)                     (your data, persistent)
```

---

## Step 1 — Free Postgres on Neon (2 minutes)

1. Go to [neon.tech](https://neon.tech) → sign up (free, no card).
2. Create a project → it gives you a **connection string** like:
   ```
   postgresql://user:pass@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
3. Copy it. This is your `DATABASE_URL`. That's the "db credentials" — Neon
   generates them; you never manage a server.

(Supabase works the same way — use its `postgresql://…` connection string.)

## Step 2 — Generate your admin password hash (once, locally)

```bash
cd backend
python -m multi_agent_system.auth      # prints AUTH_PASSWORD_HASH=scrypt$...
```

## Step 3 — Pick a free host and set env vars

All hosts build the repo's `Dockerfile` (React + FastAPI in one image). Set these
environment variables on whichever host you choose:

| Variable | Value |
| :--- | :--- |
| `DATABASE_URL` | the Neon string from step 1 |
| `GOOGLE_API_KEY` | your Gemini key |
| `ENVIRONMENT` | `production` |
| `AUTH_ENABLED` | `true` |
| `AUTH_ALLOW_SIGNUP` | `true`  (so new users can register) |
| `AUTH_USERNAME` | your admin name |
| `AUTH_PASSWORD_HASH` | the `scrypt$…` from step 2 |
| `SECRET_KEY` | any 32+ random chars (`python -c "import secrets;print(secrets.token_urlsafe(32))"`) |
| `COOKIE_SECURE` | `true` |
| `EMAIL_ENABLED` | `true` (optional) + `EMAIL_FROM`, `SMTP_USER`, `SMTP_PASSWORD` |

No `DATABASE_URL` disk/volume is needed — the data is in Neon.

### Option A — Hugging Face Spaces  (free, no card) ★ recommended

1. [huggingface.co](https://huggingface.co) → **New Space** → **Docker** (blank).
2. In the Space's **README.md**, put this frontmatter so it serves on port 8000:
   ```yaml
   ---
   title: Taseer's Agent
   sdk: docker
   app_port: 8000
   ---
   ```
3. Push this repo's files to the Space (or connect the GitHub repo).
4. **Settings → Variables and secrets** → add the env vars from the table above
   (put `GOOGLE_API_KEY`, `DATABASE_URL`, `AUTH_PASSWORD_HASH`, `SECRET_KEY`,
   `SMTP_PASSWORD` as **Secrets**).
5. It builds and serves at `https://<user>-<space>.hf.space`. Open it, sign up.

### Option B — Koyeb  (free web service, no card)

1. [koyeb.com](https://koyeb.com) → **Create Web Service** → GitHub → this repo.
2. Builder: **Dockerfile**. Port: **8000**.
3. Add the env vars from the table. Deploy → you get a `*.koyeb.app` URL.

### Option C — Render  (free tier now works, because data is in Neon)

Render's free plan wipes its own disk, but with `DATABASE_URL` pointing at Neon
that no longer matters — your data is in Neon.

1. New → **Web Service** → this repo → runtime **Docker**, **Free** plan.
2. Add the env vars from the table (skip the disk; you don't need it with Neon).
3. Deploy. Note: free instances **sleep after 15 min** (≈30 s cold start), but
   never lose data now.

### Option D — Fly.io  (free allowance; requires a card)

`fly.toml` is included. `fly launch --no-deploy`, set secrets with
`fly secrets set DATABASE_URL=… GOOGLE_API_KEY=… AUTH_PASSWORD_HASH=… SECRET_KEY=…`,
then `fly deploy`. (Fly can also keep SQLite on a free volume if you prefer, but
Neon keeps compute and data cleanly separate.)

---

## Notes

- **Nothing is local.** Compute is on the host, data is in Neon. Sign in from any
  device; new users can register from anywhere (`AUTH_ALLOW_SIGNUP=true`).
- **Local dev is still zero-config** — omit `DATABASE_URL` and it uses a local
  SQLite file. Set `DATABASE_URL` to the Neon string and the *same code* runs on
  Postgres.
- **Rotate** the Gemini key and Gmail app password before going public.
