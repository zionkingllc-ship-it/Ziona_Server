"""GraphQL contract snapshot — guards the mobile/admin schema against breaking changes.

The fixture is the full SDL, lexicographically sorted so type/field ORDER never
matters (GraphQL is name-addressed) — only renames, removals, and type changes
fail. Refactors that reorganize Python modules must keep this byte-identical.

If this test fails because you INTENTIONALLY added a field (additive, safe),
regenerate the fixture:

    python -c "
    import django, os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
    django.setup()
    from graphql import build_schema
    from graphql.utilities import lexicographic_sort_schema, print_schema
    from config.graphql_schema import schema
    open('tests/graphql/schema_contract.graphql', 'w').write(
        print_schema(lexicographic_sort_schema(build_schema(schema.as_str()))))
    "

NEVER regenerate to paper over a removed/renamed field — that breaks the mobile app.
"""

from pathlib import Path

from graphql import build_schema
from graphql.utilities import lexicographic_sort_schema, print_schema

FIXTURE = Path(__file__).parent / "schema_contract.graphql"


def _current_sorted_sdl() -> str:
    from config.graphql_schema import schema

    # Round-trip through SDL text: strawberry's printer handles its enum
    # defaults; the re-parsed pure-SDL schema can then be sorted semantically.
    return print_schema(lexicographic_sort_schema(build_schema(schema.as_str())))


def test_graphql_schema_matches_contract_snapshot():
    assert _current_sorted_sdl() == FIXTURE.read_text(), (
        "The GraphQL schema no longer matches tests/graphql/schema_contract.graphql. "
        "If the change is intentional and ADDITIVE, regenerate the fixture (see module "
        "docstring). If a field was removed or renamed, this is a BREAKING change for "
        "the mobile app — coordinate with Prime before proceeding."
    )
