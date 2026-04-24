# IGDB Integration Setup

GamesList Manager can pull rich metadata for every game in your library from
[IGDB.com](https://www.igdb.com/) — cover art, genres, summaries, the IGDB
aggregated critic rating, and average completion times ("Main Story",
"Main + Extras", "Completionist"). Once fetched, the data is stored alongside
your game in the `.gmd` file and displayed in a dedicated **Game Details** view.

IGDB uses **Twitch OAuth** for authentication, so you'll need a (free) Twitch
account and a Twitch "Application" to get a Client ID + Client Secret.

## 1. Create a Twitch account

If you don't already have one, sign up at <https://www.twitch.tv/signup>.

> You must have **two-factor authentication** enabled on the account before
> Twitch will let you create an application. Enable it in *Settings →
> Security and Privacy*.

## 2. Register an application

1. Open the Twitch developer console: <https://dev.twitch.tv/console/apps>
2. Click **Register Your Application**.
3. Fill in the form:
   - **Name** — anything, e.g. `GamesList Manager (Personal)`
   - **OAuth Redirect URLs** — `http://localhost` (required field, not actually
     used by GamesList)
   - **Category** — *Application Integration*
   - **Client Type** — *Confidential*
4. Click **Create**.

## 3. Copy your Client ID and Client Secret

1. On the applications list, click **Manage** next to your new app.
2. Copy the **Client ID**.
3. Click **New Secret** and copy the **Client Secret** shown — Twitch only
   shows the secret once, so keep this browser tab open until you've pasted
   it into GamesList.

## 4. Enter the credentials in GamesList

1. In GamesList Manager, open **Options → IGDB Settings**.
2. Check **Enable IGDB integration**.
3. Paste the Client ID and Client Secret.
4. (Optional) Check **Auto-apply strong matches when fetching metadata** if
   you'd like GamesList to skip the match picker when a single IGDB entry
   clearly matches your game's name and year.
5. Click **Test Connection**. You should see *"Connection succeeded."*
6. Click **Save**.

## 5. Fetch metadata

You can fetch metadata two ways:

- **Per game** — right-click (or double-click) a game in the Games List,
  choose **View Details**, then click **Fetch Metadata**. If more than one
  IGDB entry could match, GamesList shows a picker with the top 5 candidates.
- **Entire library** — **Options → Enrich Library from IGDB** walks every
  game that doesn't yet have IGDB data. Matches that can't be auto-resolved
  are queued and presented for your review at the end of the run so the
  process isn't interrupted mid-way.

All network calls respect IGDB's rate limits (4 req/s), are retried with
backoff on transient errors, and are cached locally — once a game has been
matched, its cover image lives in
`%APPDATA%\GamesListManager\igdb_cache\covers\` (Windows) /
`~/.config/GamesListManager/igdb_cache/covers/` (Linux) /
`~/Library/Application Support/GamesListManager/igdb_cache/covers/` (macOS).

## What gets stored

Every enriched game gains an `igdb` block inside the `.gmd` JSON:

```json
{
  "igdb_id": 1942,
  "slug": "the-witcher-3-wild-hunt",
  "cover_image_id": "co1wyy",
  "cover_url": "https://images.igdb.com/.../t_cover_big/co1wyy.jpg",
  "cover_cache": "covers/1942.jpg",
  "genres": ["RPG", "Adventure"],
  "platforms": ["PC (Microsoft Windows)", "PlayStation 4"],
  "summary": "...",
  "aggregated_rating": 93.1,
  "aggregated_rating_count": 27,
  "release_date": "2015-05-18",
  "time_to_beat": {"hastily": 180000, "normally": 360000, "completely": 720000, "count": 12345},
  "fetched_at": "2026-04-20T15:00:00"
}
```

The block is optional — older `.gmd` files without any IGDB data continue to
load unchanged, and you can remove metadata at any time via **View Details →
Remove Metadata**.

## Troubleshooting

- **"Auth failed" on Test Connection** — double-check that you pasted the
  Client ID and Client Secret from the *same* Twitch application, with no
  extra spaces.
- **"IGDB rate limit exceeded"** — rare, but if it happens, wait a minute and
  try again. The client already backs off automatically on transient 429s.
- **Covers don't appear** — the popup shows cached images from the cache
  directory above. Delete the `covers/` folder to force re-downloads.
- **Match picker is empty** — IGDB may not index every obscure title. Use
  *Not in IGDB (skip)* to dismiss the prompt; the game will simply keep its
  user-entered data.

## Privacy

- Your Twitch Client ID and Client Secret are stored **locally** in the
  GamesList config file under your user directory. They are never transmitted
  anywhere except directly to Twitch/IGDB when requesting data.
- GamesList does not send your library, ratings, or sessions to IGDB — only
  game-name searches and IGDB ID lookups.
