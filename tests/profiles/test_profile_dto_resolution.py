from core.profiles.schema import _dto_to_profile
from core.shared.dtos import (
    AuthorDTO,
    PostResponseDTO,
    StatsDTO,
    TextMediaDTO,
    UserProfileDTO,
    UserProfileStatsDTO,
)


def test_dto_to_profile_conversion():
    """Test standard DTO to GraphQL type conversion."""
    stats = UserProfileStatsDTO(followers_count=1200, following_count=500, posts_count=10)

    dto = UserProfileDTO(
        id="user-123",
        username="testuser",
        full_name="Test User",
        bio="Test Bio",
        avatar_url=None,
        location="Test Location",
        stats=stats,
        is_following=False,
        is_own_profile=True,
        recent_posts=[],
        created_at="2024-01-01T00:00:00Z",
    )

    gql = _dto_to_profile(dto)

    assert gql.id == "user-123"
    # Verify formatted stats
    assert gql.stats.followers_count() == "1.2k"
    assert gql.stats.following_count() == "500"
    assert gql.stats.posts_count() == "10"


def test_dto_to_profile_text_post_fix():
    """Verify that text posts in recent_posts do not trigger AttributeError."""
    author = AuthorDTO(id="user-123", username="testuser")

    # Text post has no media.url
    text_post_dto = PostResponseDTO(
        id="post-456",
        type="text",
        created_at="2024-01-01T00:00:00Z",
        caption="Sample text",
        author=author,
        media=TextMediaDTO(),
        stats=StatsDTO(),
        share_url="http://test.com/post/456",
    )

    stats = UserProfileStatsDTO(followers_count=0, following_count=0, posts_count=1)

    dto = UserProfileDTO(
        id="user-123",
        username="testuser",
        stats=stats,
        recent_posts=[text_post_dto],
        created_at="2024-01-01T00:00:00Z",
    )

    gql = _dto_to_profile(dto)

    assert len(gql.recent_posts) == 1
    assert gql.recent_posts[0].id == "post-456"
    assert gql.recent_posts[0].post_type.value == "TEXT"
    assert gql.recent_posts[0].text_message() == "Sample text"
