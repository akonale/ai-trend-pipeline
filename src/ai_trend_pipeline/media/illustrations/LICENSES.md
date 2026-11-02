# Illustration licences

The three `person-*` and `reading-*` drawings in `assets/` come from **Open Doodles** by Pablo Stanley, downloaded from the
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

All Open Doodles drawings are modified: metadata removed, style attributes inlined, colours
flattened, path numbers rounded, viewBox cropped to content (see `modification_note`). At render
time two colours are swapped for the site tokens. The "retrieved" date is 2026-11-02 for all of
them.

| Asset | Author | Source page | Licence | Verified | SHA-256 of the committed file |
| --- | --- | --- | --- | --- | --- |
| `person-reading` | Pablo Stanley | https://www.opendoodles.com/ | CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/) | 2026-11-02 | `44007e5dd9eefff3a3cbb8c257da688e34088d3bb32ea73cc442d099b76d66d3` |
| `reading-with-books` | Pablo Stanley | https://www.opendoodles.com/ | CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/) | 2026-11-02 | `ff8c07831e47d1a10b1b649f2d4f5ba00dfd133140a20990279b48c8df7a0e59` |
| `person-meditating` | Pablo Stanley | https://www.opendoodles.com/ | CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/) | 2026-11-02 | `13d597f1033fc166edbe5c30428c86a03172c6a7752bcaa828fc8b8b994b8998` |

## Project-original glyphs

| Glyph | Author | Topic | SHA-256 of the committed file |
| --- | --- | --- | --- |
| `glyph-robot` | this project | `ai` | `b9d39ac1ddfac518ef5585a5684552b4ccf8ee7b154a466b0cf7484d9c4bfc1b` |
| `glyph-padlock` | this project | `security` | `6b5813c64341ce08428002abf4dcd409356010bc72febe93e1b810835d72f970` |
| `glyph-chip` | this project | `chips` | `de3b78c904cf0e4740554fd1320e04cebb68c3416bb17b759e7f7c7cf8c8a638` |
| `glyph-terminal` | this project | `code` | `59ed125a9263a068f2449d30f3309b0d4722bdd8a48fd367aff2360e5ed639e1` |
| `glyph-globe` | this project | `internet` | `649c734c2c0f088c5708737cee9960fe4ad31863024564b268026a111dd921c0` |
| `glyph-bolt-panel` | this project | `energy` | `198e81d753178167ce01f3eb4ed314279bdc12b802402766085c82e048a0e783` |
| `glyph-electric-car` | this project | `electric-cars` | `73c6a46839d0ef45189dabc8c82d11141692b6e14ae034a888a756fec9fa93c3` |
| `glyph-leaf-sun` | this project | `climate` | `46fd23c9410b92fc601cb96eae94c566161935d0a41e0a87c50f478a49a92aa8` |
| `glyph-coins-chart` | this project | `economy` | `0e8259605cd659597f8e757a71872a7d47ef15327f3626450608ee39e2a62857` |
| `glyph-rocket` | this project | `space` | `ca5043c235ba3efc11542f816f9c72f279a61528efa572c1a6db9def2cfde91a` |
| `glyph-flask` | this project | `science` | `8346342e9c12ce6ef45ed3e8e90542ab22b1633b73f1ccc528da08655a641d5d` |
| `glyph-fruit-bowl` | this project | `food` | `518f928d07bbc4ea1f5604cd53f87ae2221ab22fb3f9cd893f720ebb4eaa98a6` |

These glyphs are original work created for this project. The project owner dedicates them to the public domain under CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/). No attribution is required.

## Deferred source

Openclipart is **deferred** to a later task. On 2026-11-02 its site served no clipart (empty
search, 500 on detail pages, downloads redirecting to a logo), so its source and licence could not
be verified. Its FAQ (https://openclipart.org/share) says all clipart is released into the public
domain under the Creative Commons Zero 1.0 licence, but nothing is vendored from it. Adding it
needs a new `source` and `licence` enum value in `catalogue.py`.
