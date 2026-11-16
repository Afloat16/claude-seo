# Google Cloud Natural Language API Reference

Use NLP analysis as an internal diagnostic for entity coverage, content sentiment, and topic classification. Do not present it as Google's E-E-A-T score, a ranking factor, or a proxy for numeric E-E-A-T weights.

## Endpoint

Entities use `POST https://language.googleapis.com/v1/documents:analyzeEntities`.
Sentiment, classification, and moderation use
`POST https://language.googleapis.com/v2/documents:annotateText`.

Send the API key in the `X-Goog-Api-Key` header, not in the URL.

## Language

`nlp_analyze.py` sends no language by default, so the API detects it and the
JSON `language` field reports the detected code. Pass `--language <code>`
(BCP-47, e.g. `en`, `pt-BR`, `zh-Hant`) to override detection. The v1 entity
call takes the code as `document.language`, the v2 call as
`document.languageCode`. Language support differs per feature, so an
unsupported language returns an API error for that feature.

## Analysis coverage

`nlp_analyze.py` keeps a local sampling cap of 100,000 characters per request.
The JSON `text_length` is the full input length, `analysis_text_length` is the
length of the request document, and `text_truncated` says whether only a
prefix was selected. URL analysis also reports `extracted_text_length`.

When `text_truncated` is true, describe the entity, sentiment and category
findings as applying to the sampled prefix. Do not infer that an entity absent
from the response is absent from the entire page; it may occur in the omitted
tail. Select the relevant passage for a further analysis when needed.

This sampling cap is distinct from the API's
[content quota](https://docs.cloud.google.com/natural-language/quotas), which
is measured in bytes. The API also has token and entity-mention limits, so
request length alone does not guarantee that every token was processed.

## Features

| Feature | What It Does | SEO Use |
|---------|-------------|---------|
| `extractEntities` | Extract people, orgs, places, events with salience scores | Topic coverage depth, entity optimization |
| `extractDocumentSentiment` | Document + sentence-level sentiment score/magnitude | Content tone assessment |
| `classifyText` | Map content to 700+ Google categories | Topic relevance verification |
| `moderateText` | Content safety/moderation categories | Content quality flags |

## Entity Types

PERSON, LOCATION, ORGANIZATION, EVENT, WORK_OF_ART, CONSUMER_GOOD, OTHER, PHONE_NUMBER, ADDRESS, DATE, NUMBER, PRICE

Each entity includes:
- `name`: Entity text
- `type`: Entity type
- `salience`: Importance score (0-1, higher = more relevant)
- `sentiment`: Per-entity sentiment (score + magnitude)
- `metadata`: Wikipedia URL, MID (Knowledge Graph ID)
- `mentions`: Occurrences in the text

## Sentiment Scoring

- **Score**: -1.0 (negative) to +1.0 (positive)
- **Magnitude**: 0 to infinity (emotional intensity, higher = more emotional)
- Neutral content: score ~0, low magnitude
- Mixed content: score ~0, HIGH magnitude (both positive and negative)

## Pricing

| Feature | Free/month | Paid (per 1K chars) |
|---------|-----------|-------------------|
| Entity Analysis | 5,000 units | $0.001 |
| Sentiment Analysis | 5,000 units | $0.001 |
| Content Classification | 30,000 units | $0.002 |
| Text Moderation | 50,000 units | $0.0005 |

One "unit" = 1,000 characters. Free tier resets monthly.

## Enable the API

1. Go to [console.cloud.google.com/apis/library](https://console.cloud.google.com/apis/library)
2. Search for "Cloud Natural Language API"
3. Click Enable
4. **Billing must be enabled** on the project (free tier still applies)

Uses the same API key as PSI/CrUX.
