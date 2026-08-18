"""Safe initial P10 adapters built around explicit, injected boundaries."""

from __future__ import annotations

from autplay.domain.import_identity import (
    IMPORT_ADAPTER_ID,
    IMPORT_ADAPTER_VERSION,
    MAX_IMPORT_BYTES,
    IdentityTrack,
    ImportEnvelope,
    ImportFormat,
    ParsedImport,
    extract_version_markers,
)
from autplay.ports.source_adapters import (
    AdapterLimits,
    CredentialRequirement,
    NetworkPolicy,
    PublicMetadataTransport,
    SourceAdapterManifest,
    SourceCandidate,
    SourceCapability,
)


class GenericUserExportSourceAdapter:
    """Offline CSV/JSON/HTML parser for explicitly supplied user exports."""

    manifest = SourceAdapterManifest(
        adapter_id=IMPORT_ADAPTER_ID,
        adapter_version=IMPORT_ADAPTER_VERSION,
        capabilities=(SourceCapability.IMPORT,),
        credential_requirement=CredentialRequirement.NONE,
        network_policy=NetworkPolicy.OFFLINE,
        limits=AdapterLimits(MAX_IMPORT_BYTES, 10_000, 0, 10.0),
    )

    def parse(self, payload: bytes, *, format_name: str, schema_version: str) -> ParsedImport:
        """Parse exact bytes; unknown format names fail before any side effect."""

        from autplay.domain.import_identity import parse_import

        try:
            import_format = ImportFormat(format_name.upper())
        except ValueError as error:
            from autplay.domain.import_identity import ImportEnvelopeError

            raise ImportEnvelopeError("import.format_unsupported") from error
        return parse_import(
            ImportEnvelope(
                import_format,
                payload,
                schema_version=schema_version,
                adapter_id=self.manifest.adapter_id,
                adapter_version=self.manifest.adapter_version,
            )
        )


class AuthorizedLocalMetadataSourceAdapter:
    """Normalize metadata from a previously authorized MediaStore/SAF selection.

    Raw filesystem paths and content URIs are intentionally not accepted. The
    Android/local ingest boundary owns authorization and byte access.
    """

    manifest = SourceAdapterManifest(
        adapter_id="autplay.authorized-local-metadata",
        adapter_version="1.0.0",
        capabilities=(SourceCapability.LOCAL_METADATA,),
        credential_requirement=CredentialRequirement.NONE,
        network_policy=NetworkPolicy.OFFLINE,
        limits=AdapterLimits(0, 10_000, 0, 2.0),
    )

    def normalize(self, metadata: dict[str, object]) -> IdentityTrack:
        """Build a path-free identity query from bounded local metadata."""

        forbidden = {"path", "raw_path", "uri", "content_uri", "source_url", "token"}
        if forbidden.intersection(key.casefold() for key in metadata):
            raise ValueError("local metadata contains a forbidden locator or credential")
        title = metadata.get("title")
        artist = metadata.get("artist")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("local metadata title is required")
        if not isinstance(artist, str) or not artist.strip():
            raise ValueError("local metadata artist is required")
        album = metadata.get("album")
        duration = metadata.get("duration_ms")
        if album is not None and not isinstance(album, str):
            raise ValueError("local metadata album must be text")
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int)):
            raise ValueError("local metadata duration must be an integer")
        markers = extract_version_markers(" ".join((title, album or "")))
        return IdentityTrack(
            title=title.strip(),
            artists=(artist.strip(),),
            album=album.strip() if album else None,
            duration_ms=duration,
            version_markers=markers,
        )


class GenericPublicMetadataSourceAdapter:
    """Bound a user-supplied public-only transport without choosing a provider."""

    manifest = SourceAdapterManifest(
        adapter_id="autplay.public-metadata-contract",
        adapter_version="1.0.0",
        capabilities=(SourceCapability.PUBLIC_METADATA,),
        credential_requirement=CredentialRequirement.NONE,
        network_policy=NetworkPolicy.PUBLIC_ALLOWLIST_ONLY,
        limits=AdapterLimits(0, 1, 20, 5.0),
    )

    def __init__(self, transport: PublicMetadataTransport) -> None:
        self._transport = transport

    def search(self, query: IdentityTrack, *, limit: int) -> tuple[SourceCandidate, ...]:
        """Return bounded public metadata with stable, credential-free provenance."""

        if not 1 <= limit <= self.manifest.limits.max_results_per_query:
            raise ValueError("public metadata result limit is invalid")
        results = self._transport.search(
            query,
            limit=limit,
            timeout_seconds=self.manifest.limits.timeout_seconds,
        )
        if len(results) > limit:
            raise ValueError("public metadata transport exceeded its declared result limit")
        return results


__all__ = (
    "AuthorizedLocalMetadataSourceAdapter",
    "GenericPublicMetadataSourceAdapter",
    "GenericUserExportSourceAdapter",
)
