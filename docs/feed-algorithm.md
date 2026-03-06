# Feed Algorithm

## Overview

Ziona uses three feed strategies: **For You**, **Following**, and **Discover**.

## For You Feed

### New Users (< 7 days)
- Filters by user's selected interest categories
- Ranks by **engagement score**: `likes + (comments × 2) + (shares × 3)`
- Falls back to chronological if no interests set

### Returning Users
- Mixes content from followed creators (weighted higher) with discovery content
- Followed creators' posts appear first, then chronological fallback
- Uses `is_following` annotation for priority ordering

## Following Feed
- **Chronological** posts from followed users
- Empty state shows suggested creators (interest-based + follower count)
- Cursor-based pagination using `created_at`

## Discover Feed
- All non-own posts, optionally filtered by `PostCategory`
- Ordered chronologically
- Supports category parameter for browsing specific content types

## Caching Strategy

| Cache Key                   | TTL   | Invalidated By           |
|-----------------------------|-------|--------------------------|
| `feed:following:{user_id}`  | 5 min | New post by followed user|
| `feed:for_you:{user_id}`    | 5 min | Post create/delete       |
| `followers:{user_id}`       | 5 min | Follow/unfollow          |
| `following:{user_id}`       | 5 min | Follow/unfollow          |
| `is_following:{a}:{b}`      | 5 min | Follow/unfollow          |

## Pagination
All feeds use **cursor-based pagination** with post IDs as cursors. The `created_at` timestamp of the cursor post determines the page boundary.
