"""Chat attachment HTTP helpers — thin wrappers over :mod:`inbound_store`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from octop.infra.gateway.media.attachment_hints import is_image_media_type
from octop.infra.gateway.media.inbound_store import InboundFile, write_inbound, write_inbound_stream

if TYPE_CHECKING:
    from harness_agent.backends.workspace import BackendWorkspace

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
# Bio input files (fastq/bam/vcf…) get a dedicated chunked path with a much
# higher ceiling; regular chat attachments stay at MAX_ATTACHMENT_BYTES.
MAX_BIO_ATTACHMENT_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class StoredAttachment:
    filename: str
    media_type: str
    size: int
    data_path: str
    owner_id: int = 0


def _stored_from_inbound(inbound: InboundFile, *, owner_id: int = 0) -> StoredAttachment:
    return StoredAttachment(
        owner_id=owner_id,
        filename=inbound.filename,
        media_type=inbound.media_type,
        size=inbound.size,
        data_path=inbound.path,
    )


def dashboard_inbound_preview_url(
    agent_id: str,
    workspace_path: str,
    *,
    media_type: str = "",
) -> str:
    """JWT-protected preview/download URL for dashboard UI (not for LLM)."""
    rel = workspace_path.lstrip("/")
    aid = quote(agent_id, safe="")
    if is_image_media_type(media_type) or media_type.startswith("video/"):
        params = f"source={quote(rel, safe='')}"
        if media_type:
            params += f"&mime_type={quote(media_type, safe='')}"
        return f"/api/agents/{aid}/media/preview?{params}"
    return f"/api/agents/{aid}/workspace/download?path={quote(rel, safe='')}"


async def save_attachment(
    workspace: BackendWorkspace,
    *,
    owner_id: int,
    filename: str,
    media_type: str,
    data: bytes,
) -> StoredAttachment:
    del owner_id  # access control is JWT + agent scope at download time
    inbound = await write_inbound(
        workspace,
        data,
        filename=filename,
        media_type=media_type,
    )
    return _stored_from_inbound(inbound)


async def save_bio_attachment(
    workspace: BackendWorkspace,
    *,
    owner_id: int,
    filename: str,
    media_type: str,
    chunks: AsyncIterator[bytes],
) -> StoredAttachment:
    """Stream a large bio input file into ``inbound/`` (chunked, up to 2GB)."""
    del owner_id  # access control is JWT + agent scope at download time
    inbound = await write_inbound_stream(
        workspace,
        chunks,
        filename=filename,
        media_type=media_type,
        max_bytes=MAX_BIO_ATTACHMENT_BYTES,
    )
    return _stored_from_inbound(inbound)
