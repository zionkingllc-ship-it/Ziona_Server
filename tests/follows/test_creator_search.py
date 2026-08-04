"""Creator search for the Discover screen.

The Discover search bar had no backend to call — there was no search query at
all. These cover matching, ranking, viewer-awareness and the guards that stop a
one-character query from scanning the whole user table.
"""

import pytest

from core.follows.models import Follow
from core.follows.services import FollowService


@pytest.fixture
def creators(create_user):
    return {
        "john": create_user(email="john@x.com", username="john", full_name="John Baptist"),
        "johnny": create_user(email="johnny@x.com", username="notjohnny", full_name="Ann Smith"),
        "mary": create_user(email="mary@x.com", username="mary", full_name="Mary Johnson"),
        "peter": create_user(email="peter@x.com", username="peter", full_name="Peter Rock"),
    }


def _usernames(result):
    return [c["user"].username for c in result["creators"]]


@pytest.mark.django_db
def test_search_matches_username_and_full_name(creators):
    result = FollowService.search_creators(query="john")

    found = set(_usernames(result))
    assert "john" in found  # username match
    assert "notjohnny" in found  # username contains
    assert "mary" in found  # full_name "Mary Johnson" contains
    assert "peter" not in found
    assert result["total_count"] == 3


@pytest.mark.django_db
def test_exact_username_ranks_first(creators):
    result = FollowService.search_creators(query="john")

    # "john" (exact handle) must beat "notjohnny" and the full-name match.
    assert _usernames(result)[0] == "john"


@pytest.mark.django_db
def test_search_is_case_insensitive(creators):
    assert "john" in _usernames(FollowService.search_creators(query="JOHN"))
    assert "mary" in _usernames(FollowService.search_creators(query="jOhNsOn"))


@pytest.mark.django_db
def test_short_query_returns_nothing_instead_of_scanning(creators):
    result = FollowService.search_creators(query="j")

    assert result["creators"] == []
    assert result["total_count"] == 0
    assert result["has_more"] is False


@pytest.mark.django_db
def test_blank_query_returns_nothing(creators):
    assert FollowService.search_creators(query="   ")["creators"] == []
    assert FollowService.search_creators(query="")["creators"] == []


@pytest.mark.django_db
def test_viewer_is_excluded_from_own_results(creators):
    viewer = creators["john"]

    result = FollowService.search_creators(query="john", viewer_id=str(viewer.id))

    assert "john" not in _usernames(result)
    assert "notjohnny" in _usernames(result)


@pytest.mark.django_db
def test_is_following_reflects_the_viewers_graph(creators):
    viewer = creators["peter"]
    Follow.objects.create(follower=viewer, following=creators["john"])

    result = FollowService.search_creators(query="john", viewer_id=str(viewer.id))
    flags = {c["user"].username: c["is_following"] for c in result["creators"]}

    assert flags["john"] is True
    assert flags["notjohnny"] is False


@pytest.mark.django_db
def test_deleted_and_suspended_creators_are_excluded(create_user, creators):
    deleted = create_user(email="del@x.com", username="johndeleted")
    deleted.deleted_at = "2026-01-01T00:00:00+00:00"
    deleted.save(update_fields=["deleted_at"])
    suspended = create_user(email="sus@x.com", username="johnsuspended", status="suspended")

    found = _usernames(FollowService.search_creators(query="john"))

    assert suspended.username not in found
    assert deleted.username not in found


@pytest.mark.django_db
def test_pagination_reports_has_more_and_slices(creators):
    first = FollowService.search_creators(query="john", page=1, page_size=2)
    second = FollowService.search_creators(query="john", page=2, page_size=2)

    assert len(first["creators"]) == 2
    assert first["has_more"] is True
    assert first["total_count"] == 3
    assert len(second["creators"]) == 1
    assert second["has_more"] is False
    # No overlap between pages.
    assert not set(_usernames(first)) & set(_usernames(second))


@pytest.mark.django_db
def test_page_size_is_capped(creators):
    result = FollowService.search_creators(query="john", page_size=500)
    assert result["page_size"] == 50


@pytest.mark.django_db
def test_search_creators_graphql_query(create_user, client):
    """The Discover search bar's actual entry point."""
    import json

    create_user(email="graph@x.com", username="graphjohn", full_name="Graph John")

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query ($q: String!) {
                  searchCreators(query: $q) {
                    creators { id username bio isFollowing stats { followersCount } }
                    totalCount
                    page
                    pageSize
                    hasMore
                  }
                }
                """,
                "variables": {"q": "graphjohn"},
            }
        ),
        content_type="application/json",
    )

    body = json.loads(response.content)
    assert "errors" not in body, body.get("errors")
    payload = body["data"]["searchCreators"]
    assert payload["totalCount"] == 1
    assert payload["creators"][0]["username"] == "graphjohn"
    assert payload["creators"][0]["isFollowing"] is False
    assert payload["hasMore"] is False
