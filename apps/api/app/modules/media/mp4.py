"""Read an MP4's duration from its `moov/mvhd` box with a few small ranged reads (the local video
provider's stand-in for a transcoder's metadata). `moov` may come before or after `mdat`, so the
top-level boxes are walked by their headers without reading the media data."""

import struct
from collections.abc import Callable

ReadRange = Callable[[int, int], bytes]  # (offset, length) -> bytes

_MAX_BOXES = 64
_MAX_MOOV_BYTES = 16 * 1024 * 1024


class NotAnMp4Error(ValueError):
    pass


def _box_header(data: bytes) -> tuple[int, bytes, int]:
    """(box size, type, header length). Size 0 means "to the end of the file"."""
    if len(data) < 8:  # noqa: PLR2004
        raise NotAnMp4Error("truncated box header")
    size, kind = struct.unpack(">I4s", data[:8])
    if size == 1:
        if len(data) < 16:  # noqa: PLR2004
            raise NotAnMp4Error("truncated large box header")
        return struct.unpack(">Q", data[8:16])[0], kind, 16
    return size, kind, 8


def _mvhd_duration(moov: bytes) -> float:
    offset = 0
    while offset + 8 <= len(moov):
        size, kind, header = _box_header(moov[offset : offset + 16])
        if size < header:
            break
        if offset + size > len(moov):
            raise NotAnMp4Error("truncated movie box")
        if kind == b"mvhd":
            body = moov[offset + header : offset + size]
            if not body or body[0] not in (0, 1):
                raise NotAnMp4Error("unsupported movie header")
            version = body[0]
            if len(body) < (32 if version == 1 else 20):
                raise NotAnMp4Error("truncated movie header")
            if version == 1:
                timescale, duration = struct.unpack(">IQ", body[20:32])
            else:
                timescale, duration = struct.unpack(">II", body[12:20])
            if timescale == 0:
                raise NotAnMp4Error("zero timescale")
            if not 0 < duration / timescale <= 86400:  # noqa: PLR2004
                raise NotAnMp4Error("duration must be between zero and 24 hours")
            return float(duration) / float(timescale)
        offset += size
    raise NotAnMp4Error("no mvhd box")


def mp4_duration_seconds(read: ReadRange, file_size: int) -> float:
    """Duration in seconds; raises NotAnMp4Error for anything that isn't an MP4 with a movie
    header."""
    offset, seen_ftyp = 0, False
    for _ in range(_MAX_BOXES):
        if offset + 8 > file_size:
            break
        size, kind, header = _box_header(read(offset, min(16, file_size - offset)))
        if offset == 0:
            seen_ftyp = kind == b"ftyp"
            if not seen_ftyp:
                raise NotAnMp4Error("missing ftyp box")
        if size == 0:
            size = file_size - offset
        if size < header:
            raise NotAnMp4Error("invalid box size")
        if offset + size > file_size:
            raise NotAnMp4Error("truncated box")
        if kind == b"moov":
            if size > _MAX_MOOV_BYTES:
                raise NotAnMp4Error("moov box too large")
            return _mvhd_duration(read(offset + header, size - header))
        offset += size
    raise NotAnMp4Error("no moov box")
