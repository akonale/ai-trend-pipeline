# Illustration licences

Every drawing in `assets/` comes from **Open Doodles** by Pablo Stanley, downloaded from the
official site (https://www.opendoodles.com/) on 2026-11-02. The licence was read on the official
site on the same day (`licence_verified_on` in `catalogue.json`). No attribution is needed in
posts.

## Statements, as read on 2026-11-02

From the Open Doodles home page (https://www.opendoodles.com/):

> Free for Commercial and Personal Use. No need to credit

The page also carries a "CC0 Public Domain" meta tag.

From the Open Doodles about page (https://www.opendoodles.com/about):

> You can copy, edit, remix, share, or redraw these images for any purpose without restriction under copyright or database law (CC0 license.)

Legal code: CC0 1.0 Universal, https://creativecommons.org/publicdomain/zero/1.0/

## Assets

All assets are modified: metadata removed, style attributes inlined, colours flattened, path
numbers rounded (see `modification_note`). At render time two colours are swapped for the site
tokens. The "retrieved" date is 2026-11-02 for all of them.

| Asset | Author | Source page | Licence | Verified | SHA-256 of the committed file |
| --- | --- | --- | --- | --- | --- |
| `person-reading` | Pablo Stanley | https://www.opendoodles.com/ | CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/) | 2026-11-02 | `0471773eee79b1f99c5083582f14fa9cc824f5949a1ed6333ec2887015c3a19e` |
| `reading-with-books` | Pablo Stanley | https://www.opendoodles.com/ | CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/) | 2026-11-02 | `f42bfe709904957307e2d4d9e7934654859603415df125ec9460c849f2018761` |
| `person-meditating` | Pablo Stanley | https://www.opendoodles.com/ | CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/) | 2026-11-02 | `252a9add1baa2dfe464a0103e9c2d6e8be830b11dc8a7ef7cd4f643f9ca3b534` |

## Deferred source

Openclipart is **deferred** to a later task. On 2026-11-02 its site served no clipart (empty
search, 500 on detail pages, downloads redirecting to a logo), so its source and licence could not
be verified. Its FAQ (https://openclipart.org/share) says all clipart is released into the public
domain under the Creative Commons Zero 1.0 licence, but nothing is vendored from it. Adding it
needs a new `source` and `licence` enum value in `catalogue.py`.
