# zotellm

LLM-powered citation formatting and Zotero field code insertion for Word documents.

## Problem

When you generate Word documents from Markdown (via pandoc), LaTeX, or Python, there is no way to include live Zotero citations. Pandoc's `--citeproc` produces plain text references that the Zotero Word plugin cannot recognize or manage. This toolkit bridges that gap.

## Tools

### 1. `llm_reference_formatter.py` — LLM-powered citation formatting

Uses any LLM (OpenAI, Anthropic, ollama, Claude CLI, or any OpenAI-compatible API) to identify informal citations in a markdown document (e.g., "Banwell et al., 2023"), looks up full metadata via CrossRef, optionally adds items to your Zotero library, and outputs pandoc-ready markdown with `[@citekey]` markers plus a CSL JSON bibliography.

### 2. `zotero_field_insert.py` — Insert Zotero field codes

Finds `[@citationkey]` markers in a `.docx` file and replaces each with proper `ADDIN ZOTERO_ITEM CSL_CITATION` Word field codes. Also inserts an `ADDIN ZOTERO_BIBL` field for the bibliography.

## Installation

```bash
pip install python-docx requests
```

No other dependencies required. The LLM formatter uses raw HTTP requests for API calls, or can shell out to any CLI tool (claude, ollama, llm).

## Full Pipeline

```bash
# 1. Write your document with informal citations
#    "MOGAD is diagnosed using criteria (Banwell et al., 2023)"

# 2. Format citations with the LLM tool
python llm_reference_formatter.py paper.md -o paper_cited.md \
    --zotero-db ~/Zotero/zotero.sqlite

# 3. Convert to docx with pandoc (no --citeproc)
pandoc paper_cited.md -o paper.docx --reference-doc=template.docx

# 4. Insert Zotero field codes
python zotero_field_insert.py paper.docx \
    --bib references.json \
    --keymap keymap.json \
    --zotero-db ~/Zotero/zotero.sqlite

# 5. Open paper.docx in Word, click Zotero > Refresh
```

## LLM Reference Formatter

### LLM Providers

Works with any LLM — no vendor lock-in:

```bash
# CLI tool (no API key needed) — auto-detects claude, ollama, or llm
python llm_reference_formatter.py paper.md -o paper_cited.md --provider cli

# Custom CLI command
python llm_reference_formatter.py paper.md --provider cli --cli-command "ollama run llama3"

# OpenAI API (or any OpenAI-compatible endpoint)
export OPENAI_API_KEY=sk-...
python llm_reference_formatter.py paper.md --provider openai

# Anthropic API
export ANTHROPIC_API_KEY=sk-ant-...
python llm_reference_formatter.py paper.md --provider anthropic

# Local model via OpenAI-compatible API (ollama, vLLM, LM Studio)
python llm_reference_formatter.py paper.md \
    --api-base http://localhost:11434/v1 --model llama3

# Third-party APIs (Together, Groq, etc.)
export OPENAI_API_KEY=your-key
python llm_reference_formatter.py paper.md \
    --api-base https://api.together.xyz/v1 --model meta-llama/Llama-3-70b
```

### What it does

1. Reads a markdown document with informal citations (e.g., "Banwell et al., 2023", "(Smith 2020)")
2. Uses an LLM to identify every citation and extract author/year/title metadata
3. Searches CrossRef for full bibliographic metadata (DOI, journal, volume, pages)
4. Optionally looks up items in your local Zotero database or adds new ones via the Zotero Web API
5. Rewrites the document with `[@citekey]` pandoc citation markers
6. Outputs `references.json` (CSL JSON) and `keymap.json` for `zotero_field_insert.py`

### Arguments

| Argument | Required | Description |
|---|---|---|
| `input` | Yes | Input markdown file with informal citations |
| `--output`, `-o` | No | Output markdown path (default: `input_cited.md`) |
| `--provider`, `-p` | No | `openai` (default), `anthropic`, or `cli` |
| `--model`, `-m` | No | Model name (default: gpt-4o / claude-sonnet-4-20250514) |
| `--api-base` | No | API base URL for custom endpoints |
| `--api-key` | No | API key (overrides env var) |
| `--cli-command` | No | Custom CLI command for `--provider cli` |
| `--bib-output` | No | CSL JSON output path (default: `references.json`) |
| `--keymap-output` | No | Keymap output path (default: `keymap.json`) |
| `--zotero-api-key` | No | Zotero Web API key (for adding items to library) |
| `--zotero-library-id` | No | Zotero user library ID |
| `--zotero-db` | No | Path to local `zotero.sqlite` (for key lookups) |
| `--no-crossref` | No | Skip CrossRef lookups |
| `--dry-run` | No | Preview without writing files |

## Zotero Field Insert

### Usage

```bash
# Basic (standalone bibliography)
python zotero_field_insert.py document.docx --bib references.json

# With Zotero library linking
python zotero_field_insert.py document.docx \
    --bib references.json \
    --keymap keymap.json \
    --zotero-db ~/Zotero/zotero.sqlite \
    --output document_with_zotero.docx
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `input` | Yes | Input `.docx` file containing `[@key]` citation markers |
| `--bib`, `-b` | Yes | CSL JSON bibliography file |
| `--output`, `-o` | No | Output path (default: overwrites input) |
| `--keymap`, `-k` | No | JSON mapping citation keys to Zotero item keys |
| `--zotero-db` | No | Path to `zotero.sqlite` for library linking |
| `--font` | No | Font for citation superscripts (default: Calibri) |
| `--size` | No | Font size in pt (default: 11) |
| `--bib-heading` | No | Heading text for bibliography location (default: References) |

## Input Files

### Bibliography (CSL JSON)

An array of CSL-JSON items, each with an `id` matching the citation keys in your document:

```json
[
  {
    "id": "banwell2023",
    "type": "article-journal",
    "title": "Diagnosis of myelin oligodendrocyte glycoprotein...",
    "author": [{"family": "Banwell", "given": "B"}],
    "container-title": "The Lancet Neurology",
    "volume": "22",
    "page": "268-282",
    "issued": {"date-parts": [[2023]]},
    "DOI": "10.1016/S1474-4422(22)00431-8"
  }
]
```

### Keymap (optional)

Maps citation keys to Zotero item keys so the plugin can link back to your library:

```json
{
  "banwell2023": "BBJKWN9G",
  "sattarnezhad2018": null
}
```

Set a key to `null` if the item is not in your Zotero library.

### Getting a Zotero API key

1. Go to https://www.zotero.org/settings/keys
2. Create a new key with read/write access to your library
3. Your library ID is visible in the URL when viewing your library on zotero.org

## How it works

A Zotero citation in a Word document is a Word "complex field" consisting of 5 XML runs:

1. `<w:fldChar fldCharType="begin"/>` — field start
2. `<w:instrText>ADDIN ZOTERO_ITEM CSL_CITATION {...json...}</w:instrText>` — the Zotero payload
3. `<w:fldChar fldCharType="separate"/>` — separator
4. `<w:t>1</w:t>` — visible citation text (placeholder until Zotero refreshes)
5. `<w:fldChar fldCharType="end"/>` — field end

The `instrText` contains a JSON object with the citation ID, CSL-JSON item metadata, and a Zotero URI linking back to the user's library. The bibliography uses the same structure with `ADDIN ZOTERO_BIBL` instead.

`zotero_field_insert.py` constructs these XML elements using python-docx's low-level `OxmlElement` API and inserts them at each `[@key]` marker location.

## Limitations

- Citation display text is a placeholder until you click Refresh in the Zotero Word plugin
- The document must be opened in Word (not Google Docs or LibreOffice) with the Zotero plugin installed
- Multi-key citations like `[@key1; @key2]` are not yet supported (use separate markers)

## License

MIT
