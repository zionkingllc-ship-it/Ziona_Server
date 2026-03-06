# API Reference — Milestone 2

All endpoints are exposed via the GraphQL API at `/graphql/`.

## Queries

| Query | Args | Returns | Auth |
|-------|------|---------|------|
| `forYouFeed` | cursor?, limit? | `FeedResponse` | ✅ |
| `followingFeed` | cursor?, limit? | `FeedResponse` | ✅ |
| `discoverFeed` | category?, cursor?, limit? | `FeedResponse` | ✅ |
| `postComments` | postId, cursor?, limit? | `CommentsResponse` | Optional |
| `bookmarkFolders` | — | `[BookmarkFolderType]` | ✅ |
| `friendsList` | search?, limit? | `[FriendType]` | ✅ |
| `followers` | userId, cursor?, limit? | `FollowListResponse` | Optional |
| `following` | userId, cursor?, limit? | `FollowListResponse` | Optional |
| `suggestedCreators` | limit? | `[SuggestedCreatorType]` | ✅ |
| `userProfile` | userId | `UserProfileType` | Optional |
| `listReports` | status?, cursor?, limit? | `ReportListResponse` | ✅ Admin |

## Mutations

| Mutation | Args | Returns | Auth |
|----------|------|---------|------|
| `createPost` | postType, caption?, category?, mediaUrls?, ... | `PostPayload` | ✅ |
| `updatePost` | postId, caption? | `PostPayload` | ✅ |
| `deletePost` | postId | `PostPayload` | ✅ |
| `likePost` | postId | `LikePayload` | ✅ |
| `unlikePost` | postId | `LikePayload` | ✅ |
| `createComment` | postId, text, parentCommentId? | `CommentPayload` | ✅ |
| `deleteComment` | commentId | `CommentPayload` | ✅ |
| `likeComment` | commentId | `LikePayload` | ✅ |
| `savePost` | postId, folderId? | `SavePayload` | ✅ |
| `unsavePost` | postId | `SavePayload` | ✅ |
| `createBookmarkFolder` | name, icon? | `BookmarkFolderPayload` | ✅ |
| `deleteBookmarkFolder` | folderId | `BookmarkFolderPayload` | ✅ |
| `sharePostDirect` | postId, recipientId | `SharePayload` | ✅ |
| `sharePostExternal` | postId | `SharePayload` | ✅ |
| `followUser` | userId | `FollowPayload` | ✅ |
| `unfollowUser` | userId | `FollowPayload` | ✅ |
| `updateProfile` | bio?, fullName?, avatarUrl?, location? | `ProfilePayload` | ✅ |
| `setInterests` | interests: [String!]! | `SetInterestsPayload` | ✅ |
| `reportContent` | reason, postId?, commentId?, description? | `ReportPayload` | ✅ |
| `reviewReport` | reportId, status | `ReportPayload` | ✅ Admin |

## REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/.well-known/apple-app-site-association` | iOS Universal Links |
| GET | `/.well-known/assetlinks.json` | Android App Links |
| GET | `/post/<post_id>/` | Share preview (OG tags) |
