# Carbon DC EMS — Skid 2 Admin UI (prototype)

Self-contained single-file web app (`index.html`). Served from this repo at
`/ems-ui/` when the Vite panel is running.

## Connect to the carbondcai backend

1. Start the stack (backend on `:8000`, panel on `:5173`).
2. Open the UI: <http://127.0.0.1:5173/ems-ui/>
   Public: <https://carbondcai-ems.pages.dev/> · <https://carbon-dc-ems-ui.netlify.app/>
3. Use the top-bar **Simulator** / **Live** switch (seed user `admin` / `admin` for Live).

**Live** pulls KPIs from `/api/v1/ems/supervisor`, `/api/v1/ems/telemetry/all`, and
`/api/v1/ems/charger/status` (proxied by Vite to the FastAPI backend). **Simulator**
falls back to the in-browser simulation. When Live is active, **Backend sim** /
**Backend live** toggles the EMS hub data source (`PUT /api/v1/ems/mode`).

If you open the HTML outside Vite, the UI targets `http://127.0.0.1:8000/api` directly
(CORS allows the local panel origins).

## Deploy to Netlify (static / sim-only)

From this folder (linked to `carbon-dc-ems-ui-demo`):

```bash
netlify link --id d7dca657-baaf-40fb-9fa0-761e466a92ab
netlify deploy --dir=. --prod
```

Or drag this folder onto the Netlify Deploys drop zone.

Cloudflare Pages (`carbondcai-ems`):

```bash
npx wrangler pages deploy . --project-name carbondcai-ems
```

**Live Connect from Netlify** needs a *public* FastAPI URL (Netlify cannot reach your
laptop’s `127.0.0.1:8000`). Options:

1. Click **Connect** and enter `https://your-api-host/api` (stored in `localStorage`), or
2. Open with `?api=https://your-api-host/api`, or
3. Set `apiBase` in `config.js`, or uncomment the `/api/*` proxy in `netlify.toml`.

Backend CORS already allows the Netlify demo origins; add more via `SKID2_CORS_ORIGINS`.

## Notes

- Light/dark toggle (top-right). Theme persists via localStorage.
- Built-in UI review/annotation layer (💬 Review, bottom-right) — comments export to .md/.json.
- MPPT registers use the PDS1750K placeholder map pending the CNVTR-1/2 maps.
