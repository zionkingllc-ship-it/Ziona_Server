"""displayName: the name the app shows for a person, everywhere a person appears.

Rule (core.users.models.compose_display_name): the person's name, trimmed, or
their @username when they have not set one — so it is never empty. About a
quarter of live creators have no name (email sign-up does not ask for one).
"""

import json

import pytest
from django.test import Client

from core.users.models import User, compose_display_name

PASSWORD = "Display12345!"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("full_name", "username", "expected"),
    [
        ("Grace Adeyemi", "grace", "Grace Adeyemi"),
        ("  Grace Adeyemi \t", "grace", "Grace Adeyemi"),
        ("", "grace", "grace"),
        ("   ", "grace", "grace"),
        (None, "grace", "grace"),
        ("Brian❤️", "brian0730", "Brian❤️"),
        (None, None, ""),
    ],
)
def test_compose_display_name(full_name, username, expected):
    assert compose_display_name(full_name, username) == expected


# ---------------------------------------------------------------------------
# Every surface, through the real API
# ---------------------------------------------------------------------------


@pytest.fixture
def people(create_user):
    from core.circles.models import Circle, CircleMembership, CirclePost
    from core.engagement.models import Comment
    from core.follows.models import Follow
    from core.notifications.models import Notification
    from core.posts.models import Post

    viewer = create_user(email="viewer@dn.test", username="viewer")
    named = create_user(email="named@dn.test", username="grace")
    unnamed = create_user(email="unnamed@dn.test", username="nathaniel")
    # Stored the way legacy rows are: untrimmed. Only the API output is trimmed.
    User.objects.filter(id=named.id).update(full_name="  Grace Adeyemi ")
    User.objects.filter(id=unnamed.id).update(full_name="")

    posts = {}
    for author in (named, unnamed):
        post = Post.objects.create(user=author, post_type="text", caption=f"by {author.username}")
        Comment.objects.create(post=post, user=author, text="Amen")
        Notification.objects.create(
            user=viewer,
            sender=author,
            notification_type="like_post",
            reference_type="post",
            reference_id=post.id,
            message="liked your post",
        )
        Follow.objects.create(follower=author, following=viewer)
        Follow.objects.create(follower=viewer, following=author)
        posts[author.username] = post

    circle = Circle.objects.create(name="Prayer Warriors", description="x")
    for member in (viewer, named, unnamed):
        CircleMembership.objects.create(circle=circle, user=member, role="member")
    for author in (named, unnamed):
        CirclePost.objects.create(circle=circle, user=author, text=f"circle by {author.username}")

    return {
        "viewer": viewer,
        "named": named,
        "unnamed": unnamed,
        "posts": posts,
        "circle": circle,
        "expected": {str(named.id): "Grace Adeyemi", str(unnamed.id): "nathaniel"},
    }


def _client(user) -> Client:
    from core.authentication.tokens import TokenService

    client = Client()
    token = TokenService.generate_access_token(str(user.id), user.role)
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client


def _query(client, query: str, variables: dict | None = None) -> dict:
    response = client.post(
        "/graphql/",
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
    )
    body = response.json()
    assert not body.get("errors"), body["errors"]
    return body["data"]


def _assert_names(rows, expected: dict):
    """rows: (id, displayName) pairs; each known author must show the right name."""
    seen = {person_id: name for person_id, name in rows if person_id in expected}
    assert seen == expected, seen


def test_feed_authors(people):
    data = _query(
        _client(people["viewer"]),
        "{ forYouFeed(limit: 20) { posts { author { id username displayName } } } }",
    )
    rows = [(p["author"]["id"], p["author"]["displayName"]) for p in data["forYouFeed"]["posts"]]
    _assert_names(rows, people["expected"])


def test_discover_feed_authors(people):
    data = _query(
        _client(people["viewer"]),
        "{ discoverFeed(limit: 20) { posts { author { id displayName } } } }",
    )
    rows = [(p["author"]["id"], p["author"]["displayName"]) for p in data["discoverFeed"]["posts"]]
    _assert_names(rows, people["expected"])


@pytest.mark.parametrize("author", ["named", "unnamed"])
def test_comment_authors(people, author):
    post = people["posts"][people[author].username]
    data = _query(
        _client(people["viewer"]),
        "query($p: String!) { postComments(postId: $p) { comments { user { id displayName } } } }",
        {"p": str(post.id)},
    )
    rows = [(c["user"]["id"], c["user"]["displayName"]) for c in data["postComments"]["comments"]]
    person_id = str(people[author].id)
    _assert_names(rows, {person_id: people["expected"][person_id]})


def test_notification_senders(people):
    data = _query(
        _client(people["viewer"]),
        "{ notifications(limit: 20) { items { user { id displayName } } } }",
    )
    rows = [
        (n["user"]["id"], n["user"]["displayName"])
        for n in data["notifications"]["items"]
        if n["user"]
    ]
    _assert_names(rows, people["expected"])


def test_followers_list(people):
    data = _query(
        _client(people["viewer"]),
        "query($u: String!) { followers(userId: $u) { users { id displayName } } }",
        {"u": str(people["viewer"].id)},
    )
    rows = [(u["id"], u["displayName"]) for u in data["followers"]["users"]]
    _assert_names(rows, people["expected"])


def test_friends_list(people):
    data = _query(_client(people["viewer"]), "{ friendsList { id displayName fullName } }")
    rows = [(f["id"], f["displayName"]) for f in data["friendsList"]]
    _assert_names(rows, people["expected"])


def test_suggested_creators_for_a_guest(people):
    data = _query(Client(), "{ suggestedCreators(limit: 10) { id displayName } }")
    rows = [(s["id"], s["displayName"]) for s in data["suggestedCreators"]]
    _assert_names(rows, people["expected"])


@pytest.mark.parametrize("term", ["grace", "nathaniel"])
def test_creator_search(people, term):
    data = _query(
        _client(people["viewer"]),
        "query($q: String!) { searchCreators(query: $q) { creators { id displayName } } }",
        {"q": term},
    )
    rows = [(c["id"], c["displayName"]) for c in data["searchCreators"]["creators"]]
    person = people["named"] if term == "grace" else people["unnamed"]
    _assert_names(rows, {str(person.id): people["expected"][str(person.id)]})


@pytest.mark.parametrize("author", ["named", "unnamed"])
def test_profile(people, author):
    person = people[author]
    data = _query(
        _client(people["viewer"]),
        "query($u: String!) { userProfile(userId: $u) { displayName fullName } }",
        {"u": str(person.id)},
    )
    assert data["userProfile"]["displayName"] == people["expected"][str(person.id)]


def test_circle_post_authors_and_their_existing_name_field_agree(people):
    data = _query(
        _client(people["viewer"]),
        "query($c: String!) { circlePosts(circleId: $c) { posts { user { id name displayName } } } }",
        {"c": str(people["circle"].id)},
    )
    users = [p["user"] for p in data["circlePosts"]["posts"]]
    _assert_names([(u["id"], u["displayName"]) for u in users], people["expected"])
    assert all(u["name"] == u["displayName"] for u in users)


def test_me_keeps_its_raw_display_name_and_profile_gets_the_resolved_one(people):
    """me.displayName predates this field and is the raw, editable name — unchanged.

    The edit-profile form relies on it being empty when no name is set; the
    resolved, never-empty value is me.profile.displayName.
    """
    data = _query(
        _client(people["unnamed"]),
        "{ me { username displayName profile { displayName shareUrl } } }",
    )
    assert data["me"]["displayName"] in ("", None)
    assert data["me"]["profile"]["displayName"] == "nathaniel"
    assert data["me"]["profile"]["shareUrl"].endswith(f"/profile/{people['unnamed'].id}")


# ---------------------------------------------------------------------------
# Names are trimmed where they are saved
# ---------------------------------------------------------------------------


def test_profile_edit_trims_and_a_whitespace_only_change_spends_no_cooldown(create_user):
    from core.profiles.services import ProfileService

    user = create_user(email="trim@dn.test", username="trimmer")
    ProfileService.update_profile(str(user.id), full_name="  Ama Owusu  ")
    user.refresh_from_db()
    assert user.full_name == "Ama Owusu"
    first_change = user.last_name_change

    ProfileService.update_profile(str(user.id), full_name="Ama Owusu   ")  # no real change

    user.refresh_from_db()
    assert user.last_name_change == first_change


def test_google_sign_up_trims_the_name(settings):
    from unittest.mock import patch

    from django.urls import reverse

    settings.GOOGLE_CLIENT_IDS = ["test-client.apps.googleusercontent.com"]
    with patch("google.oauth2.id_token.verify_oauth2_token") as verify:
        verify.return_value = {
            "aud": "test-client.apps.googleusercontent.com",
            "email": "spaced@dn.test",
            "sub": "google-spaced",
            "email_verified": True,
            "name": " Uchenna Chukwulebe ",
        }
        Client().post(
            reverse("authentication:google-oauth"),
            data=json.dumps({"id_token": "token"}),
            content_type="application/json",
        )

    assert User.objects.get(email="spaced@dn.test").full_name == "Uchenna Chukwulebe"


def test_migration_trims_existing_names(create_user):
    from importlib import import_module

    from django.apps import apps

    spaced = create_user(email="legacy@dn.test", username="legacy")
    clean = create_user(email="clean@dn.test", username="clean")
    User.objects.filter(id=spaced.id).update(full_name=" Chinenye Jessica Kanu ")
    User.objects.filter(id=clean.id).update(full_name="Debby Olayiwola")
    migration = import_module("core.users.migrations.0014_trim_full_names")

    migration.trim_full_names(apps, None)

    assert User.objects.get(id=spaced.id).full_name == "Chinenye Jessica Kanu"
    assert User.objects.get(id=clean.id).full_name == "Debby Olayiwola"
