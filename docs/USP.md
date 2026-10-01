# Unique selling points

- **No server to run.** Nobody keeps a process alive. The object store decides who hosts.
- **No accounts.** A world ID plus store access is the whole access model. Sharing the ID invites someone.
- **Free.** R2 free tier (10 GB-month, 1M writes, 10M reads, $0 egress). Or your own server, with no per-op bill at all.
- **First in wins.** Atomic lease. Two people pressing Play in the same second cannot both host.
- **Crash-safe.** The lease expires on its own. A dead host frees the world in 5 minutes.
- **Version-agnostic.** Works with any Minecraft server jar; downloaded at runtime.
- **Any game, not just Minecraft.** A `nomad.json` pointer file lists the save
  files to sync and the command to launch; anything with a save directory works.
- **Portable.** Frozen build ships with an embedded Java runtime. Friends install nothing.
- **Two clear choices.** Cloudflare R2 for a hosted bucket, or your own
  S3-compatible server (Garage) if you would rather keep the world on your
  hardware. A wizard tests the connection before saving.
- **Integrity-checked.** Every world blob has a SHA-256; pulls reject corrupted or tampered data.
