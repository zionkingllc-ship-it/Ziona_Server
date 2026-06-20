import json

from django.test import RequestFactory

from core.shared.middleware import _graphql_operation_kind


def test_graphql_post_query_is_classified_as_query():
    request = RequestFactory().post(
        "/graphql/",
        data=json.dumps({"query": "query Viewer { me { id } }"}),
        content_type="application/json",
    )

    assert _graphql_operation_kind(request) == "query"


def test_graphql_post_mutation_is_classified_as_mutation():
    request = RequestFactory().post(
        "/graphql/",
        data=json.dumps({"query": 'mutation Update { updateBio(bio: "hello") { success } }'}),
        content_type="application/json",
    )

    assert _graphql_operation_kind(request) == "mutation"


def test_graphql_operation_name_selects_the_executed_operation():
    request = RequestFactory().post(
        "/graphql/",
        data=json.dumps(
            {
                "operationName": "ReadOnly",
                "query": """
                    query ReadOnly { me { id } }
                    mutation Write { updateBio(bio: "hello") { success } }
                """,
            }
        ),
        content_type="application/json",
    )

    assert _graphql_operation_kind(request) == "query"
