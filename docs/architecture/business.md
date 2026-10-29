# Business side: data, charts, approval

What the product does and the rules it applies. For how it is built see [tech.md](tech.md); for the
overview see [README.md](README.md).

Contents: [1 Data](#1-data) | [2 Charts](#2-charts) | [3 Approval and posting](#3-approval-and-posting) | [4 Known limitations](#4-known-limitations)

## 1. Data

All external data enters through activities (see [workflow.md](workflow.md)). Each activity input
and output is a Pydantic model so it survives the Temporal data converter.

### Pipeline of data

```
 HN top stories ─▶ HNStory[] ─▶ pick_topic ─▶ topic title ─┐
 (skipped when a topic is typed)                           ▼
                                  match_dataset ─▶ OWID CSV ─▶ OwidRow[] ─▶ filter_table_rows ─▶ OwidTable
```

### Hacker News (`hn.py`)

- `HNActivities.fetch_hn_top_stories(HNTopStoriesInput)` calls `https://hacker-news.firebaseio.com/v0/`
  `topstories.json`, takes the first `limit` ids (default 30, 1 to 100) and fetches each item
  concurrently (`asyncio.gather`).
- Keeps only items with `type == "story"`, not dead or deleted, with a title.
- Output `HNTopStoriesOutput.stories: list[HNStory]` (`id`, `title`, `url`, `score`, `by`, `time`
  as UTC datetime, `comments`). Client: `make_hn_client()` (httpx, 10 s timeout).
- Used only when the run has no manual topic.

### Topic picking (`topic.py`)

- `TopicActivities.pick_topic(PickTopicInput)` sends the stories to Claude with the tool
  `pick_topic`. The tool schema restricts `topic` to an enum of `CATALOG_TITLES` (the six titles in
  `owid.CATALOG`). `tool_choice` is `auto` (a forced tool was rejected by the model).
- The returned title is matched case-insensitively against the catalog and normalised to the
  catalog spelling. Output `PickTopicOutput(topic, reason)`.
- An off-catalog title raises `ApplicationError(type="TopicNotInCatalog", non_retryable=False)`, so
  Temporal retries the activity (`LLM_RETRY_POLICY`, 3 attempts). A response with no tool call is
  non-retryable.
- `DEFAULT_MODEL = "claude-sonnet-5-5"` is shared by `chart_spec.py` and `critique.py`.
  `make_anthropic_client(settings)` raises if `ANTHROPIC_API_KEY` is unset.

#### Manual topic override

`DailyChartInput.topic` set (web box, `just chart --topic "..."`) skips both HN and `pick_topic`;
`topic_reason` becomes `"Manually chosen"` (`MANUAL_TOPIC_REASON`). The typed text goes straight to
`fetch_owid_dataset`, so it must match the catalog (below) or the run fails with `NoMatchingDataset`.

### Our World in Data (`owid.py`)

- `CATALOG` is a tuple of `OwidDataset(slug, title, keywords)`: CO2 emissions per capita, Internet
  use, solar energy consumption, electric car sales share, life expectancy, GDP per capita.
- `match_dataset(topic)`: exact case-insensitive title match first, then whole-word keyword match
  (`\b...\b`). Returns `None` if nothing matches.
- `OwidActivities.fetch_owid_dataset(FetchOwidInput)` GETs
  `https://ourworldindata.org/grapher/<slug>.csv?csvType=full&useColumnShortNames=true`.
  No match raises `NoMatchingDataset` (non-retryable); HTTP 404 is non-retryable.
- CSV shape: header starts `Entity,Code,Year`, column 4 is the value (name kept as
  `OwidTable.value_column`). Header check is case-insensitive. Rows with an empty value are dropped.
- Types: `OwidRow(entity, code | None, year, value)`; `OwidTable(slug, title, value_column, rows,
  matched_countries, notes)`; `FetchOwidInput(topic, since_year=2000, options: PostOptions | None)`.

### Post-fetch filtering (`post_options.filter_table_rows`)

Runs inside `fetch_owid_dataset`, so the workflow only sees the filtered table.

| Option | Effect |
| --- | --- |
| none | rows with `year >= since_year` (default 2000) |
| `time_preset` `last10/20/50` | years from `latest - N + 1`, where latest is the dataset's latest year (not today) |
| `time_preset` `all` | no lower bound |
| `from_year` / `to_year` | inclusive bounds |
| range gives no rows | falls back to `since_year` and adds a note |
| `countries` | match by entity name or ISO code (case-insensitive); keeps matched entities only |

Unmatched countries add notes (with a `difflib` "Did you mean ...?" at cutoff 0.8). If none match,
a note says the chart picks its own. `matched_countries` and `notes` travel in `OwidTable`; notes
reach the run page via `ReviewState.option_notes`.

### Boundary types (where defined)

| Type | Module | Crosses |
| --- | --- | --- |
| `DailyChartInput`, `ReviewState`, `ApprovalDecision`, `DailyChartResult` | `daily_chart.py` | services <-> workflow |
| `HNTopStoriesInput/Output`, `HNStory` | `hn.py` | workflow <-> activity |
| `PickTopicInput/Output` | `topic.py` | workflow <-> activity |
| `FetchOwidInput`, `OwidTable`, `OwidRow` | `owid.py` | workflow <-> activity |
| `WriteChartSpecInput`, `ChartSpec` | `chart_spec.py` | workflow <-> activity |
| `CritiqueChartInput`, `ChartCritique` | `critique.py` | workflow <-> activity |
| `RenderChartInput/Output` | `render.py` | workflow <-> activity |
| `PostChartInput/Output` | `bluesky.py` | workflow <-> activity |
| `ChartStyle`, `PostOptions` | `style.py`, `post_options.py` | form/store -> workflow input |

### Fixtures and the no-live-calls rule

Tests never call HN, OWID, Anthropic or Bluesky. They use recorded responses in `tests/fixtures/`
(`hn_top_stories.json`, `owid_life_expectancy.csv`, `llm_pick_topic_*.json`,
`llm_chart_spec_response.json`, `llm_critique_*.json`, `bluesky_*.json`,
`vega_lite_line_chart.json`) served through `httpx.MockTransport`. To record a new fixture, save a
real response once by hand, strip anything private, and commit it.

## 2. Charts

### Flow inside the workflow

```
 OwidTable ─▶ write_chart_spec ─▶ critique_chart ─pass─▶ render_chart_png ─▶ PNG
                  ▲                    │ fail
                  └── feedback ◀───────┘   (one regenerate, then render whatever came back)
```

### Spec generation (`chart_spec.py`)

- `ChartSpecActivities.write_chart_spec(WriteChartSpecInput)` calls Claude with the tool
  `write_chart` (schema from `ChartDraft`: `entities` 1 to 8, `vega_lite_spec`, `caption` up to 280
  chars). The prompt carries the topic, table title, entity list, year range and the first 20 rows.
  The model sees a summary, not the full data.
- The model must use only the fields `entity`, `year`, `value` and must not include data. The code
  strips any `data`/`datasets` key and injects the real rows for the chosen entities as
  `data.values`.
- Validation (all non-retryable `InvalidChartDraft`): invalid draft, caption over budget,
  entities not in the table, empty table. This error type triggers the regenerate path.
- If `table.matched_countries` is set, entities are forced to those countries, overriding the
  model's choice.
- Output `ChartSpec(vega_lite_spec, caption, entities)`.

### Style and per-post options

Applied in two places, in this order inside `write_chart_spec`:

| What | Where applied | How |
| --- | --- | --- |
| Tone | In the prompt (`style_prompt`, `TONES`) | the model writes the caption in that tone |
| Chart type (line, bar, area) | In code (`apply_chart_type`) | rewrites marks and encoding; keeps `$schema`, title, size |
| Country forcing | In code (and filter in `owid.py`) | entities fixed to `matched_countries` |
| Palette and font | In code (`apply_style_to_spec`) | sets `config.range.category`, `config.mark.color`, `config.font` |
| Hashtags and signature | In code (`caption_suffix`) | appended to the caption after a blank line |

Because a suffix is appended, the model gets a caption budget of `280 - len(suffix) - 2`
(`_caption_budget`) and a longer caption is rejected, so the final text stays within Bluesky's
300 characters. Palettes (`PALETTES`: classic, ocean, sunset, okabe-ito, ink) and fonts
(`FONTS`: sans, serif, mono) are fixed allow-lists in `style.py`; `ChartStyle` validators reject
anything else (max 3 hashtags, signature up to 40 chars).

Fonts: vl-convert bundles only Liberation Sans and the slim image has no system fonts, so the
Dockerfile installs `fonts-liberation`; serif and mono stacks end with Liberation Sans so text
always renders.

#### Merge precedence

per-post option > stored profile > default. Only `tone` can be overridden per post
(`merge_style` copies the profile, or a default `ChartStyle()`, with the option's tone). The
profile comes from `StyleStore.get(requested_by)`; the default is `ChartStyle()` (classic, sans,
neutral, no hashtags). With no profile and no tone option, `style` is `None` and the spec is left
as the model wrote it.

#### Free-text instructions and prompt injection

`PostOptions.instructions` (max 300 chars, whitespace collapsed, printable only) is the only
free text that reaches the prompt. `options_prompt` wraps it in `<user_instructions>` tags, replaces
`<` and `>` with look-alikes (`_neutralise`), and tells the model to treat it as untrusted wishes
about wording and emphasis only. Everything else (type, years, countries, palette, font) is
validated against allow-lists or applied by code, so the model never decides it.

### Rendering (`render.py`)

`render_chart_png(RenderChartInput)` runs `vl_convert.vegalite_to_png` (scale 2.0) in a thread,
writes `chart-<first 16 hex of spec sha256>.png` into `output_dir` via a temp file and
`os.replace`, and returns `RenderChartOutput(path, spec_sha256, size_bytes)`. Same spec gives the
same file, so reruns are safe. A spec that cannot render raises non-retryable `InvalidVegaLiteSpec`.

### Critique and loop (`critique.py`, `chart_loop.py`)

- `critique_chart` sends the spec (without data), caption and a per-entity data summary (first,
  last, min, max). Output `ChartCritique(passed, reasons<=5)`; a failure must carry reasons.
- `draft_chart_with_critique` (called from the workflow) writes, critiques, and if the draft is
  invalid or the critique fails, writes once more with `feedback` and critiques again.
  `MAX_DRAFTS = 2`; `CritiquedChart.attempts` is 1 or 2. The second result is used even if its
  critique fails; the human sees `critique.passed` and reasons on the run page.
- `DraftChartWorkflow` in `chart_loop.py` wraps the helper for tests; the worker does not
  register it.


## 3. Approval and posting

What a person sees and decides on the run page (`web/templates/run.html`, built from `RunView`):

- Status badge: Drafting, Needs review, Decision sent, Posting, Posted, Rejected, Expired.
- Topic and why it was chosen (the LLM's reason, or "Manually chosen"), the chart PNG, the caption,
  the critique result (passed, or the reasons it failed) and how many drafts were made.
- The options used for the run and any notes (for example a country that had no data).
- Who requested the run and who decided it, and a link to the run in the Temporal UI.
- Approve and Reject buttons, only while the status is `awaiting_decision`.

Rules:

- The first decision wins. A second decision is ignored by the workflow; the web and CLI refuse it
  up front with "status is ..." (HTTP 409 on the web).
- No decision within 20 hours (`APPROVAL_TIMEOUT`) ends the run as `expired`; nothing is posted.
- Reject ends the run `rejected`; nothing is posted. An optional note (max 500 chars) is stored.
- Approve sets status `posting`, posts to Bluesky and ends `approved`. The post is a single image
  post whose text is the caption (hashtags and signature included) and whose alt text is
  `Chart: <topic>. <caption>` cut to 1000 characters. Posting is idempotent (see tech.md).
- Requester is recorded as `requested_by` (the signed-in email; `dev@localhost` in local mode; the
  CLI records none). Approver is `decided_by` in the decision.
- Daily cap: each signed-in person may start `DAILY_CHART_CAP` runs per UTC day (default 10). Every
  started run counts, whatever its outcome. The count resets at 00:00 UTC. CLI runs are never
  capped and not counted. Over the cap the web answers 429 with "You've reached today's limit".
- Anyone allowed through Cloudflare Access can approve any run; there is no per-run ownership check.
- The web page always generates a new run id (`chart-<date>-<8 hex>`); the CLI default is
  `daily-chart-<date>`, so a second CLI start on the same day without `--id` is refused
  (`RunAlreadyExists`).

## 4. Known limitations

- Topics are limited to the six catalog datasets; a typed topic outside it fails `NoMatchingDataset`.
- The critique cannot tell a requested bar chart from a bad one.
- Time presets are relative to the dataset's latest year, not today.
- The model sees only a sample of rows, so captions are checked against summaries.
- The PNG must stay under 1,000,000 bytes to be posted (`ImageTooLarge`).
- Nothing starts a run on a schedule; runs are started by a person.
- No refine loop, runs list, topic suggestions or timeline (planned, not built; see `docs/history.md`).
