"""Byte-preserving JSON Lines quarantine and verified reconstruction.

This module never executes record content, guesses repairs, or opens the source
for writing. Errors intentionally contain fixed descriptions, never input data.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from contextlib import ExitStack
from pathlib import Path

DEFAULT_MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024 * 1024
_CHUNK = 64 * 1024
_MANIFEST_LIMIT = 64 * 1024
_INDEX_LINE_LIMIT = 2048
_MAX_INTEGER = 2**63 - 1
_MAX_DEPTH = 256
_ARTIFACTS = ("accepted.jsonl", "rejected.bin", "index.jsonl")
_REASONS = frozenset(
    (
        "line_too_long",
        "invalid_utf8",
        "bom",
        "blank_line",
        "invalid_json",
        "duplicate_key",
        "nonfinite_number",
        "nesting_limit",
        "not_object",
    )
)
_SUMMARY_KEYS = frozenset(
    (
        "format_version",
        "total_lines",
        "accepted_lines",
        "rejected_lines",
        "input_bytes",
        "input_sha256",
        "reason_counts",
    )
)
_INDEX_KEYS = frozenset(("line", "offset", "length", "stream", "stream_offset", "reason", "sha256"))


class QuarantineError(ValueError):
    """Configuration, filesystem, unstable-source or bundle validation error."""


class _InvalidRecord(Exception):
    pass


def _policy(max_line_bytes, objects_only):
    if type(max_line_bytes) is not int or not 1 <= max_line_bytes <= MAX_LINE_BYTES:
        raise QuarantineError("max_line_bytes must be an integer from 1 through 67108864")
    if type(objects_only) is not bool:
        raise QuarantineError("objects_only must be a boolean")


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _identity(info):
    return info.st_dev, info.st_ino


def _regular_reader(path, stack):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise QuarantineError("Expected a regular file without a symbolic link")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        handle = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    stack.enter_context(handle)
    opened = os.fstat(handle.fileno())
    if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
        raise QuarantineError("File identity changed while opening")
    return handle, before, opened


def _unchanged(path, handle, path_before, fd_before):
    # On Windows, stat and fstat can have different ctime semantics.
    if _signature(path.lstat()) != _signature(path_before) or _signature(
        os.fstat(handle.fileno())
    ) != _signature(fd_before):
        raise QuarantineError("Input changed during the operation")


def _owned_writer(path, stack, owned):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    owned.append((path, _identity(os.fstat(fd))))
    try:
        handle = os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise
    return stack.enter_context(handle)


def _cleanup(owned):
    for path, identity in reversed(owned):
        try:
            if _identity(path.lstat()) == identity:
                path.unlink()
        except OSError:
            pass


class _Sink:
    def __init__(self, handle=None):
        self.handle = handle
        self.bytes = 0
        self.digest = hashlib.sha256()

    def write(self, data):
        if self.handle is not None and self.handle.write(data) != len(data):
            raise QuarantineError("Could not write a complete output")
        self.bytes += len(data)
        self.digest.update(data)

    def metadata(self):
        return {"bytes": self.bytes, "sha256": self.digest.hexdigest()}


def _record_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidRecord("duplicate_key")
        result[key] = value
    return result


def _record_constant(_text):
    raise _InvalidRecord("nonfinite_number")


def _number_token(_text):
    # Validate syntax through the JSON scanner without int digit limits,
    # floating-point overflow, rounding or constructing giant numeric objects.
    return None


def _nesting_exceeded(text, limit):
    depth = 0
    quoted = escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > limit:
                return True
        elif character in "]}":
            depth -= 1
    return False


def _classify(raw, objects_only):
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        return "invalid_utf8"
    if text.startswith("\ufeff"):
        return "bom"
    if not text.strip(" \t\r\n"):
        return "blank_line"
    if _nesting_exceeded(text, _MAX_DEPTH):
        return "nesting_limit"
    try:
        value = json.loads(
            text,
            object_pairs_hook=_record_object,
            parse_int=_number_token,
            parse_float=_number_token,
            parse_constant=_record_constant,
        )
    except _InvalidRecord as exc:
        return str(exc)
    except RecursionError:
        return "nesting_limit"
    except ValueError:
        return "invalid_json"
    if objects_only and not isinstance(value, dict):
        return "not_object"
    return None


def _json_line(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")


def _scan(handle, max_line_bytes, objects_only, accepted, rejected, index):
    total = accepted_lines = rejected_lines = offset = 0
    fingerprint = hashlib.sha256()
    reasons = {}
    while True:
        parts = []
        length = 0
        record_hash = hashlib.sha256()
        oversize = False
        rejected_offset = rejected.bytes
        while True:
            part = handle.readline(_CHUNK)
            if not part:
                break
            fingerprint.update(part)
            record_hash.update(part)
            length += len(part)
            if length > max_line_bytes and not oversize:
                oversize = True
                for buffered in parts:
                    rejected.write(buffered)
                parts.clear()
            if oversize:
                rejected.write(part)
            else:
                parts.append(part)
            if part.endswith(b"\n"):
                break
        if not length:
            break
        total += 1
        reason = "line_too_long" if oversize else _classify(b"".join(parts), objects_only)
        if reason is None:
            selected = "accepted"
            stream_offset = accepted.bytes
            accepted_lines += 1
            for part in parts:
                accepted.write(part)
        else:
            selected = "rejected"
            stream_offset = rejected_offset
            rejected_lines += 1
            reasons[reason] = reasons.get(reason, 0) + 1
            if not oversize:
                for part in parts:
                    rejected.write(part)
        if index is not None:
            index.write(
                _json_line(
                    {
                        "line": total,
                        "offset": offset,
                        "length": length,
                        "stream": selected,
                        "stream_offset": stream_offset,
                        "reason": reason,
                        "sha256": record_hash.hexdigest(),
                    }
                )
            )
        offset += length
        if offset > _MAX_INTEGER:
            raise QuarantineError("Input exceeds supported byte count")
    return {
        "format_version": 1,
        "total_lines": total,
        "accepted_lines": accepted_lines,
        "rejected_lines": rejected_lines,
        "input_bytes": offset,
        "input_sha256": fingerprint.hexdigest(),
        "reason_counts": reasons,
    }


def _hash_handle(handle):
    handle.seek(0)
    digest = hashlib.sha256()
    size = 0
    while chunk := handle.read(_CHUNK):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def _verify_source(path, reader, path_before, fd_before, summary):
    _unchanged(path, reader, path_before, fd_before)
    size, fingerprint = _hash_handle(reader)
    _unchanged(path, reader, path_before, fd_before)
    if (
        size != summary["input_bytes"]
        or size != fd_before.st_size
        or fingerprint != summary["input_sha256"]
    ):
        raise QuarantineError("Input changed during the operation")


def inspect(source, *, max_line_bytes=DEFAULT_MAX_LINE_BYTES, objects_only=False):
    """Check physical LF-separated records without writing files or record content."""
    _policy(max_line_bytes, objects_only)
    try:
        path = Path(source).absolute()
        with ExitStack() as stack:
            reader, path_before, fd_before = _regular_reader(path, stack)
            summary = _scan(reader, max_line_bytes, objects_only, _Sink(), _Sink(), None)
            _verify_source(path, reader, path_before, fd_before, summary)
        return summary
    except QuarantineError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise QuarantineError("Cannot check the input file") from exc


def partition(source, output_dir, *, max_line_bytes=DEFAULT_MAX_LINE_BYTES, objects_only=False):
    """Create an exclusive complete bundle, preserving every original byte.

    Rejects are a successful quarantine outcome, reflected in the returned counts.
    A valid manifest is published last. Handled failures remove only owned files.
    """
    _policy(max_line_bytes, objects_only)
    owned = []
    directory = None
    directory_identity = None
    completed = False
    try:
        source_path = Path(source).absolute()
        with ExitStack() as source_stack:
            reader, path_before, fd_before = _regular_reader(source_path, source_stack)
            directory = Path(output_dir).absolute()
            directory.mkdir(mode=0o700)
            directory_identity = _identity(directory.lstat())
            with ExitStack() as write_stack:
                sinks = {
                    name: _Sink(_owned_writer(directory / name, write_stack, owned))
                    for name in _ARTIFACTS
                }
                summary = _scan(
                    reader,
                    max_line_bytes,
                    objects_only,
                    sinks["accepted.jsonl"],
                    sinks["rejected.bin"],
                    sinks["index.jsonl"],
                )
                _verify_source(source_path, reader, path_before, fd_before, summary)
            manifest = {
                **summary,
                "policy": {"max_line_bytes": max_line_bytes, "objects_only": objects_only},
                "artifacts": {name: sink.metadata() for name, sink in sinks.items()},
            }
            temporary_manifest = directory / ".manifest.tmp"
            with ExitStack() as manifest_stack:
                writer = _owned_writer(temporary_manifest, manifest_stack, owned)
                raw = _json_line(manifest)
                if writer.write(raw) != len(raw):
                    raise QuarantineError("Could not write a complete manifest")
            # Hard-link creation is atomic and fails if the destination exists.
            # The temporary name is in the same directory/filesystem.
            os.link(temporary_manifest, directory / "manifest.json")
            owned.append((directory / "manifest.json", _identity(temporary_manifest.lstat())))
            temporary_manifest.unlink()
            completed = True
        return summary
    except QuarantineError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise QuarantineError("Cannot create a complete quarantine bundle") from exc
    finally:
        if not completed:
            _cleanup(owned)
            if directory is not None and directory_identity is not None:
                try:
                    if _identity(directory.lstat()) == directory_identity:
                        directory.rmdir()
                except OSError:
                    pass


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QuarantineError("Duplicate metadata key")
        result[key] = value
    return result


def _metadata_constant(_text):
    raise QuarantineError("Invalid metadata number")


def _metadata_json(raw):
    try:
        text = raw.decode("utf-8")
        if _nesting_exceeded(text, 16):
            raise QuarantineError("Metadata exceeds nesting limit")
        return json.loads(text, object_pairs_hook=_strict_object, parse_constant=_metadata_constant)
    except QuarantineError:
        raise
    except (ValueError, RecursionError) as exc:
        raise QuarantineError("Invalid bundle metadata") from exc


def _keys(value, keys):
    if not isinstance(value, dict) or set(value) != keys:
        raise QuarantineError("Unsupported metadata fields")


def _count(value, *, positive=False):
    if type(value) is not int or not int(positive) <= value <= _MAX_INTEGER:
        raise QuarantineError("Invalid metadata count")


def _sha(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise QuarantineError("Invalid metadata digest")


def _manifest_schema(value):
    _keys(value, _SUMMARY_KEYS | {"policy", "artifacts"})
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        raise QuarantineError("Unsupported bundle version")
    for key in ("total_lines", "accepted_lines", "rejected_lines", "input_bytes"):
        _count(value[key])
    _sha(value["input_sha256"])
    if (
        value["accepted_lines"] + value["rejected_lines"] != value["total_lines"]
        or value["total_lines"] > value["input_bytes"]
    ):
        raise QuarantineError("Inconsistent manifest counts")
    reasons = value["reason_counts"]
    if not isinstance(reasons, dict) or set(reasons) - _REASONS:
        raise QuarantineError("Invalid rejection reason counts")
    for count in reasons.values():
        _count(count, positive=True)
    if sum(reasons.values()) != value["rejected_lines"]:
        raise QuarantineError("Inconsistent rejection reason counts")
    _keys(value["policy"], {"max_line_bytes", "objects_only"})
    _policy(**value["policy"])
    _keys(value["artifacts"], set(_ARTIFACTS))
    for metadata in value["artifacts"].values():
        _keys(metadata, {"bytes", "sha256"})
        _count(metadata["bytes"])
        _sha(metadata["sha256"])
    artifacts = value["artifacts"]
    if (
        artifacts["accepted.jsonl"]["bytes"] + artifacts["rejected.bin"]["bytes"]
        != value["input_bytes"]
    ):
        raise QuarantineError("Inconsistent artifact sizes")
    for stream in ("accepted", "rejected"):
        name = "accepted.jsonl" if stream == "accepted" else "rejected.bin"
        if value[stream + "_lines"] > artifacts[name]["bytes"]:
            raise QuarantineError("Inconsistent stream counts")
    index_bytes = artifacts["index.jsonl"]["bytes"]
    if index_bytes > _INDEX_LINE_LIMIT * value["total_lines"] or (
        not value["total_lines"] and index_bytes
    ):
        raise QuarantineError("Inconsistent index size")


def _index_schema(entry):
    _keys(entry, _INDEX_KEYS)
    for key in ("line", "length"):
        _count(entry[key], positive=True)
    for key in ("offset", "stream_offset"):
        _count(entry[key])
    _sha(entry["sha256"])
    if entry["stream"] == "accepted":
        if entry["reason"] is not None:
            raise QuarantineError("Invalid accepted-record metadata")
    elif entry["stream"] == "rejected":
        if not isinstance(entry["reason"], str) or entry["reason"] not in _REASONS:
            raise QuarantineError("Invalid rejected-record metadata")
    else:
        raise QuarantineError("Invalid stream selector")


def _reconstruct(manifest, handles, output):
    offset = total = 0
    positions = {"accepted": 0, "rejected": 0}
    counts = {"accepted": 0, "rejected": 0}
    reasons = {}
    fingerprint = hashlib.sha256()
    index = handles["index.jsonl"]
    index.seek(0)
    for name in ("accepted.jsonl", "rejected.bin"):
        handles[name].seek(0)
    while raw := index.readline(_INDEX_LINE_LIMIT + 1):
        if len(raw) > _INDEX_LINE_LIMIT or not raw.endswith(b"\n"):
            raise QuarantineError("Index record exceeds limits or is incomplete")
        entry = _metadata_json(raw)
        _index_schema(entry)
        total += 1
        stream = entry["stream"]
        if (
            entry["line"] != total
            or entry["offset"] != offset
            or entry["stream_offset"] != positions[stream]
            or total > manifest["total_lines"]
        ):
            raise QuarantineError("Index has a gap, overlap or invalid sequence")
        length = entry["length"]
        name = "accepted.jsonl" if stream == "accepted" else "rejected.bin"
        if positions[stream] + length > manifest["artifacts"][name]["bytes"]:
            raise QuarantineError("Index record exceeds stream bounds")
        source = handles[name]
        remaining = length
        digest = hashlib.sha256()
        last = None
        while remaining:
            chunk = source.read(min(remaining, _CHUNK))
            if not chunk:
                raise QuarantineError("Truncated stream artifact")
            remaining -= len(chunk)
            lf = chunk.find(b"\n")
            if lf != -1 and (remaining or lf != len(chunk) - 1):
                raise QuarantineError("Index does not match physical line boundaries")
            last = chunk[-1]
            digest.update(chunk)
            fingerprint.update(chunk)
            if output.write(chunk) != len(chunk):
                raise QuarantineError("Could not write a complete restored file")
        if total < manifest["total_lines"] and last != 10:
            raise QuarantineError("Index does not match physical line boundaries")
        if digest.hexdigest() != entry["sha256"]:
            raise QuarantineError("Record digest mismatch")
        positions[stream] += length
        counts[stream] += 1
        offset += length
        if entry["reason"] is not None:
            reason = entry["reason"]
            reasons[reason] = reasons.get(reason, 0) + 1
    if (
        total != manifest["total_lines"]
        or offset != manifest["input_bytes"]
        or counts["accepted"] != manifest["accepted_lines"]
        or counts["rejected"] != manifest["rejected_lines"]
        or reasons != manifest["reason_counts"]
        or fingerprint.hexdigest() != manifest["input_sha256"]
    ):
        raise QuarantineError("Reconstruction does not match the manifest")
    for stream, name in (("accepted", "accepted.jsonl"), ("rejected", "rejected.bin")):
        if positions[stream] != manifest["artifacts"][name]["bytes"]:
            raise QuarantineError("Unindexed stream bytes")
    return {"format_version": 1, "bytes": offset, "sha256": fingerprint.hexdigest()}


def restore(bundle_dir, output_path):
    """Validate a bundle completely before exclusively publishing its reconstruction.

    Fixed artifact names prevent metadata-controlled path traversal. Publication
    uses an exclusive hard link and requires support in the output filesystem.
    """
    owned = []
    temporary = None
    try:
        bundle = Path(bundle_dir).absolute()
        info = bundle.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise QuarantineError("Expected a bundle directory without a symbolic link")
        target = Path(output_path).absolute()
        if os.path.lexists(target):
            raise QuarantineError("Output already exists")
        if target.resolve(strict=False).is_relative_to(bundle.resolve(strict=True)):
            raise QuarantineError("Restore output must be outside the bundle directory")
        with ExitStack() as stack:
            baselines = {}
            manifest_path = bundle / "manifest.json"
            reader, path_before, fd_before = _regular_reader(manifest_path, stack)
            baselines["manifest.json"] = (reader, path_before, fd_before)
            raw = reader.read(_MANIFEST_LIMIT + 1)
            if len(raw) > _MANIFEST_LIMIT:
                raise QuarantineError("Manifest exceeds size limit")
            manifest = _metadata_json(raw)
            _manifest_schema(manifest)
            handles = {}
            for name in _ARTIFACTS:
                handle, path_before, fd_before = _regular_reader(bundle / name, stack)
                baselines[name] = (handle, path_before, fd_before)
                handles[name] = handle
                expected = manifest["artifacts"][name]
                if fd_before.st_size != expected["bytes"]:
                    raise QuarantineError("Artifact size mismatch")
                size, digest = _hash_handle(handle)
                if size != expected["bytes"] or digest != expected["sha256"]:
                    raise QuarantineError("Artifact digest mismatch")
                _unchanged(bundle / name, handle, path_before, fd_before)
            fd, temporary_name = tempfile.mkstemp(
                prefix=".jsonl-roundtrip-", suffix=".tmp", dir=target.parent
            )
            temporary = Path(temporary_name)
            owned.append((temporary, _identity(os.fstat(fd))))
            with os.fdopen(fd, "wb") as output:
                summary = _reconstruct(manifest, handles, output)
            for name, (handle, path_before, fd_before) in baselines.items():
                _unchanged(bundle / name, handle, path_before, fd_before)
            # Each fixed input file has its own baseline; also reject replacement
            # of the bundle directory while those files are being validated.
            if _identity(bundle.lstat()) != _identity(info):
                raise QuarantineError("Bundle directory changed during restore")
            os.link(temporary, target)
            owned.append((target, _identity(temporary.lstat())))
            temporary.unlink()
            temporary = None
        return summary
    except QuarantineError:
        _cleanup(owned)
        raise
    except (OSError, ValueError, RecursionError) as exc:
        _cleanup(owned)
        raise QuarantineError("Cannot restore a verified bundle") from exc
    finally:
        if temporary is not None:
            _cleanup([(path, identity) for path, identity in owned if path == temporary])
