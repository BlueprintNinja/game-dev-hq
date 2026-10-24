"""Local object resolution with synthetic loose/packed Git storage only."""

import hashlib
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from gamedev.revision_objects import MAX_OBJECT, verify_commit
from gamedev.verified_evidence import EvidenceRefused


def object_bytes(kind, data):
    return kind + b" " + str(len(data)).encode() + b"\0" + data


def loose_object(root, kind, data, algorithm="sha1"):
    raw = object_bytes(kind, data)
    oid = hashlib.new(algorithm, raw).hexdigest()
    path = root / ".git" / "objects" / oid[:2] / oid[2:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zlib.compress(raw))
    return oid


def repository(root, algorithm="sha1"):
    metadata = root / ".git"
    metadata.mkdir(parents=True)
    (metadata / "HEAD").write_text("ref: refs/heads/main\n")
    tree = loose_object(root, b"tree", b"", algorithm)
    commit = (
        f"tree {tree}\nauthor Example <example@example.test> 1 +0000\n"
        "committer Example <example@example.test> 1 +0000\n\nSynthetic commit\n"
    ).encode()
    return loose_object(root, b"commit", commit, algorithm), commit


def pack_header(kind, size):
    byte = (kind << 4) | (size & 15)
    size >>= 4
    result = bytearray()
    while size:
        result.append(byte | 128)
        byte = size & 127
        size >>= 7
    result.append(byte)
    return bytes(result)


def varint(value):
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def packed_objects(root, commit, algorithm="sha1", delta_kind=None):
    """Write a v2 Git pack/index with a full or delta commit, without git."""
    base_id = hashlib.new(algorithm, object_bytes(b"commit", commit)).digest()
    header = b"PACK" + struct.pack(">II", 2, 2 if delta_kind else 1)
    first = pack_header(1, len(commit)) + zlib.compress(commit)
    entries = [(base_id, 12, first)]
    target = commit
    if delta_kind:
        target = commit.replace(b"Synthetic", b"Different")
        delta = varint(len(commit)) + varint(len(target))
        for start in range(0, len(target), 127):
            chunk = target[start : start + 127]
            delta += bytes([len(chunk)]) + chunk
        offset = 12 + len(first)
        if delta_kind == 7:
            base = base_id
        else:
            distance = offset - 12
            base = bytearray([distance & 127])
            while distance > 127:
                distance = (distance >> 7) - 1
                base.insert(0, 128 | (distance & 127))
            base = bytes(base)
        second = pack_header(delta_kind, len(delta)) + base + zlib.compress(delta)
        target_id = hashlib.new(algorithm, object_bytes(b"commit", target)).digest()
        entries.append((target_id, offset, second))
    pack = header + b"".join(entry[2] for entry in entries)
    pack_hash = hashlib.new(algorithm, pack).digest()
    path = root / ".git" / "objects" / "pack" / ("pack-" + pack_hash.hex())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".pack").write_bytes(pack + pack_hash)
    ordered = sorted(entries)
    fanout = b"".join(struct.pack(">I", sum(oid[0] <= i for oid, _, _ in ordered)) for i in range(256))
    index = b"\xfftOc\0\0\0\x02" + fanout
    index += b"".join(oid for oid, _, _ in ordered)
    index += b"".join(struct.pack(">I", zlib.crc32(data)) for _, _, data in ordered)
    index += b"".join(struct.pack(">I", offset) for _, offset, _ in ordered)
    index += pack_hash
    index += hashlib.new(algorithm, index).digest()
    path.with_suffix(".idx").write_bytes(index)
    return hashlib.new(algorithm, object_bytes(b"commit", target)).hexdigest(), path


class RevisionObjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.oid, self.commit = repository(self.root)

    def assert_refused(self, code, oid, root=None):
        with self.assertRaises(EvidenceRefused) as caught:
            verify_commit(root or self.root, oid)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_full_commit_resolves_without_process_network_or_writes(self):
        with (
            mock.patch("subprocess.Popen", side_effect=AssertionError("process")),
            mock.patch("socket.socket", side_effect=AssertionError("network")),
            mock.patch.object(Path, "write_bytes", side_effect=AssertionError("write")),
            mock.patch.object(Path, "write_text", side_effect=AssertionError("write")),
        ):
            self.assertIsNone(verify_commit(self.root, self.oid))

    def test_invalid_full_oid_and_noncommit_missing_or_corrupt_objects(self):
        for oid in (None, 1, "HEAD", self.oid[:12], self.oid.upper(), "-" * 40, "a" * 39, "a" * 65, "a" * 40 + "\n"):
            with self.subTest(oid=oid):
                self.assert_refused("invalid_revision", oid)
        self.assert_refused("revision_unresolvable", "f" * 40)
        blob = loose_object(self.root, b"blob", b"not a commit")
        self.assert_refused("revision_not_commit", blob)
        path = self.root / ".git" / "objects" / self.oid[:2] / self.oid[2:]
        path.write_bytes(zlib.compress(object_bytes(b"commit", self.commit + b"tampered")))
        self.assert_refused("revision_object_mismatch", self.oid)
        path.write_bytes(b"corrupt")
        self.assert_refused("revision_repository_unavailable", self.oid)

    def test_loose_and_packed_sha256_and_sha1_including_both_delta_kinds(self):
        for algorithm in ("sha1", "sha256"):
            for delta in (None, 6, 7):
                with self.subTest(algorithm=algorithm, delta=delta), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    oid, commit = repository(root, algorithm)
                    verify_commit(root, oid)
                    # Force the selected commit to come from the pack, not loose storage.
                    loose = root / ".git" / "objects" / oid[:2] / oid[2:]
                    loose.unlink()
                    target, _ = packed_objects(root, commit, algorithm, delta)
                    verify_commit(root, target)

    def test_packed_index_and_object_tampering_fails_closed(self):
        path = self.root / ".git" / "objects" / self.oid[:2] / self.oid[2:]
        path.unlink()
        oid, pack = packed_objects(self.root, self.commit)
        index = pack.with_suffix(".idx")
        original = index.read_bytes()
        index.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        self.assert_refused("revision_repository_unavailable", oid)
        index.write_bytes(original)
        pack.with_suffix(".pack").write_bytes(b"corrupt")
        self.assert_refused("revision_repository_unavailable", oid)

    def test_linked_worktree_and_bare_storage(self):
        linked = self.root / "linked"
        linked.mkdir()
        metadata = self.root / ".git" / "worktrees" / "linked"
        metadata.mkdir(parents=True)
        (metadata / "commondir").write_text("../..\n")
        (linked / ".git").write_text("gitdir: " + str(metadata) + "\n")
        verify_commit(linked, self.oid)
        verify_commit(self.root / ".git", self.oid)

    def test_bounds_and_nonlocal_paths(self):
        oid = loose_object(self.root, b"commit", b"x" * (MAX_OBJECT + 1))
        self.assert_refused("revision_storage_limit", oid)
        for root in (Path("relative"), Path("//server/private")):
            with self.subTest(root=root):
                self.assert_refused("revision_repository_unavailable", self.oid, root)

    def test_delta_copy_and_malformed_deltas(self):
        from gamedev.revision_objects import _delta

        self.assertEqual(_delta(b"abc", b"\x03\x04\x90\x03\x01!"), b"abc!")
        for delta in (b"\x04\x03\x90\x03", b"\x03\x03\0", b"\x03\x03\x90\x04", b"\x03\x01\x02x"):
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                _delta(b"abc", delta)
