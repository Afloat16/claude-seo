#!/usr/bin/env python3
"""
AI-pattern remover. Rewrites filler / AI-typical phrasings into
direct prose, and strips invisible Unicode artifacts ("text
watermarks") that AI tools and copy-paste pipelines leave behind:
zero-width characters, directional marks, exotic spaces, and Unicode
tag characters. Conservative by design: only replaces phrases listed
in ``_REPLACEMENTS``, and keeps zero-width joiners / variation
selectors that are part of real emoji sequences. Unknown idiom?
Leave it alone.

Statistical watermarks (e.g. SynthID-style token-probability
watermarks) live in word choice, not codepoints; nothing here, or
anywhere, reliably detects or strips those, so this tool does not
claim to.

Use case: a content editor running last-mile cleanup on a draft. This
is NOT a paraphraser or a translation tool; it does not introduce new
content. Every replacement is a deterministic 1:1 swap. Markdown code,
links/references and URLs retain their original source, including whitespace
and Unicode characters; cleanup applies to the surrounding prose.

Attribution
===========
Replacement table aligns with the Wikipedia "AI Cleanup" catalogue
(CC BY-SA 4.0) and ivankuznetsov/claude-seo's 24-pattern list (MIT).
We diverge from those upstreams only on a handful of phrases where
their preferred replacement reads less naturally for SEO contexts
(e.g. "leverage" -> "use", not "employ"). Diff is documented inline.

CLI::

    python scripts/content_humanize.py draft.md -o cleaned.md
    cat draft.md | python scripts/content_humanize.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# Replacements run in order. Each entry is (pattern, replacement, label).
# Patterns are compiled with re.IGNORECASE and \b word boundaries where
# appropriate. The replacement preserves the original case of the first
# character (e.g. "Leverage X" -> "Use X", not "use X").
_REPLACEMENTS: tuple[tuple[str, str, str], ...] = (
    (r"\bdelve\s+deeper\s+into\b", "explore", "delve-deeper-into"),
    (r"\bdelve\s+into\b", "explore", "delve-into"),
    (r"\bin\s+the\s+ever-evolving\s+landscape\s+of\b", "in", "ever-evolving-landscape"),
    (r"\bin\s+the\s+ever-evolving\s+world\s+of\b", "in", "ever-evolving-world"),
    (r"\bever-evolving\b", "changing", "ever-evolving"),
    (r"\bever-changing\b", "changing", "ever-changing"),
    (r"\bnavigating\s+the\s+complexities\s+of\b", "handling", "navigating-complexities"),
    (r"\btapestry\s+of\b", "range of", "tapestry-of"),
    (r"\b(rich|intricate|complex)\s+tapestry\b", "range", "rich-tapestry"),
    (r"\bembark\s+on\s+a\s+journey\b", "begin", "embark-journey"),
    (r"\ba\s+testament\s+to\b", "evidence of", "testament-to"),
    (r"\ba\s+beacon\s+of\b", "a leader in", "beacon-of"),
    (r"\b(the\s+|a\s+)?cornerstone\s+of\b", "central to", "cornerstone-of"),
    (r"\bat\s+the\s+heart\s+of\b", "central to", "at-the-heart-of"),
    (r"\bin\s+essence,\s*", "", "in-essence"),
    (r"\bin\s+conclusion,\s*", "", "in-conclusion"),
    (r"\bultimately,\s*", "", "ultimately-comma"),
    (r"\bmoreover,\s*", "", "moreover-comma"),
    (r"\bfurthermore,\s*", "", "furthermore-comma"),
    (r"\bhowever,\s+it'?s\s+worth\s+noting\s+that\b", "however,", "worth-noting-clause"),
    (r"\bit'?s\s+worth\s+noting\s+that\b", "note:", "worth-noting"),
    (r"\bby\s+leveraging\b", "by using", "by-leveraging"),
    (r"\bleverage\s+the\s+power\s+of\b", "use", "leverage-power"),
    (r"\bleveraging\s+the\s+power\s+of\b", "using", "leveraging-power"),
    (r"\bharness\s+the\s+power\s+of\b", "use", "harness-power"),
    (r"\bunlock\s+(?:the\s+(?:full\s+)?)?potential\b", "use", "unlock-potential"),
    (r"\bopen\s+up\s+a\s+world\s+of\b", "enable", "open-world"),
    (r"\ba\s+world\s+of\s+possibilities\b", "options", "world-possibilities"),
    (r"\belevate\s+your\b", "improve your", "elevate-your"),
    (r"\btransform\s+your\b", "improve your", "transform-your"),
    (r"\brevolutionize\s+the\s+way\b", "change how", "revolutionize-the-way"),
    (r"\bgame-?changer\b", "important", "game-changer"),
    (r"\bcutting-?edge\b", "modern", "cutting-edge"),
    (r"\bstate-of-the-art\b", "modern", "state-of-the-art"),
    (r"\bin\s+summary,\s*", "", "in-summary"),
    (r"\bto\s+summarize,\s*", "", "to-summarize"),
    (r"\bto\s+put\s+it\s+simply,\s*", "", "to-put-simply"),
    (r"\bin\s+a\s+nutshell,\s*", "", "in-nutshell"),
    (r"\bit'?s\s+important\s+to\s+note\s+that\b", "note:", "important-note"),
    (r"\bin\s+today'?s\s+(fast-paced|digital|competitive)\s+(world|age|landscape)\b",
     "today", "today-cliche"),
    (r"\bneedless\s+to\s+say,?\s*", "", "needless-to-say"),
    (r"\bat\s+the\s+end\s+of\s+the\s+day\b", "ultimately", "end-of-the-day"),
    (r"\bwhen\s+it\s+comes\s+to\b", "for", "when-it-comes-to"),
    (r"\bfirst\s+and\s+foremost,?\s*", "first,", "first-and-foremost"),
    (r"\blast\s+but\s+not\s+least,?\s*", "finally,", "last-but-not-least"),
    (r"\blet'?s\s+dive\s+(in|into)\b", "starting with", "let-us-dive"),
    (r"\blet'?s\s+take\s+a\s+(closer|deeper)\s+look\b", "look at", "let-us-take-look"),
)


_PATTERNS = [
    (re.compile(p, re.IGNORECASE), repl, label)
    for p, repl, label in _REPLACEMENTS
]


# Invisible / format characters deleted outright. They render as
# nothing, break regex word boundaries, inflate diffs, and are the
# codepoints used to fingerprint or smuggle hidden text in AI output.
_INVISIBLE_DELETE = {
    "\u00ad": "soft-hyphen",
    "\u180e": "mongolian-vowel-separator",
    "\u200b": "zero-width-space",
    "\u200e": "left-to-right-mark",
    "\u200f": "right-to-left-mark",
    "\u202a": "lre-embedding",
    "\u202b": "rle-embedding",
    "\u202c": "pop-directional",
    "\u202d": "lro-override",
    "\u202e": "rlo-override",
    "\u2060": "word-joiner",
    "\u2061": "function-application",
    "\u2062": "invisible-times",
    "\u2063": "invisible-separator",
    "\u2064": "invisible-plus",
    "\u2066": "lri-isolate",
    "\u2067": "rli-isolate",
    "\u2068": "fsi-isolate",
    "\u2069": "pop-isolate",
    "\u3164": "hangul-filler",
    "\ufeff": "zero-width-no-break-space",
    "\uffa0": "halfwidth-hangul-filler",
}

# Exotic whitespace normalised to a plain equivalent instead of deleted.
_INVISIBLE_SPACE = {
    "\u00a0": ("space", " "),   # no-break space
    "\u2000": ("space", " "),   # en quad
    "\u2001": ("space", " "),   # em quad
    "\u2002": ("space", " "),   # en space
    "\u2003": ("space", " "),   # em space
    "\u2004": ("space", " "),   # three-per-em space
    "\u2005": ("space", " "),   # four-per-em space
    "\u2006": ("space", " "),   # six-per-em space
    "\u2007": ("space", " "),   # figure space
    "\u2008": ("space", " "),   # punctuation space
    "\u2009": ("space", " "),   # thin space
    "\u200a": ("space", " "),   # hair space
    "\u202f": ("space", " "),   # narrow no-break space
    "\u205f": ("space", " "),   # medium mathematical space
    "\u2028": ("newline", "\n"),    # line separator
    "\u2029": ("newline", "\n\n"),  # paragraph separator
}

# Legitimate inside emoji sequences (family/warning/keycap emoji);
# stripped only when nothing emoji-like sits next to them.
_EMOJI_GLUE = {
    "\u200c": "zero-width-non-joiner",
    "\u200d": "zero-width-joiner",
    "\ufe0e": "variation-selector-15",
    "\ufe0f": "variation-selector-16",
}


def _is_emoji_like(ch: str) -> bool:
    """True when ``ch`` is itself an emoji-ish symbol, not just a high codepoint.

    Uses the Unicode general category (``So``, "symbol, other") plus the two
    ranges that hold the bulk of pictographic and emoji-adjacent symbol
    characters, instead of a blanket ``codepoint >= U+2100`` test, which also
    matches CJK ideographs, Hangul, and letters in many other scripts.
    """
    if not ch:
        return False
    if unicodedata.category(ch) == "So":
        return True
    cp = ord(ch)
    return (0x1F000 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF)


def _is_letter(ch: str) -> bool:
    """True when ``ch`` is a Unicode letter (any script)."""
    return bool(ch) and unicodedata.category(ch).startswith("L")


def _emoji_adjacent(prev: str, nxt: str) -> bool:
    """True when either neighbour is plausibly part of an emoji sequence."""
    for ch in (prev, nxt):
        if ch and (_is_emoji_like(ch) or ch == "\u20e3"):
            return True
    return False


def strip_invisible(text: str) -> tuple[str, dict]:
    """Remove invisible watermark characters; return (cleaned, counts)."""
    removed: dict[str, int] = {}
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch in _INVISIBLE_DELETE:
            label = _INVISIBLE_DELETE[ch]
        elif ch in _INVISIBLE_SPACE:
            label, replacement = _INVISIBLE_SPACE[ch]
            out.append(replacement)
        elif ch in _EMOJI_GLUE:
            prev = text[i - 1] if i else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            # ZWNJ/ZWJ are orthographic in Arabic, Persian, Urdu, Devanagari,
            # Bengali, Tamil, and other scripts that use joining or conjunct
            # forms: never delete them next to a letter in any script.
            if ch in ("\u200c", "\u200d") and (_is_letter(prev) or _is_letter(nxt)):
                out.append(ch)
                continue
            if _emoji_adjacent(prev, nxt):
                out.append(ch)
                continue
            label = _EMOJI_GLUE[ch]
        elif 0xE0000 <= ord(ch) <= 0xE007F:
            label = "unicode-tag-character"
        else:
            out.append(ch)
            continue
        removed[label] = removed.get(label, 0) + 1
    return "".join(out), removed


def _preserve_case(match_text: str, replacement: str) -> str:
    """If the original starts uppercase, capitalise the replacement."""
    if not replacement:
        return ""
    if match_text and match_text[0].isupper() and not replacement[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


# Keep source slices, rather than rendering Markdown or inserting placeholders:
# code, URLs and link identifiers must survive every cleanup pass byte-for-byte.
_FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")
_LIST_RE = re.compile(r"( *)(?:[-+*]|[0-9]{1,9}[.)])( +)(.*)$")
_QUOTE_RE = re.compile(r" {0,3}> ?")
_URL_RE = re.compile(r"(?:https?://|mailto:|www\.)[^\s<>]+", re.IGNORECASE)
_REFERENCE_RE = re.compile(
    r"(?m)^[ \t]*(?:>[ \t]*)*\[((?:\\.|[^\[\]\\]){1,999})\]:"
)


def _code_blocks(text: str) -> list[tuple[int, int]]:
    """Locate fenced/indented code without changing its source formatting.

    List indentation is relative to the current item; an indented nested list
    still contains prose. Quote/list prefixes are only used to recognise blocks.
    This is a conservative source protector, not a Markdown renderer.
    """
    spans = []
    fence = None  # (character, minimum length, quote depth, list indentation)
    list_indents: list[int] = []
    previous_blank = True
    indented = False
    offset = 0
    for source in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text):
        if not source:
            continue
        line = source.rstrip("\r\n").expandtabs(4)
        if fence:
            char, length, quotes, base = fence
            rest = line
            for _ in range(quotes):
                prefix = _QUOTE_RE.match(rest)
                if not prefix:
                    break
                rest = rest[prefix.end():]
            else:
                if not rest.strip() or len(rest) - len(rest.lstrip(" ")) >= base:
                    spans.append((offset, offset + len(source)))
                    closing = _FENCE_RE.fullmatch(rest[base:])
                    if (closing and closing[1][0] == char
                            and len(closing[1]) >= length and not closing[2].strip()):
                        fence = None
                    previous_blank = True
                    offset += len(source)
                    continue
            # A quote/list ending also terminates its unterminated fence.
            fence = None
        quotes = 0
        while prefix := _QUOTE_RE.match(line):
            quotes += 1
            line = line[prefix.end():]
        blank = not line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if not blank:
            while list_indents and indent < list_indents[-1]:
                list_indents.pop()
        base = list_indents[-1] if list_indents else 0
        if blank:
            if indented:
                spans.append((offset, offset + len(source)))
            previous_blank = True
        elif indent >= base + 4 and (previous_blank or indented):
            spans.append((offset, offset + len(source)))
            indented = True
        else:
            indented = False
            item = _LIST_RE.fullmatch(line)
            if item:
                # A list marker starts prose, even when nested under another item.
                base = item.start(3) if len(item[2]) <= 4 else item.start(2) + 1
                list_indents.append(base)
                content = line[base:]
                if content.startswith("    "):
                    spans.append((offset, offset + len(source)))
                    indented = True
                    previous_blank = True
                    offset += len(source)
                    continue
            else:
                content = line[base:]
            opening = _FENCE_RE.fullmatch(content)
            if opening and not (opening[1][0] == "`" and "`" in opening[2]):
                fence = (opening[1][0], len(opening[1]), quotes, base)
                spans.append((offset, offset + len(source)))
                previous_blank = True
            else:
                previous_blank = False
        offset += len(source)
    return spans


def _code_spans(text: str) -> list[tuple[int, int]]:
    # Pre-index equal-length runs per paragraph, avoiding a suffix scan for
    # every unmatched backtick. Escapes affect openers, not literal code text.
    runs = list(re.finditer(r"`+|\n[ \t]*\r?\n|\r[ \t]*\r", text))
    next_run = {}
    after_escape = {}
    following = {}
    for i in range(len(runs) - 1, -1, -1):
        run = runs[i]
        if run[0][0] != "`":
            following.clear()
        else:
            if len(run[0]) in following:
                next_run[i] = following[len(run[0])]
            if len(run[0]) - 1 in following:
                after_escape[i] = following[len(run[0]) - 1]
            following[len(run[0])] = i
    spans = []
    i = 0
    while i < len(runs):
        run = runs[i]
        before = run.start() - 1
        while before >= 0 and text[before] == "\\":
            before -= 1
        escaped = (run.start() - before - 1) % 2
        closing = after_escape.get(i) if escaped else next_run.get(i)
        if closing is not None:
            end = closing
            spans.append((run.start() + escaped, runs[end].end()))
            i = end + 1
        else:
            i += 1
    return spans


def _reference_label(text: str) -> str:
    # A blockquote marker on a continuation line is container syntax, not ID text.
    text = re.sub(r"(?<=\n)(?: {0,3}> ?)+", "", text)
    return " ".join(text.split()).casefold()


def _reference_space(text: str, position: int) -> int:
    """Skip horizontal whitespace and at most one nonblank continuation line."""
    match = re.compile(r"[ \t]*(?:\r\n|\r|\n)?[ \t]*(?:>[ \t]*)*").match(text, position)
    return match.end()


def _reference_end(text: str, position: int) -> int | None:
    """Find a reference destination and its optional complete, multiline title."""
    position = _reference_space(text, position)
    start = position
    angled = position < len(text) and text[position] == "<"
    if angled:
        position += 1
    depth = 0
    while position < len(text):
        char = text[position]
        if char == "\\" and position + 1 < len(text) and text[position + 1] not in "\r\n":
            position += 2
            continue
        if angled:
            if char == ">":
                position += 1
                break
            if char in "<\r\n":
                return None
        elif ord(char) <= 32 or ord(char) == 127:
            break
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return None
            depth -= 1
        position += 1
    else:
        if angled:
            return None
    if position == start or depth:
        return None
    destination_end = position
    title_start = _reference_space(text, position)
    if title_start == position or title_start >= len(text) or text[title_start] not in "\"'(":
        return destination_end
    opening = text[title_start]
    closing = ")" if opening == "(" else opening
    position = title_start + 1
    while position < len(text):
        char = text[position]
        if char == "\\" and position + 1 < len(text) and text[position + 1] not in "\r\n":
            position += 2
            continue
        if char == closing:
            # A title must finish its line, not consume a following paragraph.
            if re.compile(r"[ \t]*(?:\r|\n|$)").match(text, position + 1):
                return position + 1
            return destination_end
        if opening == "(" and char == "(":
            return destination_end
        if char in "\r\n":
            next_line = _reference_space(text, position)
            if next_line == len(text) or text[next_line] in "\r\n":
                return destination_end  # A blank line terminates a reference title.
            position = next_line
        else:
            position += 1
    return destination_end


def _inline_literals(text: str, references: set[str]) -> list[tuple[int, int]]:
    spans = _code_spans(text)
    spans.extend(match.span() for match in _URL_RE.finditer(text))
    # Bracket pairs preserve whole links, including reference labels. Rewriting
    # one label independently could detach it from a definition elsewhere.
    brackets = {}
    stack = []
    escaped = False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "[":
            stack.append(i)
        elif char == "]" and stack:
            brackets[stack.pop()] = i
    consumed = 0
    for start, end in sorted(brackets.items()):
        if start < consumed:
            continue
        after = end + 1
        if after < len(text) and text[after] == "(":
            # Keep balanced destinations/titles together, including escaped
            # parentheses and angle-bracket destinations containing spaces.
            depth = 1
            quote = ""
            i = after + 1
            while i < len(text) and depth:
                char = text[i]
                if char == "\\":
                    i += 2
                    continue
                if quote:
                    if char == quote:
                        quote = ""
                elif char == "<":
                    quote = ">"
                elif char in "\"'" and i > after + 1 and text[i - 1].isspace():
                    quote = char
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                i += 1
            # Preserve unfinished link source as well; changing its destination
            # while an editor is still typing is no safer than changing a link.
            consumed = i
            spans.append((start, i))
        elif references and after in brackets and brackets[after] - after - 1 <= 999:
            other_end = brackets[after]
            other = _reference_label(text[after + 1:other_end])
            if not other and end - start - 1 <= 999:
                other = _reference_label(text[start + 1:end])
            if other in references:
                spans.append((start, other_end + 1))
                consumed = other_end + 1
        elif references and end - start - 1 <= 999:
            if _reference_label(text[start + 1:end]) in references:
                spans.append((start, after))
    return spans


def _literal_spans(text: str) -> list[tuple[int, int]]:
    blocks = _code_blocks(text)
    regions = []
    start = 0
    for left, right in blocks:
        if start < left:
            regions.append((start, left))
        start = right
    if start < len(text):
        regions.append((start, len(text)))
    spans = list(blocks)
    references = set()
    for left, right in regions:
        region = text[left:right]
        for match in _REFERENCE_RE.finditer(region):
            end = _reference_end(region, match.end())
            if end is not None:
                references.add(_reference_label(match[1]))
                spans.append((left + match.start(), left + end))
    for left, right in regions:
        spans.extend((left + a, left + b)
                     for a, b in _inline_literals(text[left:right], references))
    merged: list[tuple[int, int]] = []
    for left, right in sorted(spans):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return merged


def _humanize_prose(text: str) -> dict:
    """Apply every replacement; return the cleaned text plus a change log."""
    changes: list[dict] = []
    # Strip invisible characters first: zero-width codepoints inside
    # words would otherwise defeat the \b boundaries below.
    cleaned, invisible_removed = strip_invisible(text)

    for pattern, replacement, label in _PATTERNS:
        def _repl(match):
            original = match.group(0)
            new = _preserve_case(original, replacement)
            changes.append({
                "label": label,
                "from": original,
                "to": new,
            })
            return new
        cleaned = pattern.sub(_repl, cleaned)

    # Collapse double spaces introduced by deleted phrases, but leave
    # newlines and intentional spacing alone: only runs between two
    # non-space characters are touched, so Markdown indentation (code
    # blocks, nested lists) and trailing hard line breaks survive.
    cleaned = re.sub(r"(?<=\S)  +(?=\S)", " ", cleaned)
    cleaned = re.sub(r"(?<=\S) ([,.;:!?])", r"\1", cleaned)

    return {
        "cleaned": cleaned,
        "changes": changes,
        "change_count": len(changes),
        "invisible_removed": invisible_removed,
        "invisible_count": sum(invisible_removed.values()),
    }


def humanize(text: str) -> dict:
    """Clean prose while preserving literal Markdown source and URL targets."""
    parts = []
    changes = []
    invisible_removed: dict[str, int] = {}
    start = 0
    for left, right in [*_literal_spans(text), (len(text), len(text))]:
        result = _humanize_prose(text[start:left])
        parts.extend((result["cleaned"], text[left:right]))
        changes.extend(result["changes"])
        for label, count in result["invisible_removed"].items():
            invisible_removed[label] = invisible_removed.get(label, 0) + count
        start = right
    return {
        "cleaned": "".join(parts),
        "changes": changes,
        "change_count": len(changes),
        "invisible_removed": invisible_removed,
        "invisible_count": sum(invisible_removed.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-pattern remover for drafts.")
    parser.add_argument(
        "source",
        nargs="?",
        help="Path to a text/markdown file, or '-' for stdin (default '-').",
        default="-",
    )
    parser.add_argument("--output", "-o", help="Write cleaned text to this path.")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON with cleaned text + change log.")
    args = parser.parse_args()

    if args.source == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.source).read_text(encoding="utf-8", errors="replace")

    result = humanize(text)

    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.output:
        Path(args.output).write_text(result["cleaned"], encoding="utf-8")
        print(
            f"Wrote {args.output} ({result['change_count']} replacements)",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(result["cleaned"])

    if result["invisible_count"]:
        print(
            f"\n--- {result['invisible_count']} invisible characters removed ---",
            file=sys.stderr,
        )
        for label, count in sorted(result["invisible_removed"].items()):
            print(f"  {label}: {count}", file=sys.stderr)

    if result["change_count"]:
        print(f"\n--- {result['change_count']} replacements ---", file=sys.stderr)
        seen: set[str] = set()
        for change in result["changes"]:
            key = change["label"]
            if key in seen:
                continue
            seen.add(key)
            print(f"  {change['from']!r} -> {change['to']!r}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
