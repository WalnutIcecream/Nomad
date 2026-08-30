# Nomad API Specification

Versioned under `/api/v1`. All endpoints except `/auth/register` and
`/auth/login` require `Authorization: Bearer <session_token>`.

The caller's `user_id` is always taken from the validated session token — never
from a request body.

## Endpoints

```text
POST   /auth/register                     {username, password}          — implemented
POST   /auth/login                        {username, password} -> {token, user}

GET    /worlds                            -> [{id, name, status, latest_version, current_host, member_count}]
POST   /worlds                            {name, minecraft_version} -> World

GET    /worlds/:id                        -> World detail (members only)
GET    /worlds/:id/status                 -> {status, current_host, latest_version}

POST   /worlds/:id/host/acquire           -> {lease_id, host_user_id} | {current_host} (already hosted)
POST   /worlds/:id/host/heartbeat         {lease_id} -> 200 | 409 lease expired
POST   /worlds/:id/host/release           {lease_id} -> 200

POST   /worlds/:id/snapshot/prepare       {lease_id, base_version} -> {upload_id, upload_url}
POST   /worlds/:id/snapshot/complete      {upload_id, lease_id, metadata} -> {version_number}

GET    /worlds/:id/versions               -> [{version_number, created_by, created_at, minecraft_version}]
POST   /worlds/:id/restore                {version_number} -> {download_url}

GET    /worlds/:id/members                -> [{user_id, username, role}]
POST   /worlds/:id/members                {user_id} -> 200 (owner only)
DELETE /worlds/:id/members/:user          -> 200 (owner only)

GET    /worlds/:id/connection             -> {world_id, connection} (members)
PUT    /worlds/:id/connection             {mode, address|relay_token, ...} -> 200 (current host only)
POST   /worlds/:id/connection/relay-token -> {token, relay_host, relay_port} (current host only)
```

All endpoints are implemented and tested. Snapshot upload is a real streaming
`PUT` to `/worlds/:id/snapshot/:upload_id`; download is a real `GET` to
`/worlds/:id/versions/:n/download`. Blobs live in the controller's on-disk
store (`NOMAD_BLOB_DIR`); an object-storage backend can replace it without
changing the launcher-facing API. Connection info lets members learn how to
reach the active host (direct address or relay token).

## Authorization

- Every world endpoint requires the caller to be in `world_members` for that world.
- Owner-only operations are enforced server-side.
- Launcher never receives GitHub credentials; uploads/downloads are backend-mediated.

## Error Codes

| Code | Meaning |
|---|---|
| 401 | Missing/invalid session token |
| 403 | Not a member / not owner |
| 409 | Lease expired, version conflict, or world not in expected state |
| 404 | Unknown world, version, or member |
