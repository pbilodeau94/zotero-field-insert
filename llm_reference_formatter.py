"""
llm_reference_formatter.py

Use an LLM to identify and format references in a markdown document.

Supports multiple LLM backends:
- Any OpenAI-compatible API (OpenAI, ollama, vLLM, LM Studio, Azure, Together, Groq)
- Anthropic API
- Any CLI tool (claude, ollama run, llm, etc.) — no API key needed

This script takes markdown text with informal citations (e.g., "Banwell et al., 2023"
or "(Smith 2020)") and:
1. Uses an LLM to identify all citations and extract metadata
2. Searches CrossRef for full bibliographic metadata
3. Optionally adds missing items to your Zotero library via the Web API
4. Outputs pandoc-ready markdown with [@citekey] markers
5. Generates references.json (CSL JSON) and keymap.json for zotero_field_insert.py

Usage:
    # OpenAI API
    export OPENAI_API_KEY=sk-...
    python llm_reference_formatter.py input.md -o output.md

    # Anthropic API
    export ANTHROPIC_API_KEY=sk-ant-...
    python llm_reference_formatter.py input.md -o output.md --provider anthropic

    # CLI tool (no API key needed — uses claude, ollama, or any command)
    python llm_reference_formatter.py input.md -o output.md --provider cli
    python llm_reference_formatter.py input.md -o output.md --provider cli --cli-command "ollama run llama3"

    # Local model via OpenAI-compatible API
    python llm_reference_formatter.py input.md -o output.md \
        --api-base http://localhost:11434/v1 --model llama3

    # Any OpenAI-compatible API
    export OPENAI_API_KEY=your-key
    python llm_reference_formatter.py input.md -o output.md \
        --api-base https://api.together.xyz/v1 --model meta-llama/Llama-3-70b

Requirements:
    pip install requests
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

CROSSREF_API = "https://api.crossref.org/works"
CROSSREF_MAILTO = "crossref@example.com"  # polite pool
ZOTERO_API = "https://api.zotero.org"

CITATION_EXTRACTION_PROMPT = """\
You are a reference formatting assistant. Analyze the following markdown document \
and identify every citation or reference to a published work.

For each citation found, extract:
1. The text as it appears in the document (e.g., "Banwell et al., 2023" or "(Smith 2020)")
2. First author last name, year
3. **CRITICAL: Infer the likely title or topic of the cited work from the surrounding \
context.** For example, if the text says "The RAND/UCLA Appropriateness Method recommends \
a panel of 9 members (Fitch et al. 2001)", the title_hint should be "RAND UCLA \
appropriateness method manual". If it says "nonblinded assessors exaggerate effect sizes \
(Hrobjartsson et al. 2013)", the title_hint should be about observer bias in clinical \
trials. Be specific — this is the primary field used for CrossRef search.
4. Any journal name mentioned or inferable from context
5. A suggested citation key in the format: firstauthorlastnameYEAR (lowercase, no spaces)

Also identify any numbered reference list at the end of the document and extract metadata \
from those entries.

Return a JSON object with this structure:
{
  "citations": [
    {
      "original_text": "Banwell et al., 2023",
      "context": "the sentence or phrase where this citation appears",
      "first_author": "Banwell",
      "year": "2023",
      "title_hint": "inferred title or topic keywords (be specific for CrossRef search)",
      "journal_hint": "any journal you can infer",
      "suggested_key": "banwell2023"
    }
  ],
  "reference_list": [
    {
      "original_text": "1. Banwell B, Bennett JL, ...",
      "first_author": "Banwell",
      "year": "2023",
      "title": "full title if available",
      "journal": "The Lancet Neurology",
      "volume": "22",
      "pages": "268-282",
      "doi": "10.1016/...",
      "suggested_key": "banwell2023"
    }
  ]
}

Return ONLY the JSON object, no other text.

Document:
"""

REWRITE_PROMPT = """\
You are a reference formatting assistant. Rewrite the following markdown document, \
replacing every inline citation with the pandoc citation syntax [@citekey].

Use these citation key mappings (original text -> citekey):
{mappings}

Rules:
- Replace "(Author et al., Year)" or "Author et al. (Year)" with [@citekey]
- For citations at the end of a sentence, place [@citekey] before the period
- For parenthetical citations, replace the entire parenthetical with [@citekey]
- If multiple citations are grouped, use [@key1; @key2]
- Remove any numbered reference list at the end (it will be auto-generated)
- Keep all other content exactly the same — do not change wording, structure, or formatting
- Preserve all YAML frontmatter, headings, figures, tables, etc.

Return ONLY the rewritten markdown, no explanation.

Document:
"""


# ---------------------------------------------------------------------------
# LLM backends
# ---------------------------------------------------------------------------

def llm_call(prompt, provider, model, api_base=None, api_key=None, max_tokens=8192,
             cli_command=None):
    """Call an LLM. Supports 'openai', 'anthropic', and 'cli' providers."""
    if provider == "anthropic":
        return _call_anthropic(prompt, model, api_key, max_tokens)
    elif provider == "openai":
        return _call_openai(prompt, model, api_base, api_key, max_tokens)
    elif provider == "cli":
        return _call_cli(prompt, cli_command)
    else:
        print(f"Error: unknown provider '{provider}'. Use 'openai', 'anthropic', or 'cli'.")
        sys.exit(1)


def _call_openai(prompt, model, api_base=None, api_key=None, max_tokens=8192):
    """Call any OpenAI-compatible API using requests (no SDK dependency)."""
    base = (api_base or os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")).rstrip("/")
    key = api_key or os.environ.get("OPENAI_API_KEY", "")

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }

    resp = requests.post(f"{base}/chat/completions", headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _call_anthropic(prompt, model, api_key=None, max_tokens=8192):
    """Call the Anthropic API using requests (no SDK dependency)."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    headers = {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    resp = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"].strip()


def _call_cli(prompt, cli_command=None):
    """Call an LLM via a CLI tool. No API key needed.

    Auto-detects available tools: claude, ollama, llm.
    Or use a custom command via --cli-command.

    The prompt is passed via stdin (piped) to the command.
    """
    if cli_command:
        cmd = cli_command
    else:
        # Auto-detect available CLI tools
        if shutil.which("claude"):
            cmd = "claude --print"
        elif shutil.which("ollama"):
            cmd = "ollama run llama3"
        elif shutil.which("llm"):
            cmd = "llm"
        else:
            print("Error: no LLM CLI tool found. Install claude, ollama, or llm,")
            print("  or specify --cli-command 'your-command'")
            sys.exit(1)

    print(f"  Using CLI: {cmd}")

    # Write prompt to temp file and pipe it
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        # Remove CLAUDECODE env var so claude CLI doesn't refuse to run
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        result = subprocess.run(
            cmd,
            shell=True,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if result.returncode != 0:
            print(f"CLI error (exit {result.returncode}): {result.stderr[:500]}")
            sys.exit(1)
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print("Error: CLI command timed out after 5 minutes")
        sys.exit(1)
    finally:
        os.unlink(prompt_file)


# ---------------------------------------------------------------------------
# CrossRef
# ---------------------------------------------------------------------------

def search_crossref(query, author=None, year=None, rows=3):
    """Search CrossRef for a work. Returns list of candidate items."""
    params = {"query": query, "rows": rows, "mailto": CROSSREF_MAILTO}
    if author:
        params["query.author"] = author
    if year:
        params["query.bibliographic"] = f"{query} {year}"

    try:
        resp = requests.get(CROSSREF_API, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("items", [])
    except Exception as e:
        print(f"  CrossRef search failed: {e}")
        return []


def crossref_to_csl(item):
    """Convert a CrossRef API item to CSL JSON format."""
    csl = {}
    csl["type"] = item.get("type", "article-journal").replace("journal-article", "article-journal")
    csl["title"] = item.get("title", [""])[0] if isinstance(item.get("title"), list) else item.get("title", "")
    csl["DOI"] = item.get("DOI", "")

    authors = []
    for a in item.get("author", []):
        author = {}
        if "family" in a:
            author["family"] = a["family"]
        if "given" in a:
            author["given"] = a["given"]
        if author:
            authors.append(author)
    if authors:
        csl["author"] = authors

    ct = item.get("container-title", [])
    if ct:
        csl["container-title"] = ct[0] if isinstance(ct, list) else ct

    if item.get("volume"):
        csl["volume"] = item["volume"]
    if item.get("issue"):
        csl["issue"] = item["issue"]
    if item.get("page"):
        csl["page"] = item["page"]

    issued = item.get("issued", {})
    date_parts = issued.get("date-parts", [[]])
    if date_parts and date_parts[0]:
        csl["issued"] = {"date-parts": [date_parts[0]]}

    issn = item.get("ISSN", [])
    if issn:
        csl["ISSN"] = issn[0] if isinstance(issn, list) else issn

    return csl


def score_crossref_match(item, author=None, year=None, title_hint=None):
    """Score how well a CrossRef result matches our search criteria."""
    score = 0

    if author and item.get("author"):
        first_author = item["author"][0].get("family", "").lower()
        if first_author == author.lower():
            score += 3
        elif author.lower() in first_author:
            score += 1

    issued = item.get("issued", {}).get("date-parts", [[]])
    if issued and issued[0] and year:
        if str(issued[0][0]) == str(year):
            score += 3

    if title_hint:
        item_title = (item.get("title", [""])[0] if isinstance(item.get("title"), list)
                      else item.get("title", "")).lower()
        hint_words = [w for w in title_hint.lower().split() if len(w) > 4]
        matches = sum(1 for w in hint_words if w in item_title)
        score += min(matches, 3)

    cr_score = item.get("score", 0)
    if cr_score > 100:
        score += 2
    elif cr_score > 50:
        score += 1

    return score


def search_pubmed(query, author=None, year=None, max_results=3):
    """Search PubMed for a work. Returns list of DOIs found."""
    # Build PubMed search query
    terms = []
    if author:
        terms.append(f"{author}[Author]")
    if year:
        terms.append(f"{year}[Date - Publication]")
    if query:
        terms.append(query)
    search_term = " AND ".join(terms) if terms else query

    try:
        # ESearch
        params = {
            "db": "pubmed",
            "term": search_term,
            "retmax": max_results,
            "retmode": "json",
        }
        resp = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                            params=params, timeout=15)
        resp.raise_for_status()
        ids = resp.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        # ESummary to get DOIs
        params = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "json",
        }
        resp = requests.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                            params=params, timeout=15)
        resp.raise_for_status()
        result = resp.json().get("result", {})

        dois = []
        for pmid in ids:
            article = result.get(pmid, {})
            for aid in article.get("articleids", []):
                if aid.get("idtype") == "doi":
                    dois.append(aid["value"])
                    break
        return dois
    except Exception as e:
        print(f"  PubMed search failed: {e}")
        return []


def crossref_by_doi(doi):
    """Fetch a specific work from CrossRef by DOI."""
    try:
        resp = requests.get(f"{CROSSREF_API}/{doi}", params={"mailto": CROSSREF_MAILTO}, timeout=15)
        resp.raise_for_status()
        return resp.json().get("message", {})
    except Exception:
        return None


def find_best_crossref_match(citation):
    """Find the best CrossRef match for a citation. Falls back to PubMed if needed."""
    author = citation.get("first_author", "")
    year = citation.get("year", "")
    title = citation.get("title_hint") or citation.get("title", "")
    journal = citation.get("journal_hint") or citation.get("journal", "")

    # Strategy 1: Search CrossRef with title hint (most specific)
    queries = []
    if title and len(title) > 10:
        queries.append(title)
    if author and title and len(title) > 5:
        queries.append(f"{author} {title}")
    if author and year:
        queries.append(f"{author} {year}")
    if author and journal:
        queries.append(f"{author} {journal} {year}")

    best_item = None
    best_score = -1

    for query in queries[:3]:
        items = search_crossref(query, author=author, year=year, rows=5)
        for item in items:
            s = score_crossref_match(item, author, year, title)
            if s > best_score:
                best_score = s
                best_item = item
        if best_score >= 6:
            break  # good enough match, stop searching
        time.sleep(0.5)  # polite rate limiting

    # Strategy 2: PubMed fallback if CrossRef match is weak
    if best_score < 5 and (author or title):
        pubmed_query = title if title and len(title) > 10 else f"{author} {year}"
        dois = search_pubmed(pubmed_query, author=author, year=year)
        for doi in dois:
            item = crossref_by_doi(doi)
            if item:
                s = score_crossref_match(item, author, year, title)
                if s > best_score:
                    best_score = s
                    best_item = item
        time.sleep(0.5)

    if best_score >= 4:
        return best_item, best_score
    return None, best_score


# ---------------------------------------------------------------------------
# Zotero
# ---------------------------------------------------------------------------

def search_zotero_library(api_key, library_id, query, library_type="user"):
    """Search a Zotero library for an item."""
    url = f"{ZOTERO_API}/{library_type}s/{library_id}/items"
    headers = {"Zotero-API-Key": api_key, "Zotero-API-Version": "3"}
    params = {"q": query, "limit": 5, "itemType": "-attachment -note"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  Zotero search failed: {e}")
        return []


def add_to_zotero(api_key, library_id, csl_item, library_type="user"):
    """Add an item to a Zotero library via the Web API. Returns the item key."""
    type_map = {
        "article-journal": "journalArticle",
        "book": "book",
        "chapter": "bookSection",
        "paper-conference": "conferencePaper",
        "report": "report",
        "thesis": "thesis",
    }
    zot_type = type_map.get(csl_item.get("type", ""), "journalArticle")

    zot_item = {"itemType": zot_type}

    if csl_item.get("title"):
        zot_item["title"] = csl_item["title"]
    if csl_item.get("container-title"):
        zot_item["publicationTitle"] = csl_item["container-title"]
    if csl_item.get("volume"):
        zot_item["volume"] = csl_item["volume"]
    if csl_item.get("issue"):
        zot_item["issue"] = csl_item["issue"]
    if csl_item.get("page"):
        zot_item["pages"] = csl_item["page"]
    if csl_item.get("DOI"):
        zot_item["DOI"] = csl_item["DOI"]
    if csl_item.get("issued", {}).get("date-parts"):
        parts = csl_item["issued"]["date-parts"][0]
        zot_item["date"] = "-".join(str(p) for p in parts)

    creators = []
    for a in csl_item.get("author", []):
        creators.append({
            "creatorType": "author",
            "firstName": a.get("given", ""),
            "lastName": a.get("family", ""),
        })
    if creators:
        zot_item["creators"] = creators

    url = f"{ZOTERO_API}/{library_type}s/{library_id}/items"
    headers = {
        "Zotero-API-Key": api_key,
        "Zotero-API-Version": "3",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, json=[zot_item], timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get("successful"):
            new_item = list(result["successful"].values())[0]
            return new_item.get("key")
        elif result.get("failed"):
            print(f"  Zotero add failed: {result['failed']}")
        return None
    except Exception as e:
        print(f"  Zotero API error: {e}")
        return None


def lookup_zotero_key_local(zotero_db, title=None, doi=None):
    """Look up a Zotero item key from the local SQLite database."""
    if not zotero_db or not Path(zotero_db).exists():
        return None
    try:
        db = sqlite3.connect(str(zotero_db))
        if doi:
            row = db.execute("""
                SELECT i.key FROM items i
                JOIN itemData id ON i.itemID = id.itemID
                JOIN itemDataValues idv ON id.valueID = idv.valueID
                JOIN fields f ON id.fieldID = f.fieldID
                WHERE f.fieldName = 'DOI' AND LOWER(idv.value) = LOWER(?)
            """, (doi,)).fetchone()
            if row:
                db.close()
                return row[0]
        if title:
            row = db.execute("""
                SELECT i.key FROM items i
                JOIN itemData id ON i.itemID = id.itemID
                JOIN itemDataValues idv ON id.valueID = idv.valueID
                JOIN fields f ON id.fieldID = f.fieldID
                WHERE f.fieldName = 'title' AND LOWER(idv.value) LIKE LOWER(?)
            """, (f"%{title[:50]}%",)).fetchone()
            if row:
                db.close()
                return row[0]
        db.close()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PROVIDER_DEFAULTS = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
}


def main():
    parser = argparse.ArgumentParser(
        description="Use an LLM to identify and format references in a markdown document"
    )
    parser.add_argument("input", help="Input markdown file with informal citations")
    parser.add_argument("--output", "-o", help="Output markdown file with [@citekey] markers")
    parser.add_argument("--bib-output", default="references.json",
                        help="Output CSL JSON bibliography (default: references.json)")
    parser.add_argument("--keymap-output", default="keymap.json",
                        help="Output keymap for zotero_field_insert (default: keymap.json)")
    parser.add_argument("--provider", "-p", default="openai",
                        choices=["openai", "anthropic", "cli"],
                        help="LLM provider (default: openai). 'openai' works with any OpenAI-compatible API. "
                             "'cli' uses a local CLI tool (claude, ollama, llm) — no API key needed.")
    parser.add_argument("--model", "-m", help="Model name (default depends on provider)")
    parser.add_argument("--api-base", help="API base URL (for local/custom endpoints, e.g., http://localhost:11434/v1)")
    parser.add_argument("--api-key", help="API key (overrides env var)")
    parser.add_argument("--cli-command", help="Custom CLI command for --provider cli (e.g., 'ollama run llama3')")
    parser.add_argument("--zotero-api-key", help="Zotero Web API key (for adding items)")
    parser.add_argument("--zotero-library-id", help="Zotero library ID")
    parser.add_argument("--zotero-db", help="Path to local zotero.sqlite (for key lookups)")
    parser.add_argument("--no-crossref", action="store_true",
                        help="Skip CrossRef lookups (use LLM-extracted metadata only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing files")
    args = parser.parse_args()

    # Read input
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found")
        sys.exit(1)
    text = input_path.read_text()

    output_path = args.output or str(input_path.with_suffix("")) + "_cited.md"
    model = args.model or PROVIDER_DEFAULTS.get(args.provider, "gpt-4o")

    print(f"Provider: {args.provider}" + (f" | Model: {model}" if args.provider != "cli" else ""))
    if args.api_base:
        print(f"API base: {args.api_base}")
    if args.cli_command:
        print(f"CLI command: {args.cli_command}")

    # Step 1: Extract citations with LLM
    print("\nStep 1: Extracting citations...")
    raw = llm_call(
        CITATION_EXTRACTION_PROMPT + text,
        provider=args.provider,
        model=model,
        api_base=args.api_base,
        api_key=args.api_key,
        max_tokens=4096,
        cli_command=args.cli_command,
    )

    # Parse JSON (handle potential markdown code blocks)
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        extracted = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error parsing LLM response: {e}")
        print("Raw response:", raw[:500])
        sys.exit(1)

    citations = extracted.get("citations", [])
    ref_list = extracted.get("reference_list", [])
    print(f"  Found {len(citations)} inline citations and {len(ref_list)} reference list entries")

    # Merge reference list info into citations
    all_refs = {}
    for ref in ref_list:
        key = ref.get("suggested_key", "")
        if key:
            all_refs[key] = ref
    for cit in citations:
        key = cit.get("suggested_key", "")
        if key and key not in all_refs:
            all_refs[key] = cit
        elif key and key in all_refs:
            existing = all_refs[key]
            if not existing.get("title_hint") and cit.get("title_hint"):
                existing["title_hint"] = cit["title_hint"]

    print(f"  {len(all_refs)} unique references to resolve")

    # Step 2: Look up each reference
    print("\nStep 2: Resolving references...")
    bib_items = []
    keymap = {}
    mappings = []

    for key, ref in all_refs.items():
        author = ref.get("first_author", "")
        year = ref.get("year", "")
        print(f"  [{key}] {author} {year}...", end=" ")

        # Check local Zotero DB first
        zotero_key = None
        if args.zotero_db:
            title = ref.get("title") or ref.get("title_hint", "")
            doi = ref.get("doi", "")
            zotero_key = lookup_zotero_key_local(args.zotero_db, title=title, doi=doi)
            if zotero_key:
                print(f"found in Zotero [{zotero_key}]")

        # CrossRef lookup
        csl = None
        if not args.no_crossref:
            cr_item, score = find_best_crossref_match(ref)
            if cr_item:
                csl = crossref_to_csl(cr_item)
                csl["id"] = key
                print(f"CrossRef match (score={score})" +
                      (f" - {csl.get('title', '')[:60]}" if not zotero_key else ""))
            else:
                print("no CrossRef match")
        else:
            print("skipping CrossRef")

        if csl is None:
            csl = {
                "id": key,
                "type": "article-journal",
                "title": ref.get("title") or ref.get("title_hint", f"[{key}]"),
            }
            if author:
                csl["author"] = [{"family": author}]
            if year:
                csl["issued"] = {"date-parts": [[int(year)]]}
            if ref.get("journal") or ref.get("journal_hint"):
                csl["container-title"] = ref.get("journal") or ref.get("journal_hint")
            if ref.get("volume"):
                csl["volume"] = ref["volume"]
            if ref.get("pages"):
                csl["page"] = ref["pages"]
            if ref.get("doi"):
                csl["DOI"] = ref["doi"]

        bib_items.append(csl)
        keymap[key] = zotero_key

        # Add to Zotero if requested and not already there
        if not zotero_key and args.zotero_api_key and args.zotero_library_id:
            print(f"    Adding to Zotero...", end=" ")
            new_key = add_to_zotero(
                args.zotero_api_key, args.zotero_library_id, csl
            )
            if new_key:
                keymap[key] = new_key
                print(f"added [{new_key}]")
            else:
                print("failed")

        # Build mapping for rewrite prompt
        orig = ref.get("original_text", "")
        if orig:
            mappings.append(f'"{orig}" -> [@{key}]')

    # Step 3: Rewrite document with LLM
    print(f"\nStep 3: Rewriting document with [@citekey] markers...")
    mapping_text = "\n".join(mappings)
    rewrite_prompt = REWRITE_PROMPT.replace("{mappings}", mapping_text)

    rewritten = llm_call(
        rewrite_prompt + text,
        provider=args.provider,
        model=model,
        api_base=args.api_base,
        api_key=args.api_key,
        max_tokens=8192,
        cli_command=args.cli_command,
    )

    # Remove any markdown code blocks wrapping
    if rewritten.startswith("```"):
        rewritten = re.sub(r"^```\w*\n?", "", rewritten)
        rewritten = re.sub(r"\n?```$", "", rewritten)

    if args.dry_run:
        print("\n--- DRY RUN ---")
        print(f"Would write {len(bib_items)} items to {args.bib_output}")
        print(f"Would write keymap to {args.keymap_output}")
        print(f"Would write rewritten markdown to {output_path}")
        print(f"\nBibliography items: {[item['id'] for item in bib_items]}")
        print(f"Keymap: {json.dumps(keymap, indent=2)}")
        return

    # Step 4: Write outputs
    print(f"\nStep 4: Writing outputs...")

    with open(args.bib_output, "w") as f:
        json.dump(bib_items, f, indent=2)
    print(f"  {args.bib_output}: {len(bib_items)} items")

    with open(args.keymap_output, "w") as f:
        json.dump(keymap, f, indent=2)
    print(f"  {args.keymap_output}: {len(keymap)} entries")

    with open(output_path, "w") as f:
        f.write(rewritten)
    print(f"  {output_path}: rewritten with [@citekey] markers")

    print(f"\nDone! Next steps:")
    print(f"  1. Review {output_path} for correct citation placement")
    print(f"  2. Convert to docx: pandoc {output_path} -o output.docx --reference-doc=template.docx")
    print(f"  3. Insert Zotero fields: python zotero_field_insert.py output.docx --bib {args.bib_output} --keymap {args.keymap_output} --zotero-db ~/Zotero/zotero.sqlite")
    print(f"  4. Open in Word and click Zotero > Refresh")


if __name__ == "__main__":
    main()
