# Synthetic showcase deployment

This repository has **not** been deployed to a public host during this repair. The configuration below keeps authenticated Live Operations behind the reverse proxy and exposes only synthetic Demo Mode. Public demo runs share a synthetic SQLite ledger; visitors can see and change those synthetic runs. Use a private meeting link or add per-visitor session isolation before advertising the service broadly.

## One-host setup

1. Point two DNS names, such as `demo.example.com` and `api.example.com`, at an HTTPS-capable host. Install Docker Compose and Caddy on the host.
2. Copy `.env.example` to an untracked `.env`. Set a random `POSTGRES_PASSWORD`, `SESSION_SECRET` (at least 32 characters), and `CREDENTIAL_ENCRYPTION_KEY` if provider credentials will ever be entered. Set:

   ```text
   NEXT_PUBLIC_API_URL=https://api.example.com
   CORS_ORIGINS=https://demo.example.com
   FRONTEND_URL=https://demo.example.com
   COOKIE_SECURE=true
   DEMO_MUTATIONS_PER_HOUR=60
   DEMO_MAX_ROOT_RUNS=1000
   PUBLIC_SHOWCASE=true
   ```

3. Copy [the Caddy example](../deploy/Caddyfile.showcase.example) into the host's Caddy configuration, replace both hostnames, and start Caddy. It obtains HTTPS certificates and blocks `/live` on both hosts. The Compose API, web and database ports bind only to localhost.
4. Run `docker compose up -d --build db api web`. The optional `sync` worker is unnecessary for a synthetic-only showcase.
5. Open `https://demo.example.com/demo`, choose **Launch Demo**, inject an incident, compare branches, replay and reset. Check `https://api.example.com/health` for API availability.

The Next.js public API URL is set at build time. Rebuild `web` if it changes. Keep database ports behind the host firewall. Use a disposable database and separate secrets for this showcase. Do not enter merchant provider credentials into a public demo deployment.

## Local verification

For a full local Live test against PostgreSQL, run:

```powershell
docker compose up -d db
$env:DATABASE_URL = 'postgresql+psycopg://opsverse:opsverse_local_only@127.0.0.1:5433/opsverse'
$env:SESSION_SECRET = 'replace-with-a-long-local-test-secret'
cd apps/api
python ../../scripts/start_test_api.py
```

In another terminal run `pnpm --dir apps/web dev`. Visit `http://localhost:3000/live` for imported records and `http://localhost:3000/demo` for synthetic runs.

The anonymous mutation budget uses process memory and resets on restart. The root-run cap is stored in the synthetic SQLite database. `PUBLIC_SHOWCASE=true` hides the global run listing; a browser reload opens its local run ID. They are resource guards for this single-host showcase, not distributed abuse protection. The SQLite ledger needs retention and stronger per-visitor authorization for an open public launch. No provider write action is performed by either route.
