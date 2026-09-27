# Attribution

Sieve is a portfolio project. It does not own the data it reads. This
file records where the data comes from and under which terms it may be
reused, because getting this wrong is a legal problem, not a
documentation detail.

## Demo data (`data/samples/`)

**Synthetic. Not real people.**

`lote_demo.json` was written by hand for this project. The names are
fictional and the messages do not correspond to any real person,
account, or conversation. It exists so the pipeline can be demoed and
tested without touching anyone's data.

## Stack Exchange (Stack Overflow in Spanish)

Live data is read from the Stack Exchange API 2.3, site
`es.stackoverflow`, via the `GET /questions` endpoint.

- **License:** Stack Exchange content is published under a Creative
  Commons Attribution-ShareAlike license. The exact version varies by
  post: the API returns it per item in the `content_license` field, and
  Sieve stores that value rather than assuming one. In practice,
  Spanish Stack Overflow posts have been observed returning
  **`CC BY-SA 3.0`**. Always read the license from
  `GroundTruth.content_license` for the specific item you are reusing
  rather than trusting a single global constant.
- **Terms of use:** <https://api.stackexchange.com/docs/terms-of-use>
- **Attribution requirement:** the author's display name, a link to
  their profile, and a link to the original post must accompany any
  reused content. Sieve keeps all three: `GroundTruth.author_url`,
  `GroundTruth.url`, and `GroundTruth.content_license`.
- **Rate limit:** 300 requests per day per IP, advertised via the
  `quota_remaining` response header. Sieve is cache-first and never
  downloads the site dump.

If any generated asset quotes or paraphrases a Stack Exchange question,
it must carry the author, the source link, and the license. The
`fuentes` field on every output model exists for this purpose.

## What Sieve deliberately does not use

- **Discord:** no public dumps, no scraped history. Real chat logs can
  contain personal data that the platform's terms never authorized for
  redistribution.
- **Slack:** same reason. Exporting a workspace and republishing
  conversations is not a free action.
- **Reddit:** the API is restricted and the content license is
  ambiguous. Not worth the risk for a portfolio project.
- **X / LinkedIn:** both prohibit scraping.
- **Any LLM prompt or output:** model terms differ per provider, and
  prompts can contain user data. Adapters live in the `llm` extra and
  must be configured explicitly.

## The line

Data in this repository is either synthetic or publicly licensed with
attribution preserved. If a future source cannot satisfy that, it does
not go in.
