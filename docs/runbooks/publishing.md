# Publishing runbook

Publishing a story is a separate, third human click. Candidate approval starts story generation,
final approval marks the package Ready, and only **Publish to Bluesky** posts. Nothing before that
click ever reaches Bluesky. This runbook covers setup, media, the publish flow, recovery from a
failed publication, retention and a manual visual check.

Run every command from the repo root.

```sh
dev-temporal()  { docker compose -f infra/compose.dev.yaml exec temporal temporal "$@"; }
prod-temporal() { env -i PATH="$PATH" HOME="$HOME" docker compose -f infra/compose.prod.yaml \
  --env-file .env.prod exec temporal temporal "$@"; }
```

In the prod stack, pass `--address temporal:7233` after `temporal` when the CLI cannot reach the
server (the Temporal port is not published to the host). Replace `<id>` in the commands below with
the publication ID shown in the sections that follow.

## Configure Bluesky

The worker is the only process that talks to Bluesky. The web app never sees the credentials.

1. Sign in to the account that should post. Use a **test** account until you are happy with the flow.
2. Open Settings, then Privacy and security, then App passwords. Create an app password named for
   this project. Never use the account password.
3. Put the three keys in `.env` (dev) or `.env.prod` (prod):

   ```sh
   BLUESKY_HANDLE=your-handle.example.com
   BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
   BLUESKY_SERVICE=https://bsky.social
   ```

   `BLUESKY_SERVICE` is optional and defaults to `https://bsky.social`. Use your own PDS URL only if
   the account lives there.
4. Restart the worker (`just dev` in dev, `just deploy` in prod). The worker will not start without
   LLM and Bluesky credentials.
5. Check the account: publish one Ready story from a test account and open the link. A
   `DestinationAuthFailed` failure (shown as "Bluesky rejected the account credentials.") means the
   handle or app password is wrong.
6. Rotate the password: create a new app password, update the env file, restart the worker, then
   delete the old app password in Bluesky. A publication that is already `publishing` finishes or
   fails on its own; press Try again afterwards if it failed.

## Media and providers

- `MEDIA_DIR` (default `media`) holds every artifact as `<sha256>.<ext>`. The file name is its own
  checksum, so **never edit, rename or replace an artifact**. Restore it from a backup instead.
- Prod mounts the `media` volume in both worker and web (`/data/media`). The worker writes and
  publishes from it; the web reads it to show the review card.
- `MEDIA_PROVIDER` is `story-card` (default, the real renderer) or `fake` (tests and local dry runs
  only). Never use `fake` in prod.
- Bluesky has no animation here, so a GIF story publishes its still card. A text-only story
  publishes text with the source link and no image.
- The publish step re-checks the hash of the approved image file. If the file is missing or changed
  it fails with `PublicationMediaMissing` or `ArtifactHashMismatch` before anything is posted.

## Publish a story

1. Approve the candidate, wait for the story to reach Review, read the **Bluesky post** proof and
   the story card, then approve the final story. The story is now **Ready**.
2. Open the story. The rail says "Approved" and offers **Publish to Bluesky**. Nothing is posted
   until you press it.
3. After the press the badge stays **Ready**. The line beside it and the rail say "Checking the
   approved story." then "Posting to Bluesky.", the button is disabled and the page refreshes by
   itself.
4. On success the badge becomes **Published** with "Published to Bluesky <time>" and a **View the
   post on Bluesky** link. On failure the rail says **Not published**, one fixed sentence and a hint.
   The badge stays Ready.

Double-click safety: the publication ID is `publish-v1-<story_id>-bluesky-<content_hash>`. It is both
the workflow ID and the idempotency key, and the post uses a fixed record key. A second click, a
second tab or a retry while a run is active returns the existing run and starts nothing. If a run
already finished `published`, it returns that result.

The page shows the post text exactly as it will be sent. If the text is over 300 characters, review
shows the problem and Ready shows no Publish button. Package problems cannot be fixed after
approval; reject the story instead.

## Recover a failed publication

Every failure shows one fixed sentence in the UI. Raw errors, tokens and post text never appear
there. Find the cause with the table, fix it, then press **Try again** when the page offers it.

| Code | UI sentence | Cause | Operator action |
| --- | --- | --- | --- |
| `ContentHashMismatch` | The story changed after it was approved. | The package does not match the approved hash | None. Package problem; reject the story |
| `ManifestMismatch` | The story card does not match the approved story. | The media manifest belongs to another text | None. Package problem; reject the story |
| `PostTextTooLong` | The post text is longer than Bluesky allows. | Text plus the source line is over 300 characters | None. Package problem; reject the story |
| `AltTextTooLong` | The image description is longer than Bluesky allows. | Alt text over the limit | None. Package problem; reject the story |
| `MediaNotSupported` | Bluesky does not support the approved image. | Image type not accepted | None. Package problem; reject the story |
| `MediaTooLarge` | The approved image is larger than Bluesky allows. | Image over the size limit | None. Package problem; reject the story |
| `PublicationMediaMissing` | The approved image file is missing. | The artifact is not in `MEDIA_DIR` | Restore the file from backup or the shared volume, then Try again |
| `ArtifactHashMismatch` | The approved image file was changed. | The file does not match its checksum | Restore the original file, then Try again |
| `DestinationAuthFailed` | Bluesky rejected the account credentials. | Wrong handle or app password | Fix `BLUESKY_*` on the worker, restart it, then Try again |
| `DestinationRateLimited` | Bluesky is limiting posts. Try again later. | Too many requests | Wait a few minutes, then Try again |
| `DestinationUnavailable` | Bluesky could not be reached. Try again. | Network or Bluesky outage | Check connectivity, then Try again |
| `DestinationRejected` | Bluesky rejected the post. | Bluesky refused the record | Check the worker log and the account state, then Try again |
| `UnexpectedDestinationResponse` | Bluesky sent an unexpected response. Try again. | Unrecognised answer from Bluesky | Try again; if it repeats, check the worker log |

Try again appears for every code except the six package problems. Failures from a run that stopped
early (no code) show "Publishing stopped before it finished. Try again." and the generic hint
"Try again."

Inspect one publication (the ID is `publish-v1-<story_id>-bluesky-<content_hash>`):

```sh
dev-temporal workflow describe --workflow-id publish-v1-<id>
dev-temporal workflow query --workflow-id publish-v1-<id> --type publication_state
dev-temporal workflow list --query 'WorkflowType = "StoryPublishingWorkflow" AND StoryStatus = "publish_failed"'
```

Use `prod-temporal` in prod. The query result holds the status, the fixed detail, the error code and,
once published, the post URL. It never holds post text or credentials.

Stuck in `publishing`: if the sentence does not change for several minutes and the worker is
healthy, terminate the run, then press Try again.

```sh
dev-temporal workflow terminate --workflow-id publish-v1-<id> --reason "stuck publishing"
```

This is safe. The post uses a fixed record key, so a second run finds the existing post and returns
it instead of posting twice.

Never create the post by hand. A hand-made post has no matching record key, so the next Try again
could post a duplicate. Package problems (for example, text too long) can't be fixed after approval;
reject the story and start again from a new candidate.

## Retention

Stories, including Published ones, leave the UI 48 hours after the **original detection time**.
Publishing does not reset the clock, because the publishing run inherits `StoryCreatedAt`. Past
that point `/stories/{id}` answers 410 and the row leaves every filter. The workflow histories are
never deleted and can still be described with the commands above.

## Visual check (manual)

The build environment could not start a headless browser (the system library `libnspr4` is
missing), so no screenshots were taken for this feature. Markup and CSS assertions in
`tests/test_design_tokens.py` and `tests/test_web_story_publish.py` stand in for them. A person
must run this checklist in a real browser, in light and dark themes, before merging.

- [ ] No horizontal scroll at 360px on `/stories` and on a story in review, Ready, publishing,
  failed and Published states.
- [ ] The rail stacks under the reading column below 48rem.
- [ ] The post text wraps a long URL host instead of overflowing the proof.
- [ ] The disabled "Publishing..." button is visibly dashed.
- [ ] The focus ring shows on Publish to Bluesky, Try again and View the post on Bluesky.
- [ ] The dark theme keeps ink-on-violet (Published badge) and ink-on-pink (failure and over-limit
  fills) legible.
- [ ] Reduced motion shows the still card, with the proof below it.
- [ ] The post proof ruler fills in proportion to the count and turns pink when over the limit.
