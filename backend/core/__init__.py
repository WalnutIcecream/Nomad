"""Cross-cutting infrastructure shared by every feature.

This package holds the code that does not belong to any single business
feature but that every feature needs: database access dependencies,
authentication plumbing, password/session hashing and snapshot blob storage.

Nothing here may import from ``backend.features`` (that would invert the
dependency direction and make the layering circular).
"""