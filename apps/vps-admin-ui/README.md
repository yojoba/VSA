# VSA Dashboard UI

Next.js 14 frontend for the centralized VSA management dashboard at `dashboard.flowbiz.ai`.

## Stack

- **Next.js 14** — React framework with App Router
- **Tailwind CSS** — utility-first styling
- **React Query** — server state management (auto-refresh every 30s)
- **lucide-react** — icons

## Pages

| Route | Description |
|-------|-------------|
| `/` | Overview — stat cards (containers, domains, certs, VPS nodes) + stack list |
| `/containers` | Container table with name, image, status, compose project |
| `/domains` | Domain registry table with container, port, VPS, status |
| `/certificates` | SSL certificate table with issuer, expiry, status |
| `/audit` | Audit log viewer with actor/action filters, pagination, CSV export |
| `/vps` | VPS node table with hostname, IP, status, last seen |

## Development

```bash
npm install
npm run dev             # Start dev server on :3000
```

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `/api` | Dashboard API base URL |

## Deployment

Deployed as part of the dashboard stack at `stacks/dashboard/`. The NGINX reverse proxy routes:
- `/api/*` to `dashboard-api:8000`
- `/*` to `dashboard-ui:3000`

See `stacks/dashboard/README.md`.
