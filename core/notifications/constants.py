"""Constants for the notifications app."""

NOTIFICATION_TEMPLATES = {
    "reply_comment": "{username} replied to your comment",
    "reply_post": "{username} replied to your post",
    "like_post": "{username} liked your post",
    "like_comment": "{username} liked your comment",
    "new_anchor": "New anchor published in {circle_name}",
    "mention": "{username} mentioned you in a comment",
    "new_circle_post": "New post in {circle_name}",
    "new_follower": "{username} started following you",
    "admin_announcement": "{message}",
    # Anchor responses reuse the reply/like notification types, so their wording
    # is keyed separately — "liked your comment" is wrong for a reflection.
    "reply_anchor_response": "{username} replied to your reflection",
    "reaction_anchor_response": "{username} reacted to your reflection",
}

BATCHED_LIKE_TEMPLATES = {
    "like_post": "{username} and {others_count} others liked your post",
    "like_comment": "{username} and {others_count} others liked your comment",
    "reaction_anchor_response": ("{username} and {others_count} others reacted to your reflection"),
}


class ErrorCodes:
    NOTIFICATION_NOT_FOUND = "NOTIFICATION_NOT_FOUND"
    INVALID_DEVICE_TOKEN = "INVALID_DEVICE_TOKEN"
    PREFERENCE_UPDATE_FAILED = "PREFERENCE_UPDATE_FAILED"
    FIREBASE_SEND_FAILED = "FIREBASE_SEND_FAILED"
    DUPLICATE_NOTIFICATION = "DUPLICATE_NOTIFICATION"
    UNAUTHORIZED_ADMIN_ACTION = "UNAUTHORIZED_ADMIN_ACTION"
