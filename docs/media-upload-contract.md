# Media Upload Contract

Ziona uses signed Google Cloud Storage URLs for browser uploads. The app asks the
backend for an upload URL, then uploads the file directly to GCS.

## GraphQL Mutation

```graphql
mutation UploadMedia($fileName: String!, $fileType: String!, $fileSize: Int!) {
  uploadMedia(fileName: $fileName, fileType: $fileType, fileSize: $fileSize) {
    success
    uploadUrl
    mediaId
    mediaUrl
    expiresIn
    error {
      code
      message
      field
    }
  }
}
```

## Browser Upload

Use the returned `uploadUrl` exactly as a one-time signed `PUT` target.

```ts
await fetch(uploadUrl, {
  method: "PUT",
  body: file,
  headers: {
    "Content-Type": file.type,
  },
});
```

Rules:

- Do not use the authenticated backend/Apollo client for the GCS `PUT`.
- Do not send the app `Authorization` header to `storage.googleapis.com`.
- The `Content-Type` header must exactly match the `fileType` sent to `uploadMedia`.
- Use the returned `mediaUrl` as the canonical public URL after upload.

## Bucket CORS

Browser-to-GCS uploads require CORS on the bucket itself. Django
`CORS_ALLOWED_ORIGINS` only controls browser-to-backend requests.

Preview the intended bucket CORS policy:

```bash
python manage.py configure_gcs_cors
```

Apply it:

```bash
python manage.py configure_gcs_cors --apply
```

The command reads origins from `GCS_CORS_ALLOWED_ORIGINS`, falling back to the
backend `CORS_ALLOWED_ORIGINS` list when unset.
