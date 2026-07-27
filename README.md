# nba_projection

A prop projection model with a chat UI. Ask about an NBA player's stat line
(points, rebounds, assists, steals, blocks, threes — or a combo like PRA) and
get a model-based read on the line, drawn from their recent games. Answers
render as visual **projection cards** (projected mean + an over/under
shot-meter) inline in the chat.

It's a statistical model built from recent games — not betting advice.

The frontend is a React (Vite + TypeScript) app in `frontend/`. It builds into
`src/nba_projection_bot/static/`, which FastAPI serves directly — so in
production there's still just one process.

Set up your environment first (`.env` with `ANTHROPIC_API_KEY`, `DATABASE_URL`,
`TAVILY_API_KEY`, `VOYAGE_API_KEY`).

## Running (production / single process)

Build the frontend once, then run the API — it serves both the page and `/ask`:

```bash
pip install -r requirements.txt
npm --prefix frontend install
npm --prefix frontend run build
cd src
uvicorn nba_projection_bot.api:app
```

Then open **http://127.0.0.1:8000/**. Interactive API docs are at `/docs`.

> Every question triggers a real, billed Anthropic call on the backend (possibly
> several, per the agent's tool-use loop), so it can take a few seconds.

## Running (frontend development)

For hot-reloading UI work, run the API and the Vite dev server side by side. The
dev server proxies `/ask` and `/health` to the API (see `frontend/vite.config.ts`):

```bash
# terminal 1 — API
cd src && uvicorn nba_projection_bot.api:app --reload
# terminal 2 — Vite dev server (opens on http://localhost:5173)
npm --prefix frontend run dev
```

## Tests

```bash
pytest                              # backend
npm --prefix frontend run test      # frontend (Vitest)
```

## notes

## tried to only get n games for a player in a season. however, playerGameLog returns an entire seasons' worth automatically so was unable to optimize that. sad face.

## planning to deploy to render later - keep docker containers and kubernetes in mind if needed

## add who it thinks will drop 50 points next
