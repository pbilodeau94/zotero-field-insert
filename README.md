# zotellm

One-command citation formatting: takes any document (.md or .docx) with informal citations, resolves them via CrossRef/PubMed, and produces a Word document with live Zotero field codes.

## Quick Start

```bash
pip install python-docx requests

# From markdown
python zotellm.py paper.md --provider cli --zotero-db ~/Zotero/zotero.sqlite

# From Word document
python zotellm.py paper.docx --provider cli --zotero-db ~/Zotero/zotero.sqlite

# Output: paper_zotero.docx → open in Word → Zotero > Refresh
```

## Citation Format

Write citations however is natural. Include **author, journal, and year** for best results:

| Format | Match rate | Example |
|---|---|---|
| Author, Journal, Year | ~95% | `(Cryan et al., Nature Reviews Neuroscience, 2019)` |
| PMID | ~99% | `(PMID: 30578015)` |
| DOI | 100% | `(doi: 10.1016/S1474-4422(22)00431-8)` |
| Author, Year | ~75% | `(Cryan et al., 2019)` |
| Author only | ~50% | `(Cryan et al.)` |

Any style works — parenthetical, narrative, numbered references, bare mentions. The LLM identifies them all and uses surrounding context to disambiguate.

## How It Works

1. Extracts text from your `.md` or `.docx`
2. LLM identifies all citations and extracts author/year/journal/PMID/DOI
3. Resolves full metadata via DOI, PMID → PubMed, or CrossRef search
4. Optionally looks up items in your Zotero library (local SQLite) or adds new ones (Web API)
5. Replaces informal citations with Zotero field codes in the Word document
6. Output: `.docx` ready to open in Word and click Zotero > Refresh

## LLM Providers

No vendor lock-in — works with any LLM:

```bash
# CLI tool (no API key needed) — auto-detects claude, ollama, or llm
python zotellm.py paper.md --provider cli

# Custom CLI command
python zotellm.py paper.md --provider cli --cli-command "ollama run llama3"

# OpenAI API (or any OpenAI-compatible endpoint)
export OPENAI_API_KEY=sk-...
python zotellm.py paper.md --provider openai

# Anthropic API
export ANTHROPIC_API_KEY=sk-ant-...
python zotellm.py paper.md --provider anthropic

# Local model via OpenAI-compatible API (ollama, vLLM, LM Studio)
python zotellm.py paper.md --api-base http://localhost:11434/v1 --model llama3

# Third-party APIs (Together, Groq, etc.)
python zotellm.py paper.md --api-base https://api.together.xyz/v1 --model meta-llama/Llama-3-70b
```

## Arguments

| Argument | Required | Description |
|---|---|---|
| `input` | Yes | Input file (`.md` or `.docx`) with informal citations |
| `--output`, `-o` | No | Output `.docx` path (default: `input_zotero.docx`) |
| `--provider`, `-p` | No | `openai` (default), `anthropic`, or `cli` |
| `--model`, `-m` | No | Model name (default depends on provider) |
| `--api-base` | No | API base URL for custom endpoints |
| `--api-key` | No | API key (overrides env var) |
| `--cli-command` | No | Custom CLI command for `--provider cli` |
| `--zotero-db` | No | Path to local `zotero.sqlite` (for key lookups) |
| `--zotero-api-key` | No | Zotero Web API key (for adding items to library) |
| `--zotero-library-id` | No | Zotero user library ID |
| `--reference-doc` | No | Pandoc reference `.docx` template (for `.md` input) |
| `--font` | No | Font for citation text (default: Calibri) |
| `--size` | No | Font size in pt (default: 11) |
| `--bib-heading` | No | Heading for bibliography location (default: References) |
| `--no-crossref` | No | Skip CrossRef/PubMed lookups |
| `--dry-run` | No | Preview without writing files |

## Standalone Tools

The unified `zotellm.py` is the recommended entry point. The individual tools are also available for advanced use:

- **`llm_reference_formatter.py`** — LLM citation extraction + CrossRef resolution → outputs pandoc markdown + CSL JSON
- **`zotero_field_insert.py`** — Inserts Zotero field codes into a `.docx` with `[@citekey]` markers

## Getting a Zotero API Key

Only needed if you want to add new items to your Zotero library automatically:

1. Go to https://www.zotero.org/settings/keys
2. Create a new key with read/write access to your library
3. Your library ID is visible in the URL when viewing your library on zotero.org

## How Zotero Field Codes Work

A Zotero citation in Word is a "complex field" — 5 XML runs:

1. `<w:fldChar fldCharType="begin"/>` — field start
2. `<w:instrText>ADDIN ZOTERO_ITEM CSL_CITATION {...}</w:instrText>` — JSON payload with full CSL metadata
3. `<w:fldChar fldCharType="separate"/>` — separator
4. `<w:t>1</w:t>` — placeholder text (Zotero replaces on refresh)
5. `<w:fldChar fldCharType="end"/>` — field end

The bibliography uses `ADDIN ZOTERO_BIBL` with the same structure.

## Limitations

- Citation text is a placeholder until you click Refresh in the Zotero Word plugin
- Requires Word (not Google Docs or LibreOffice) with the Zotero plugin installed
- Multi-key citations like `[@key1; @key2]` are not yet supported

## License

MIT
