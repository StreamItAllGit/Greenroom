# @module greenroom.collectors.dat_verifier
# @purpose Facade re-exporting No-Intro/Redump DAT parser, 4-mode sidecar hasher, and hash matcher modules.
# @inputs DAT files, ROM/ISO paths, and SQLite games/dat_entries tables
# @outputs Unified DAT import, hash calculation, and game verification interface

"""Known-Good ROM/ISO Hash Verifier and No-Intro / Redump DAT Importer for Greenroom."""

from greenroom.collectors.dat_hasher import (
    _FORBIDDEN_HASH_FILENAMES,
    _HASH_CHUNK_SIZE,
    _ROM_INNER_EXTS,
    _is_safe_hash_target,
    compute_file_hashes,
)
from greenroom.collectors.dat_matcher import (
    LAST_VERIFY_STATS,
    lookup_dat_entry_by_hash,
    run_verify_hashes,
    verify_single_game_hash,
)
from greenroom.collectors.dat_parser import (
    _CLRMAME_ROM_RE,
    _MAX_DAT_UNCOMPRESSED_BYTES,
    _extract_clrmame_field,
    _iter_clrmame_blocks,
    detect_dat_source,
    ingest_dat_text,
    parse_clrmamepro_dat_text,
    parse_xml_dat_text,
    run_import_dats,
)

__all__ = [
    "_HASH_CHUNK_SIZE",
    "_MAX_DAT_UNCOMPRESSED_BYTES",
    "_FORBIDDEN_HASH_FILENAMES",
    "_ROM_INNER_EXTS",
    "_CLRMAME_ROM_RE",
    "_is_safe_hash_target",
    "_iter_clrmame_blocks",
    "_extract_clrmame_field",
    "detect_dat_source",
    "parse_xml_dat_text",
    "parse_clrmamepro_dat_text",
    "ingest_dat_text",
    "run_import_dats",
    "compute_file_hashes",
    "lookup_dat_entry_by_hash",
    "verify_single_game_hash",
    "run_verify_hashes",
    "LAST_VERIFY_STATS",
]
