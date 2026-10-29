# 037: App shell (header, nav, user chip, footer)

Status: built

## Goal

Every page gets the same header (brand, nav "New chart" and "Your style", signed-in email chip) and
slim footer (what the product does, plus a "dev" tag in local mode), built once from a `ShellView`
view model. GET pages show the user when known and never return 401 because of the shell.

## Design plan (frontend-design skill, checked against the plan's design direction)

The look is already fixed by task 036 ("graph paper and ink"); this task only places the shell
components that 036 defined. No new colours, fonts or tokens.

- Colour (existing tokens only): header and footer on `--color-surface` with a 3px
  `--color-border` rule; current nav item on `--color-highlight` (marigold `#ffc93c`) with
  `--color-on-fill` text and a 2px ink border; user chip on `--color-neutral` with
  `--color-on-fill`; dev tag on `--color-highlight`; skip link on `--color-highlight`. All these
  pairs are already in `PAIRS` in `tests/test_design_tokens.py`.
- Type: brand in `--font-display` (900, `--text-lg`, existing `.brand` with the bar-chart mark);
  nav 800 weight `--text-base`; chip and footer `--text-sm`.
- Layout (matches the 036 mock), left aligned, content column 44rem:

  ```
  1280px                                            360px
  +-----------------------------------------------+  +---------------------------+
  | ▂▅▃ Topic Studio  [New chart] Your style  (a@b)|  | ▂▅▃ Topic Studio          |
  +-----------------------------------------------+  | [New chart] Your style    |
  |  main                                          |  | (friend@example.com)      |
  +-----------------------------------------------+  +---------------------------+
  | Charts from Our World in Data, ... [dev]       |  | main ...                  |
  +-----------------------------------------------+  | footer text wraps [dev]   |
                                                     +---------------------------+
  ```
- Principles: the chart frame stays the one bold element; the shell is quiet chrome. No
  hamburger, no JavaScript: `.site-header` and `.site-nav` already use `flex-wrap`, so the nav and
  chip drop to new lines on phones. The old header tagline ("An editorial desk...") is removed,
  as in the mock.

## Decisions

1. **Optional user** (`web/dependencies.py`): add `optional_user(request, settings =
   Depends(get_settings)) -> CurrentUser | None`. Local mode returns `CurrentUser(email=
   DEV_USER_EMAIL)` (header ignored). Cloudflare mode reads `CF_EMAIL_HEADER`, `.strip().lower()`;
   blank or missing returns `None` (no fake user). `current_user` is rewritten to depend on it:
   `current_user(user: CurrentUser | None = Depends(optional_user)) -> CurrentUser` raises the same
   `HTTPException(401, "authentication required")` when `None`. One parsing path, behaviour of
   `current_user` unchanged.
2. **Who uses what**: POST routes (`POST /runs`, `POST /runs/{id}/decision`, `POST /settings`) keep
   `current_user`. `GET /settings` also keeps `current_user`, because it loads that person's saved
   style and cannot work without an email; it stays 401 without the header (auth rules are out of
   scope; `test_cloudflare_requires_header` is unchanged). Every other GET page (`/`,
   `/runs/{id}`, `/runs/{id}/status`, `/health`) has no user gate and the shell never raises, so
   they return 200 with no chip when the header is missing.
3. **Shell model** (`web/view_models.py`):
   ```python
   class NavItem(BaseModel):
       label: str
       href: str
       current: bool = False

   class ShellView(BaseModel):
       brand: str = "Topic Studio"
       brand_href: str = "/"
       nav: list[NavItem]
       user_email: str | None = None
       env_tag: str = ""      # "dev" when auth_mode == "local", else ""
       tagline: str = SHELL_TAGLINE

   def build_shell(path: str, user_email: str | None, auth_mode: <type of Settings.auth_mode>) -> ShellView
   ```
   `SHELL_TAGLINE = "Charts from Our World in Data, posted to Bluesky once you approve them."`
   Nav, in order: `NavItem("New chart", "/")`, `NavItem("Your style", "/settings")`.
   Current flags: "New chart" when `path == "/"`, `path == "/runs"` or `path.startswith("/runs/")`
   (home, POST /runs error re-renders, run page, run not found); "Your style" when
   `path == "/settings"`; nothing for `/health` or any other path. Task 038 extends this for its
   new flow paths. Pure function, no I/O.
4. **One hook for every template** (no per-route code): in `web/dependencies.py` add
   `shell_context(request: Request) -> dict[str, ShellView]` that calls `get_settings(request)`,
   `optional_user(request, settings)` (plain call) and `build_shell(request.url.path, email,
   settings.auth_mode)`, returning `{"shell": ...}`. `create_app` passes it as
   `Jinja2Templates(directory=..., context_processors=[shell_context])`. Starlette runs it for every
   `TemplateResponse`, so home, run, run not found, settings, health and all error re-renders (400,
   409, 429, 503, 404) get `shell` with no route changes. Routes stay as they are. It does no I/O
   (reads `app.state` and one header).
5. **Markup** (`templates/base.html`, logic-free):
   ```html
   <body>
     <a class="skip-link" href="#main">Skip to content</a>
     <header class="site-header">
       <a class="brand" href="{{ shell.brand_href }}">{{ shell.brand }}</a>
       <nav class="site-nav" aria-label="Main">
         {% for item in shell.nav %}<a href="{{ item.href }}"{% if item.current %} aria-current="page"{% endif %}>{{ item.label }}</a>{% endfor %}
       </nav>
       {% if shell.user_email %}<span class="user-chip"><span class="visually-hidden">Signed in as </span>{{ shell.user_email }}</span>{% endif %}
     </header>
     <main id="main">{% block content %}{% endblock %}</main>
     <footer class="site-footer">
       <span>{{ shell.tagline }}</span>
       {% if shell.env_tag %}<span class="dev-tag"><span class="visually-hidden">Environment: </span>{{ shell.env_tag }}</span>{% endif %}
     </footer>
   </body>
   ```
   `<header>`, `<main>`, `<footer>` as direct children of `<body>` give the banner, main and
   contentinfo landmarks; no redundant `role` attributes. The chip is a plain `<span>` (not a link,
   no `role="status"`: `test_get_shows_email_and_defaults` asserts `role="status"` is absent).
   Focus order: skip link, brand, New chart, Your style, then page content. The `<title>` block and
   head are unchanged.
6. **CSS** (`static/app.css`, existing tokens only):
   - Remove the `.tagline` rule (no longer used).
   - Skip link: `.skip-link { position: absolute; top: var(--space-2); left: var(--space-4);
     z-index: 10; padding: var(--space-2) var(--space-4); background: var(--color-highlight);
     color: var(--color-on-fill); border: var(--border-width) solid var(--color-border);
     border-radius: var(--radius-sm); box-shadow: var(--shadow-sm); font-weight: 800;
     text-decoration: none; transform: translateY(-200%); }` and
     `.skip-link:focus { transform: none; }` (moves vertically only, so no sideways overflow; no
     transition, so nothing for reduced motion to turn off).
   - `.user-chip`: add `max-width: 100%` (long emails wrap via the body's `overflow-wrap: anywhere`).
   - Sticky footer: `body { display: flex; flex-direction: column; min-height: 100vh; }` and
     `main { flex: 1 0 auto; width: 100%; }` (keep `max-width: 44rem` and `margin-inline: auto`).
   - No other component changes.

## Acceptance criteria

- [ ] Home, run page, run not found, settings, health and every error re-render of them (400, 409,
      429, 503, 404) render the skip link, `<header class="site-header">` with brand and
      `<nav class="site-nav" aria-label="Main">` holding "New chart" (`href="/"`) and "Your style"
      (`href="/settings"`), `<main id="main">` and `<footer class="site-footer">` with
      `SHELL_TAGLINE`; the old header tagline is gone.
- [ ] `aria-current="page"` is on "New chart" for `/` and `/runs...`, on "Your style" for
      `/settings`, and on no nav item for `/health`.
- [ ] Cloudflare mode: the chip shows the normalised header email when present; with the header
      missing or blank, `GET /`, `/runs/{id}`, `/runs/{id}/status` and `/health` return 200 with no
      `user-chip`; POST routes and `GET /settings` still return 401. Local mode shows
      `dev@localhost` in the chip and the "dev" tag; cloudflare mode never shows the dev tag.
- [ ] `current_user` is built on `optional_user`; no route builds the shell by hand
      (context processor in `create_app` only).
- [ ] No horizontal scroll at 360px on home, run and settings pages, light and dark (builder takes
      screenshots as in 036 and notes the result in the handoff); `just lint` and `just test` pass.
- [ ] `docs/architecture/tech.md` describes `optional_user`, `shell_context`, `ShellView` and the
      shell markup; the "reserved, not used yet" list no longer names `.user-chip`, `.site-footer`,
      `.dev-tag`.

## Files to touch

- `src/ai_trend_pipeline/web/dependencies.py` (`optional_user`, `current_user` on top of it,
  `shell_context`)
- `src/ai_trend_pipeline/web/view_models.py` (`NavItem`, `ShellView`, `SHELL_TAGLINE`,
  `build_shell`)
- `src/ai_trend_pipeline/web/app.py` (`context_processors=[shell_context]`)
- `src/ai_trend_pipeline/web/templates/base.html`
- `src/ai_trend_pipeline/web/static/app.css`
- `tests/test_web_shell.py` (new)
- `docs/architecture/tech.md` ("Layering" bullet on the user dependency, "Dependencies", "Auth",
  "Templates, view models, design")

## Tests to write

All in `tests/test_web_shell.py`, using `auth_client`/`client_for` from `tests/test_web.py` and
`make_client` from `tests/test_web_settings.py`.

- `build_shell` current flags: `/` and `/runs` and `/runs/wf-1` mark only "New chart";
  `/settings` marks only "Your style"; `/health` marks none.
- `build_shell` env tag: "dev" for `local`, "" for `cloudflare`; `user_email` passes through,
  `None` stays `None`; nav hrefs are `/` and `/settings`; tagline equals `SHELL_TAGLINE`.
- `optional_user` (via a route): cloudflare with `" Friend@Example.com "` shows chip text
  `friend@example.com` on `GET /`.
- Cloudflare without header and with a blank header: `GET /`, `/runs/wf-1`, `/runs/wf-1/status`,
  `/health` return 200 and contain no `user-chip` and no `dev-tag`.
- Cloudflare without header: `POST /runs`, `POST /runs/wf-1/decision`, `POST /settings` and
  `GET /settings` return 401 (existing tests cover some; assert all four here in one test).
- Local mode: `GET /` and `GET /health` show `dev@localhost` inside `user-chip` and the `dev-tag`,
  even when a CF header with another email is sent.
- Landmarks on every page: home, run, run not found (404), settings, health, a 400 `POST /runs`
  re-render, a 409 decision re-render and a 400 `POST /settings` re-render each contain the skip
  link (`class="skip-link" href="#main"`) before `<header class="site-header">`,
  `aria-label="Main"`, `id="main"`, `class="site-footer"` and the tagline, and no
  "An editorial desk".
- `aria-current="page"` appears exactly once on `/` and `/settings` (on the right link) and zero
  times on `/health`.
- CSS: `app.css` contains `.skip-link`, `.skip-link:focus` and no `.tagline` rule.

Existing tests: no assertion changes are expected. Checked: `test_home_components` (`site-header`),
`test_home_has_settings_link` (`href="/settings"`, "Your style"), `test_get_shows_email_and_defaults`
(email present, `role="status"` absent), `test_cloudflare_requires_header` (GET /settings 401),
`test_component_classes_present` (`.user-chip`, `.site-footer`, `.dev-tag`) all still hold. If one
fails, change only the visible-text assertion and name it in the handoff.

## Out of scope

- The stepper and flow pages (038), new routes, any JavaScript, hamburger or toggle menu.
- Backend, services, workflow or activity changes; changing auth rules (including making
  `GET /settings` work without a user).
- New colour tokens, fonts or restyling other components; removing other unused CSS (041).
- README and history updates (041).

## Handoff log

<!-- Each agent appends at most 5 lines: "**agent**: what I did. Next: ...". Newest at the bottom. -->

**planner**: Spec written with the frontend-design skill loaded; design reuses 036 tokens and mock markup, adds only `.skip-link` and sticky-footer CSS.
Shell reaches every template through one Starlette `context_processors` hook (`shell_context`), so routes are untouched; `current_user` now wraps `optional_user`.
Deliberate exception: `GET /settings` stays 401 without the header (it needs an email; auth rules out of scope). Human may want to confirm this reading of "GET routes never return 401".
Next: orchestrator approves (approval no), builder implements on `task/037-app-shell`.

**builder**: Shell built as specced (optional_user, shell_context via context_processors, ShellView, base.html, skip-link and sticky-footer CSS); tests in tests/test_web_shell.py pin the GET /settings 401 exception; tech.md updated. No existing assertions changed.
Checked 360/1280px, light/dark, local and cloudflare (email, long email, no header): no overflow, nav wraps cleanly, skip link and focus rings visible. frontend-design skill was loaded. Shots: scratchpad/build037/shots/.
