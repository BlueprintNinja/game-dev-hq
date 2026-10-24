"""Bounded, read-only Git commit resolution, with no process/config/hook access.

Only local object storage is consulted. No refs, credentials, alternates,
promisor fetches, replace refs or Git configuration are used. Unsupported or
corrupt storage fails closed. Pack v2 indexes and pack v2/v3 objects are read
directly, including deltas; final content is checked against the OID.
"""

import hashlib
import re
import struct
import zlib
from pathlib import Path

from .verified_evidence import EvidenceRefused

MAX_OBJECT = 2_097_152
MAX_INDEX = 67_108_864
MAX_PACKS = 128
MAX_DEPTH = 32


def _local(path):
    path = Path(path)
    if str(path).startswith(("\\\\", "//")) or not path.is_absolute():
        raise EvidenceRefused("revision_repository_unavailable")
    resolved = path.resolve(strict=True)
    if str(resolved).startswith(("\\\\", "//")):
        raise EvidenceRefused("revision_repository_unavailable")
    return resolved


def _read(path, limit):
    with _local(path).open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise EvidenceRefused("revision_storage_limit")
    return value


def _objects(repository):
    root = _local(repository)
    metadata = root / ".git"
    if metadata.is_file():
        link = _read(metadata, 4096).decode("utf-8").strip()
        if not link.startswith("gitdir: "):
            raise ValueError
        metadata = _local(root / link[8:])
    elif not metadata.is_dir():
        metadata = root
    common = metadata / "commondir"
    if common.is_file():
        metadata = _local(metadata / _read(common, 4096).decode("utf-8").strip())
    if not (metadata / "HEAD").is_file():
        raise ValueError
    return _local(metadata / "objects")


def _inflate(stream):
    decoder = zlib.decompressobj()
    output = bytearray()
    compressed = 0
    while not decoder.eof:
        chunk = stream.read(8192)
        if not chunk:
            raise ValueError
        compressed += len(chunk)
        if compressed > MAX_OBJECT * 2:
            raise EvidenceRefused("revision_storage_limit")
        output.extend(decoder.decompress(chunk, MAX_OBJECT + 1 - len(output)))
        if len(output) > MAX_OBJECT:
            raise EvidenceRefused("revision_storage_limit")
    return bytes(output)


def _varint(data, position):
    value = shift = 0
    while shift < 64:
        byte = data[position]
        position += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, position
        shift += 7
    raise ValueError


def _copy_delta(data, position, opcode, source):
    offset = size = 0
    for bit in range(7):
        if opcode & (1 << bit):
            byte = data[position]
            position += 1
            if bit < 4:
                offset |= byte << (bit * 8)
            else:
                size |= byte << ((bit - 4) * 8)
    size = size or 0x10000
    if offset + size > len(source):
        raise ValueError
    return source[offset : offset + size], position


def _delta(source, data):
    source_size, position = _varint(data, 0)
    target_size, position = _varint(data, position)
    if source_size != len(source) or target_size > MAX_OBJECT:
        raise ValueError
    output = bytearray()
    while position < len(data):
        opcode = data[position]
        position += 1
        if opcode & 128:
            chunk, position = _copy_delta(data, position, opcode, source)
        elif opcode:
            chunk = data[position : position + opcode]
            position += opcode
        else:
            raise ValueError
        output.extend(chunk)
        if len(output) > target_size:
            raise ValueError
    if len(output) != target_size or position != len(data):
        raise ValueError
    return bytes(output)


def _index_offset(index, oid):
    width = len(oid)
    if index[:8] != b"\xfftOc\x00\x00\x00\x02":
        raise ValueError
    count = struct.unpack_from(">I", index, 8 + 255 * 4)[0]
    names = 8 + 256 * 4
    offsets = names + count * (width + 4)
    if offsets + count * 4 + width * 2 > len(index):
        raise ValueError
    algorithm = "sha1" if width == 20 else "sha256"
    if hashlib.new(algorithm, index[:-width]).digest() != index[-width:]:
        raise ValueError
    low, high = 0, count
    while low < high:
        middle = (low + high) // 2
        candidate = index[names + middle * width : names + (middle + 1) * width]
        if candidate < oid:
            low = middle + 1
        else:
            high = middle
    if low == count or index[names + low * width : names + (low + 1) * width] != oid:
        return None
    offset = struct.unpack_from(">I", index, offsets + low * 4)[0]
    if offset & 0x80000000:
        offset = struct.unpack_from(">Q", index, offsets + count * 4 + (offset & 0x7FFFFFFF) * 8)[0]
    return offset


def _pack_header(stream):
    byte = stream.read(1)[0]
    kind, size, shift = (byte >> 4) & 7, byte & 15, 4
    while byte & 128:
        byte = stream.read(1)[0]
        size |= (byte & 127) << shift
        shift += 7
        if shift > 64:
            raise ValueError
    if size > MAX_OBJECT:
        raise EvidenceRefused("revision_storage_limit")
    return kind, size


def _packed_record(stream, offset, width):
    if stream.read(8) not in (b"PACK\x00\x00\x00\x02", b"PACK\x00\x00\x00\x03") or offset < 12:
        raise ValueError
    stream.seek(offset)
    kind, size = _pack_header(stream)
    base_offset = base_oid = None
    if kind == 6:
        byte = stream.read(1)[0]
        distance = byte & 127
        for _ in range(10):
            if not byte & 128:
                break
            byte = stream.read(1)[0]
            distance = ((distance + 1) << 7) | (byte & 127)
        if byte & 128 or not 0 < distance < offset:
            raise ValueError
        base_offset = offset - distance
    elif kind == 7:
        base_oid = stream.read(width)
    data = _inflate(stream)
    if len(data) != size:
        raise ValueError
    return kind, base_offset, base_oid, data


def _packed_record(stream, offset, width):
    if stream.read(8) not in (b"PACK\x00\x00\x00\x02", b"PACK\x00\x00\x00\x03") or offset < 12:
        raise ValueError
    stream.seek(offset)
    kind, size = _pack_header(stream)
    base_offset = base_oid = None
    if kind == 6:
        byte = stream.read(1)[0]
        distance = byte & 127
        for _ in range(10):
            if not byte & 128:
                break
            byte = stream.read(1)[0]
            distance = ((distance + 1) << 7) | (byte & 127)
        if byte & 128 or not 0 < distance < offset:
            raise ValueError
        base_offset = offset - distance
    elif kind == 7:
        base_oid = stream.read(width)
    data = _inflate(stream)
    if len(data) != size:
        raise ValueError
    return kind, base_offset, base_oid, data


def _packed_record(stream, offset, width):
    if stream.read(8) not in (b"PACK\x00\x00\x00\x02", b"PACK\x00\x00\x00\x03") or offset < 12:
        raise ValueError
    stream.seek(offset)
    kind, size = _pack_header(stream)
    base_offset = base_oid = None
    if kind == 6:
        byte = stream.read(1)[0]
        distance = byte & 127
        for _ in range(10):
            if not byte & 128:
                break
            byte = stream.read(1)[0]
            distance = ((distance + 1) << 7) | (byte & 127)
        if byte & 128 or not 0 < distance < offset:
            raise ValueError
        base_offset = offset - distance
    elif kind == 7:
        base_oid = stream.read(width)
    data = _inflate(stream)
    if len(data) != size:
        raise ValueError
    return kind, base_offset, base_oid, data


def _packed_record(stream, offset, width):
    if stream.read(8) not in (b"PACK\x00\x00\x00\x02", b"PACK\x00\x00\x00\x03") or offset < 12:
        raise ValueError
    stream.seek(offset)
    kind, size = _pack_header(stream)
    base_offset = base_oid = None
    if kind == 6:
        byte = stream.read(1)[0]
        distance = byte & 127
        for _ in range(10):
            if not byte & 128:
                break
            byte = stream.read(1)[0]
            distance = ((distance + 1) << 7) | (byte & 127)
        if byte & 128 or not 0 < distance < offset:
            raise ValueError
        base_offset = offset - distance
    elif kind == 7:
        base_oid = stream.read(width)
    data = _inflate(stream)
    if len(data) != size:
        raise ValueError
    return kind, base_offset, base_oid, data


def _packed(objects, pack, offset, width, depth):
    if depth > MAX_DEPTH:
        raise ValueError
    with _local(pack).open("rb") as stream:
        kind, base_offset, base_oid, data = _packed_record(stream, offset, width)
    if kind == 6:
        base_kind, base = _packed(objects, pack, base_offset, width, depth + 1)
        return base_kind, _delta(base, data)
    if kind == 7:
        base_kind, base = _object(objects, base_oid, depth + 1)
        return base_kind, _delta(base, data)
    return {1: b"commit", 2: b"tree", 3: b"blob", 4: b"tag"}[kind], data


def _object(objects, oid, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError
    name = oid.hex()
    loose = objects / name[:2] / name[2:]
    if loose.is_file():
        with _local(loose).open("rb") as stream:
            raw = _inflate(stream)
        header, data = raw.split(b"\0", 1)
        kind, size = header.split(b" ", 1)
        if int(size) != len(data):
            raise ValueError
    else:
        indexes = sorted((objects / "pack").glob("pack-*.idx"))
        if len(indexes) > MAX_PACKS:
            raise EvidenceRefused("revision_storage_limit")
        matches = []
        for path in indexes:
            offset = _index_offset(_read(path, MAX_INDEX), oid)
            if offset is not None:
                matches.append((path.with_suffix(".pack"), offset))
        if not matches:
            raise EvidenceRefused("revision_unresolvable")
        kind, data = _packed(objects, *matches[0], len(oid), depth)
    algorithm = "sha1" if len(oid) == 20 else "sha256"
    raw = kind + b" " + str(len(data)).encode("ascii") + b"\0" + data
    if hashlib.new(algorithm, raw).digest() != oid:
        raise EvidenceRefused("revision_object_mismatch")
    return kind, data


def verify_commit(repository, revision):
    """Resolve exactly this full OID in the trusted configured local repository."""
    if not isinstance(revision, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
        raise EvidenceRefused("invalid_revision")
    try:
        kind, data = _object(_objects(repository), bytes.fromhex(revision))
        tree_line = data.split(b"\n", 1)[0]
        tree_shape = rb"tree [0-9a-f]{" + str(len(revision)).encode("ascii") + rb"}"
        if kind != b"commit" or not re.fullmatch(tree_shape, tree_line) or b"\n\n" not in data:
            raise EvidenceRefused("revision_not_commit")
    except EvidenceRefused:
        raise
    except OSError, ValueError, IndexError, KeyError, OverflowError, struct.error, zlib.error:
        raise EvidenceRefused("revision_repository_unavailable") from None
