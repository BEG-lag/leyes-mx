from leyes_mx.site import _replace_block, changes_md, history_block, laws_table, stats_line

DATA = {
    "generated": "2026-10-07",
    "groups": {"constitucion": "Constitución", "energia": "Energía"},
    "laws": [
        {
            "slug": "cpeum",
            "group": "constitucion",
            "title": "Constitución Política de los Estados Unidos Mexicanos",
            "abbreviation": "CPEUM",
            "published": "1917-02-05",
            "last_reform": "2026-10-07",
            "articles": 136,
            "decrees": 285,
            "versions": 44,
            "history_since": "2006-04-07",
        },
        {
            "slug": "lse",
            "group": "energia",
            "title": "Ley del Sector Eléctrico",
            "abbreviation": "LSE",
            "published": "2025-03-18",
            "last_reform": "",
            "articles": 188,
            "decrees": 0,
            "versions": 1,
            "history_since": "2025-03-18",
        },
    ],
    "recent": [
        {
            "date": "2026-10-07",
            "slug": "cpeum",
            "abbreviation": "CPEUM",
            "title": "DECRETO por el que se reforman los artículos 82 | 116.",
            "pdf": "https://x/285.pdf",
        }
    ],
    "totals": {
        "laws": 2,
        "articles": 324,
        "decrees": 285,
        "versions": 45,
        "laws_with_history": 1,
        "earliest_version": "2006-04-07",
    },
}


def test_laws_table_in_both_languages():
    en = laws_table(DATA, "en")
    assert (
        "| [Constitución Política de los Estados Unidos Mexicanos](leyes/cpeum.md) | CPEUM | 05-02-1917 | 07-10-2026 | 136 |"
        in en
    )
    assert "| 18-03-2025 | none | 188 |" in en
    assert "sin reforma" in laws_table(DATA, "es")


def test_stats_and_history_blocks_use_the_totals():
    assert stats_line(DATA).startswith("**2 laws** · **324 articles** · **285 reform decrees**")
    assert "back to 2006" in stats_line(DATA)
    block = history_block(DATA)
    assert "**45 versions**" in block and "| 44 | 2006-04-07 |" in block
    assert "lse" not in block.split("| Law |")[1]


def test_changes_escapes_pipes_and_links_decrees():
    md = changes_md(DATA)
    assert "[07-10-2026](https://x/285.pdf)" in md and "82 \\| 116." in md


def test_replace_block_keeps_markers():
    text = "a\n<!-- x:start -->\nold\n<!-- x:end -->\nb"
    assert _replace_block(text, "x", "new\n") == "a\n<!-- x:start -->\nnew\n<!-- x:end -->\nb"
