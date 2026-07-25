# Deploying for free (no local machine, data persists)

The app runs on **SQLite** by default (a local file) but also speaks **PostgreSQL**.
The trick to a *truly free* deployment is to put the database in a free managed
Postgres (**Neon**) and run the app on any free host. Because the data lives in
Neon, it survives even on hosts that wipe their disk — nothing is stored locally.

```
   Free host (compute)                Free database
   Render / Koyeb / Cloud Run  ─────▶  Neon Postgres
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

> **Note:** Hugging Face Spaces now require a **paid PRO plan** for Docker/compute
> Spaces (only Static Spaces are free, and those can't run a backend). Use one of
> the options below instead.

### Option A — Render  (free web service, no card) ★ recommended

Render's **free web service** runs the Docker image at no cost. The disk is what
costs money — and you don't need it, because your data is in Neon.

1. [render.com](https://render.com) → **New +** → **Web Service** → connect this repo.
2. Runtime **Docker** · Instance type **Free** · leave the Dockerfile path default.
   **Do not add a disk.**
3. **Environment** → add the env vars from the table above (`DATABASE_URL` = your
   Neon string, `GOOGLE_API_KEY`, `AUTH_PASSWORD_HASH`, `SECRET_KEY`, etc.).
4. **Create Web Service** → it builds and serves at `https://<name>.onrender.com`.
   Open it, sign up (you become the admin).

Free instances **sleep after ~15 min idle** (≈30–50 s cold start on the next
visit) but **never lose data** — everything is in Neon. Do **not** use the
`render.yaml` blueprint for the free path (it defines a paid disk); create the
service manually as above.

### Option B — Koyeb  (free web service)

1. [koyeb.com](https://koyeb.com) → **Create Web Service** → GitHub → this repo.
2. Builder: **Dockerfile**. Port: **8000**. Instance: **Free** (nano).
3. Add the env vars from the table. Deploy → you get a `*.koyeb.app` URL.

### Option C — Google Cloud Run  (generous free tier; requires a card)

Scales to zero, 60-min request limit, no data loss (data is in Neon).

```bash
gcloud run deploy taseers-agent --source . --region us-central1 \
  --allow-unauthenticated --min-instances 0 \
  --set-env-vars ENVIRONMENT=production,AUTH_ENABLED=true,AUTH_ALLOW_SIGNUP=true,COOKIE_SECURE=true \
  --set-env-vars "DATABASE_URL=<your-neon-url>" \
  --set-secrets GOOGLE_API_KEY=gemini:latest,AUTH_PASSWORD_HASH=admin-hash:latest,SECRET_KEY=secret:latest
```

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
