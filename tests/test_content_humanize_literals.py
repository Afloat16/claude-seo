"""Draft cleanup must not rewrite executable examples or link destinations."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from content_humanize import humanize  # noqa: E402


@pytest.mark.parametrize("literal", [
    '`state-of-the-art`',
    '``use `state-of-the-art` here``',
    '`state-of-the-art\nwith  two spaces ,`',
    '```python\nslug = "state-of-the-art"\n```',
    '~~~text\nDelve into  the command ,\n~~~',
    '````text\n```\nstate-of-the-art\n```\n`````',
    '  ```text\n  state-of-the-art\n   ```',
    '    slug = "state-of-the-art"\n    print(slug)',
    '\tslug = "state-of-the-art"',
    '-     state-of-the-art',
    r'\``state-of-the-art`',
    '`state-of-the-art\u2028  literal`',
    '> ```text\n> state-of-the-art\n> ```',
    '- ```text\n  state-of-the-art\n  ```',
    '- Example:\n\n      slug = "state-of-the-art"',
    '[Delve into the guide](https://example.com/state-of-the-art)',
    '![state-of-the-art](../state-of-the-art.png)',
    '[guide](../state-of-the-art/path_(section))',
    r'[guide](../state-of-the-art/escaped\(part\))',
    '[guide](<../state-of-the-art guide.md> "Delve into the guide")',
    '[guide](../state-of-the-art "A ) title")',
    '[a [nested] label](../state-of-the-art)',
    'https://example.com/state-of-the-art?q=cutting-edge#game-changer',
    'HTTPS://EXAMPLE.COM/state-of-the-art',
    '<https://example.com/state-of-the-art>',
    'mailto:state-of-the-art@example.com',
    'www.example.com/state-of-the-art',
])
def test_literal_source_survives_while_adjacent_prose_is_cleaned(literal):
    source = literal + '\n\nDelve into the prose.'
    result = humanize(source)
    assert result['cleaned'] == literal + '\n\nExplore the prose.'
    assert result['change_count'] == 1
    assert result['changes'] == [
        {'label': 'delve-into', 'from': 'Delve into', 'to': 'Explore'},
    ]


@pytest.mark.parametrize("source", [
    '```text\nstate-of-the-art\nDelve into the command.',
    '~~~text\nstate-of-the-art\n```\nDelve into the command.',
    '````text\nstate-of-the-art\n```\nDelve into the command.',
    '[guide](../state-of-the-art',
])
def test_unfinished_literal_is_preserved_during_editing(source):
    assert humanize(source)['cleaned'] == source
    assert humanize(source)['change_count'] == 0


@pytest.mark.parametrize("reference", [
    '[guide][state-of-the-art]',
    '[state-of-the-art][]',
    '[state-of-the-art]',
    '[guide][STATE-OF-THE-ART]',
])
def test_reference_links_remain_attached_to_their_later_definitions(reference):
    definition = '[state-of-the-art]: ../state-of-the-art "Delve into this guide"'
    source = f'{reference}\n\nDelve into the prose.\n\n{definition}\n'
    assert humanize(source)['cleaned'] == source.replace('Delve into the prose.', 'Explore the prose.')


def test_reference_destination_on_next_line_is_preserved():
    source = '[guide][state-of-the-art]\n\n[state-of-the-art]:\n  ../state-of-the-art\n'
    assert humanize(source)['cleaned'] == source


def test_unresolved_brackets_are_still_prose():
    source = '[Delve into the prose] and [state-of-the-art] claims.'
    assert humanize(source)['cleaned'] == '[Explore the prose] and [modern] claims.'


def test_nested_list_text_remains_prose_instead_of_becoming_code():
    source = '- Delve into it.\n    - Delve into details.\n        - Delve into examples.\n'
    result = humanize(source)
    assert result['cleaned'] == source.replace('Delve into', 'Explore')
    assert result['change_count'] == 3


def test_indentation_cannot_start_code_in_the_middle_of_a_paragraph():
    source = 'Read this paragraph\n    and delve into the details.'
    assert humanize(source)['cleaned'] == 'Read this paragraph\n    and explore the details.'


@pytest.mark.parametrize("source,expected", [
    ('> ```text\n> state-of-the-art\n\nDelve into it.',
     '> ```text\n> state-of-the-art\n\nExplore it.'),
    ('- ```text\n  state-of-the-art\n\nDelve into it.',
     '- ```text\n  state-of-the-art\n\nExplore it.'),
    ('`unmatched Delve into it.', '`unmatched Explore it.'),
    (r'\`Delve into it.', r'\`Explore it.'),
    ('`` state-of-the-art ` literal `` Delve into it.',
     '`` state-of-the-art ` literal `` Explore it.'),
    ('`Delve into it.\n\nstate-of-the-art`', '`Explore it.\n\nmodern`'),
])
def test_literal_boundaries_do_not_hide_following_prose(source, expected):
    assert humanize(source)['cleaned'] == expected


def test_unicode_and_spacing_are_preserved_inside_literals_only():
    literal = '`del\u200bve into\u00a0  state-of-the-art ,`'
    source = literal + '\n\nDel\u200bve into\u00a0  the prose , please.'
    result = humanize(source)
    assert result['cleaned'] == literal + '\n\nExplore the prose, please.'
    assert result['invisible_removed'] == {'zero-width-space': 1, 'space': 1}
    assert result['invisible_count'] == 2
    assert result['change_count'] == 1


def test_literal_boundaries_do_not_complete_a_phrase_match():
    source = 'delve `into` the API. leverage `the power of` a script.'
    assert humanize(source)['cleaned'] == source
    assert humanize(source)['changes'] == []


def test_protected_content_is_idempotent_and_needs_no_reserved_placeholder():
    source = '\x000\x00 `state-of-the-art` Delve into it.'
    once = humanize(source)
    twice = humanize(once['cleaned'])
    assert once['cleaned'] == '\x000\x00 `state-of-the-art` Explore it.'
    assert twice['cleaned'] == once['cleaned']
    assert twice['change_count'] == 0


def test_long_unmatched_backtick_runs_still_allow_prose_cleanup():
    # Different run lengths have no closing partner; exercise a large draft
    # without a timing assertion tied to one machine's speed.
    source = ' '.join('`' * n for n in range(1, 300)) + ' Delve into it.'
    assert humanize(source)['cleaned'] == source.replace('Delve into', 'Explore')


def test_cli_json_and_file_output_preserve_the_same_literals(tmp_path):
    source = 'Delve into it.\n\n```text\nstate-of-the-art\n```\n\n[guide](../state-of-the-art)\n'
    draft = tmp_path / 'draft.md'
    cleaned = tmp_path / 'cleaned.md'
    draft.write_text(source, encoding='utf-8')
    json_run = subprocess.run(
        [sys.executable, str(SCRIPTS / 'content_humanize.py'), str(draft), '--json'],
        check=True, text=True, capture_output=True,
    )
    file_run = subprocess.run(
        [sys.executable, str(SCRIPTS / 'content_humanize.py'), str(draft), '-o', str(cleaned)],
        check=True, text=True, capture_output=True,
    )
    result = json.loads(json_run.stdout)
    assert result == humanize(source)
    assert cleaned.read_text(encoding='utf-8') == source.replace('Delve into', 'Explore')
    assert '1 replacements' in file_run.stderr


def test_reference_definition_does_not_hide_indented_following_prose():
    source = '[id]: ../state-of-the-art\n  Delve into the prose.'
    assert humanize(source)['cleaned'] == '[id]: ../state-of-the-art\n  Explore the prose.'


def test_quoted_multiline_reference_label_and_destination_survive():
    source = (
        '> [guide][state-of-the-art guide]\n>\n'
        '> [state-of-the-art\n> guide]: ../state-of-the-art\n'
        '\nDelve into the prose.'
    )
    # Keep the reference source even with quote prefixes on its continuation.
    assert humanize(source)['cleaned'] == source.replace('Delve into the prose.', 'Explore the prose.')


@pytest.mark.parametrize("definition", [
    '[id]:\n  ../state-of-the-art\n  "Delve into this guide"',
    '[id]: ../state-of-the-art\n  "Delve into this\n  state-of-the-art guide"',
    "[id]: <../state-of-the-art guide> 'Delve into this\n guide'",
    '[id]: ../state-of-the-art (Delve into this\n guide)',
    '[id]: ../state-of-the-art/part_(one_(two_(three)))\n "Delve into this guide"',
])
def test_complete_multiline_reference_title_is_literal_and_following_prose_is_not(definition):
    source = definition + '\n\n[Delve into it][id]\n\nDelve into prose.'
    result = humanize(source)
    assert result['cleaned'] == definition + '\n\n[Delve into it][id]\n\nExplore prose.'
    assert result['change_count'] == 1


def test_blank_line_terminates_an_invalid_multiline_reference_title():
    source = '[id]: ../state-of-the-art\n "Delve into it\n\nstate-of-the-art guide"'
    assert humanize(source)['cleaned'] == (
        '[id]: ../state-of-the-art\n "Explore it\n\nmodern guide"'
    )


@pytest.mark.parametrize("with_reference", [False, True])
def test_large_nested_non_link_brackets_remain_prose(with_reference):
    prefix = '[id]: ../state-of-the-art\n\n' if with_reference else ''
    source = prefix + '[' * 16000 + 'Delve into it' + ']' * 16000
    assert humanize(source)['cleaned'] == prefix + '[' * 16000 + 'Explore it' + ']' * 16000
