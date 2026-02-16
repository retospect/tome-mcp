"""BibTeX parser and writer using bibtexparser v2.

Provides safe read-modify-write operations on references.bib.
All writes go through a roundtrip safety check: the file is parsed,
modified in memory, serialized, re-parsed, and compared before writing.
A backup is made before every write.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import bibtexparser
from bibtexparser.model import Entry, Field

from tome.errors import BibParseError, BibWriteError, DuplicateKey, PaperNotFound

# x-fields managed by Tome
X_FIELDS = {"x-pdf", "x-doi-status", "x-tags"}


def parse_bib(path: Path) -> bibtexparser.Library:
    """Parse a .bib file into a bibtexparser Library.

    Args:
        path: Path to the .bib file.

    Returns:
        Parsed Library object.

    Raises:
        BibParseError: If the file cannot be parsed.
        FileNotFoundError: If the file does not exist.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise BibParseError(str(path), f"UTF-8 decode error: {e}") from e

    try:
        library = bibtexparser.parse_string(text)
    except Exception as e:
        raise BibParseError(str(path), str(e)) from e

    return library


def get_entry(library: bibtexparser.Library, key: str) -> Entry:
    """Get a bib entry by key.

    Args:
        library: Parsed bib library.
        key: The bib key to look up.

    Returns:
        The matching Entry.

    Raises:
        PaperNotFound: If the key does not exist.
    """
    entries_dict = {e.key: e for e in library.entries}
    if key not in entries_dict:
        import difflib
        near = difflib.get_close_matches(key, entries_dict.keys(), n=5, cutoff=0.5)
        raise PaperNotFound(key, near=near)
    return entries_dict[key]


def list_keys(library: bibtexparser.Library) -> list[str]:
    """Return all entry keys in order."""
    return [e.key for e in library.entries]


def entry_to_dict(entry: Entry) -> dict[str, Any]:
    """Convert a bib Entry to a plain dict.

    Returns:
        Dict with 'key', 'type', and all field key-value pairs.
    """
    result: dict[str, Any] = {
        "key": entry.key,
        "type": entry.entry_type,
    }
    for field in entry.fields:
        result[field.key] = field.value
    return result


def set_field(entry: Entry, key: str, value: str) -> None:
    """Set or update a field on an entry.

    Args:
        entry: The bib entry to modify.
        key: Field name (e.g. 'title', 'x-doi-status').
        value: Field value.
    """
    entry.set_field(Field(key=key, value=value))


def remove_field(entry: Entry, key: str) -> str | None:
    """Remove a field from an entry.

    Args:
        entry: The bib entry to modify.
        key: Field name to remove.

    Returns:
        The removed field value, or None if field didn't exist.
    """
    field = entry.fields_dict.get(key)
    if field is None:
        return None
    value = field.value
    entry.pop(key)
    return value


def add_entry(
    library: bibtexparser.Library,
    key: str,
    entry_type: str = "article",
    fields: dict[str, str] | None = None,
) -> Entry:
    """Add a new entry to the library.

    Args:
        library: The bib library.
        key: Bib key for the new entry.
        entry_type: Entry type (article, inproceedings, misc, etc.).
        fields: Dict of field name → value.

    Returns:
        The newly created Entry.

    Raises:
        DuplicateKey: If the key already exists.
    """
    existing_keys = {e.key for e in library.entries}
    if key in existing_keys:
        raise DuplicateKey(key)

    entry_fields = []
    if fields:
        for k, v in fields.items():
            entry_fields.append(Field(key=k, value=v))

    entry = Entry(entry_type=entry_type, key=key, fields=entry_fields)
    library.add(entry)
    return entry


def remove_entry(library: bibtexparser.Library, key: str) -> Entry:
    """Remove an entry from the library.

    Args:
        library: The bib library.
        key: Bib key to remove.

    Returns:
        The removed Entry.

    Raises:
        PaperNotFound: If the key does not exist.
    """
    entry = get_entry(library, key)
    library.remove(entry)
    return entry


def rename_key(library: bibtexparser.Library, old_key: str, new_key: str) -> Entry:
    """Rename an entry's bib key.

    Args:
        library: The bib library.
        old_key: Current bib key.
        new_key: New bib key.

    Returns:
        The entry with its key changed.

    Raises:
        PaperNotFound: If old_key does not exist.
        DuplicateKey: If new_key already exists.
    """
    existing_keys = {e.key for e in library.entries}
    if old_key not in existing_keys:
        import difflib
        near = difflib.get_close_matches(old_key, existing_keys, n=5, cutoff=0.5)
        raise PaperNotFound(old_key, near=near)
    if new_key in existing_keys:
        raise DuplicateKey(new_key)

    entry = get_entry(library, old_key)
    entry.key = new_key
    return entry


def write_bib(
    library: bibtexparser.Library,
    path: Path,
    backup_dir: Path | None = None,
) -> None:
    """Write the library back to a .bib file with safety checks.

    Performs a roundtrip test: serialize → re-parse → compare entry keys
    and field counts. If anything looks wrong, the write is aborted.

    A backup of the original file is made before writing.

    Args:
        library: The library to write.
        path: Path to write to.
        backup_dir: Directory for backup file. Defaults to path.parent.

    Raises:
        BibWriteError: If the roundtrip test fails.
    """
    serialized = bibtexparser.write_string(library)

    # Roundtrip safety check
    try:
        reparsed = bibtexparser.parse_string(serialized)
    except Exception as e:
        raise BibWriteError(str(path), f"Roundtrip parse failed: {e}") from e

    _check_roundtrip(library, reparsed, path)

    # Backup and atomic write
    if path.exists():
        bak_dir = backup_dir or path.parent
        bak_dir.mkdir(parents=True, exist_ok=True)
        bak_path = bak_dir / (path.name + ".bak")
        shutil.copy2(path, bak_path)

    tmp_path = path.with_suffix(".bib.tmp")
    tmp_path.write_text(serialized, encoding="utf-8")
    tmp_path.replace(path)


def _check_roundtrip(
    original: bibtexparser.Library,
    reparsed: bibtexparser.Library,
    path: Path,
) -> None:
    """Verify that serialization didn't lose or corrupt entries.

    Raises:
        BibWriteError: If entries were lost or fields changed count.
    """
    orig_keys = {e.key for e in original.entries}
    reparse_keys = {e.key for e in reparsed.entries}

    lost = orig_keys - reparse_keys
    if lost:
        raise BibWriteError(
            str(path),
            f"Roundtrip lost {len(lost)} entries: {', '.join(sorted(lost)[:5])}",
        )

    gained = reparse_keys - orig_keys
    if gained:
        raise BibWriteError(
            str(path),
            f"Roundtrip gained {len(gained)} unexpected entries: "
            f"{', '.join(sorted(gained)[:5])}",
        )

    # Check field counts per entry
    orig_by_key = {e.key: e for e in original.entries}
    reparse_by_key = {e.key: e for e in reparsed.entries}
    for key in orig_keys:
        orig_count = len(orig_by_key[key].fields)
        reparse_count = len(reparse_by_key[key].fields)
        if orig_count != reparse_count:
            raise BibWriteError(
                str(path),
                f"Entry '{key}' had {orig_count} fields before write, "
                f"{reparse_count} after roundtrip.",
            )


def generate_key(surname: str, year: int, existing_keys: set[str]) -> str:
    """Generate a bib key from author surname and year.

    Handles collisions by appending letter suffixes (a, b, c, ...).

    Args:
        surname: First author's surname (will be lowercased, stripped of
            non-alpha characters).
        year: Publication year.
        existing_keys: Set of keys already in use.

    Returns:
        A unique bib key like 'xu2022' or 'xu2022a'.
    """
    clean = "".join(c for c in surname.lower() if c.isalpha())
    if not clean:
        clean = "unknown"

    base = f"{clean}{year}"
    if base not in existing_keys:
        return base

    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = f"{base}{suffix}"
        if candidate not in existing_keys:
            return candidate

    raise ValueError(f"Exhausted key suffixes for '{base}'")


def get_x_field(entry: Entry, field: str) -> str | None:
    """Get the value of an x-field, or None if not set."""
    f = entry.fields_dict.get(field)
    return f.value if f else None


def get_tags(entry: Entry) -> list[str]:
    """Get the x-tags as a list of strings."""
    raw = get_x_field(entry, "x-tags")
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]
