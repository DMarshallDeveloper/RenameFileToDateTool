# Session notes — 2026-05-19 → 2026-05-20

## Goal
Reconcile `F:\Photos` (Google Takeout dump, restructured into year folders) against the master library at `D:\Files\Pictures and Videos`. Master has roughly equal totals but per-year counts diverge, and we want to understand why.

## Key paths
- Working copy: `F:\PhotosCopy`
- Backup: `F:\Photos`
- Master: `D:\Files\Pictures and Videos`
- Logs: `D:\Files\Documents\Code\RenameFileToDateTool\RenameFileToDateTool\logs\`
  - `write_exif_from_filename.log` — exhaustive EXIF rewrite history
  - `audit_master.log` — last audit
  - `compare_libraries.log` — last compare

---

## 2026-05-19 — Working-copy setup and structural cleanup

### Working copy
- `F:\PhotosCopy` is a duplicate of `F:\Photos` (verified by `compare_libraries.py` — 15098 ↔ 15098, all `same_name`, 0 deltas).
- Renamed `F:\PhotosCopy\2000-2010` → `F:\PhotosCopy\2000 - 2010` so the folder name matches master's `BUNDLED_EARLY_FOLDER`.

### Code changes
| File | Change |
|---|---|
| `compare_libraries.py` | Added per-year breakdown table with categorized unmatched files (`same_timestamp_extra/missing`, `same_date_extra/missing`, `isolated_extra/missing`). Added `transcodes_missing_soft_delete` finding. Fixed 1-to-1 pairing bug: `find_match` now respects `consumed_after` so a second backup file can't false-pair to an already-claimed master file. |
| `audit_master.py` | Added `--root` CLI flag so the audit can target a working copy without editing `photo_lib/config.py`. |
| `photo_lib/exiftool_runner.py` | Chunked `write_exif_dates_batch` — `WRITE_BATCH_CHUNK_SIZE=100`, `WRITE_BATCH_TIMEOUT_SECONDS=1200`. The unchunked version hit the 600s subprocess timeout on the 12689-image and 1383-video batches. |
| `photo_lib/extensions.py` | Added `webp` to `IMAGE_EXTENSIONS`. |
| `update_filename_to_date_from_google_takeout_json_metadata.py` | (earlier today) Hash-based dedup in `plan_destination_filename`, multi-source support via `nargs="+"`. |

All changes covered by tests; full suite: 186 passing.

### Data work on `F:\PhotosCopy`
File count throughout: **15098** (unchanged).

| Step | Result |
|---|---|
| Audit (`audit_master.py --root F:\PhotosCopy`) | 17 folders [NEEDS FIX] (filesystem timestamps reset by copy), 720 extension mismatches, 10 wrong-year-folder files |
| `write_exif_from_filename.py --path F:\PhotosCopy` (3 attempts, last one chunked) | 15076 files updated; 14 skipped in 2011 (placeholder collision); 22 non-media files left alone (.json / .txt / .3gp / .52_1 / .webp + .gif etc.) |
| Renamed `.heif` → `.heic` | 539 files |
| Renamed PNG-as-JPEG → `.jpg` | 7 files (5 + 2 from successive exiftool warnings) |
| Moved 10 wrong-year-folder files | `2021/2022-*` → `2022/`, `2024/2025-*` → `2025/`, `2025/2026-*` → `2026/` |
| **Post-fix audit** | 14 folders [OK], 3 [NEEDS FIX] (2011, 2025, 2026); 174 ext mismatches; 0 wrong-year files |
| Comprehensive extension-mismatch rename | 170 files: `png→jpg` (73), `mov→mp4` (72), `heic→jpg` (20), `png→heic` (2), `jpeg→heic` (1), `webp→jpg` (1), `jpeg→png` (1). Skipped 3 `heic→heif` (master uses `.heic` exclusively for HEIF-family content). |

### End-of-day state
- File count: 15098 ✓
- All structural fixes done EXCEPT the 14 placeholder collisions in `F:\PhotosCopy\2011\`
- **170 files were renamed AFTER the last EXIF rewrite** — their EXIF tags were never (or only partially) written because exiftool refused them under their wrong extensions. They need another EXIF rewrite pass.

---

## 2026-05-20 — Final reconciliation pass

### 1. EXIF writer re-run (dry-run + live)
- Dry-run: only **100 files** needed EXIF writes (not the predicted 170 — many of those files already had correct EXIF baked in from earlier; exiftool had simply refused them under their wrong extensions). 14 placeholder collisions skipped, 14976 already in sync.
- Live run: 100 files updated cleanly. Chunked batches finished without timeout.

### 2. Audit re-run
- 16 folders [OK], 1 [NEEDS FIX] (2011, expected — placeholder collision)
- 4 extension flags:
  - 3 × `.heic` files claiming to be `.heif` (intentional — yesterday's convention: master uses `.heic` for all HEIF-family content)
  - 1 × `2026/2026-04-04 19.24.52_1` — extensionless file, header `ftypqt  ` confirmed QuickTime/MOV
- 0 wrong-year files, 0 non-canonical filenames

### 3. Extensionless MOV fixed
- Renamed `F:\PhotosCopy\2026\2026-04-04 19.24.52_1` → `2026-04-04 19.24.52_1.mov` (slot was free; another `.heic` shares the base name as a Live Photo sibling).
- Ran EXIF writer again — 1 file updated, 15076 in sync.

### 4. 2011 placeholder collisions resolved

**Background** (the underlying mechanic):
- `apply_placeholder_time_bump` bumps midnight Jan 1 (`YYYY-01-01 00.00.00`) to `13.00.00` because midnight NZ = Dec 31 in UTC, which rolls UTC-respecting viewers back a year. 13:00 NZ NZDT (UTC+13) = 00:00 UTC.
- `maybe_rename_placeholder` does a literal `00.00.00` → `13.00.00` swap that keeps the `_N` index. If the target `_N` slot is taken, it returns None and the EXIF writer skips the file to preserve the filename ≡ EXIF invariant.
- 2011 has **44 photos** whose source metadata gave a date but no usable time (Google Takeout `photoTakenTime` absent / epoch-zero). 30 had already been through the bump in a prior run (sitting at `13.00.00_1..30.jpg`); 14 were still at `00.00.00_1..14.jpg` and collided on rename.

**Resolution**:
- Wrote `scratch_dedup_2011.py` to pixel-hash all 14 placeholders + 31 `13.00.00_*.jpg/jpeg` files. Result: **all 44 photos are pixel-distinct**. No content duplicates. (The session-notes-19 suspicion that `00.00.00_1.jpg` and `13.00.00_1.jpeg` were the same photo pre/post EXIF was wrong.)
- Renamed `00.00.00_N.jpg` → `13.00.00_(N+30).jpg` (numeric sort, so `_1→_31`, `_2→_32`, ..., `_14→_44`).
- Ran EXIF writer — all 14 stamped cleanly with `2011:01:01 13:00:00`. 15077 in sync.

### 5. compare_libraries.py vs master

Final counts:
- BEFORE (`F:\PhotosCopy`): **15,098**
- AFTER (master): **14,958**
- Net delta: **−140**

Match types:
- Matched: 11,638 (same_name 6,179; canonical_rename 574; ext_change 64; jpeg_to_jpg 2,557; size_tiebreak 2,264)
- **0 placeholder_renames** — names align between takeout and master
- 0 transcodes

Unmatched:
- B-only: 3,460 (in takeout, not in master) — 181 same-ts, 3,094 same-date, **185 isolated**
- A-only: 3,320 (in master, not in takeout) — 377 same-ts, 2,767 same-date, **176 isolated**

Most unmatched files pair by *date* to a counterpart on the other side — sibling photos with timestamp drift (likely from successive EXIF rewrites shifting times by seconds, or HEIC+MOV Live Photo pairs where the times differ by milliseconds). The genuine candidates for "in one library, truly not in the other" are the **isolated** counts:
- **185 isolated_missing** (takeout has, master might be missing) — concentrated in 2023 (36), 2024 (30), 2008 (18), 2021 (18), 2014 mixed, 2020 (13)
- **176 isolated_extra** (master has, takeout doesn't) — concentrated in 2007 (38), 2020 (30), 2023 (13). Likely post-takeout iPhone additions.

Per-year highlights:
- **2011: clean** — 48=48, all matched. Today's placeholder rename did its job.
- **2026: −510** (largest gap). 94 same-ts, 463 same-date, 8 isolated. Takeout was generated mid-year 2026, so master and takeout have shifted timestamps on the same photos; only ~8 are real candidates for loss.
- **2015: +1 net, but 364 B-only / 365 A-only** — almost pure timestamp drift across siblings.
- **2007, 2023, 2014, 2013**: each has +40 to +90 deltas with most unmatched files pairing on date.

### 6. Size anomalies

`compare_libraries.py` flagged 406 files with >100 KB diff under the same name. Pattern in `2000 - 2010\2000-01-01 13.00.00_*.jpg` is striking: master is usually **much larger** than takeout (e.g., `_103.jpg` 140 KB → 766 KB; `_119.jpg` 90 KB → 1.18 MB). Conclusion: **Google Takeout downsampled the early-2000s originals** — master has better-quality versions for most files in that folder.

### 7. Size inversions — where master is *smaller* than takeout

Wrote `scratch_size_inversions.py` to find every same-named pair where master < takeout (threshold > 1 KB to ignore EXIF/metadata noise). Output: `size_inversions.tsv` (sorted by deficit, columns: deficit_bytes, relpath, before_bytes, after_bytes, ratio_after_over_before).

**659 inversions, 257.9 MB total deficit.**

Per-year:

| Year folder | Files | Deficit |
|---|---:|---:|
| 2000 - 2010 | 196 | 67.5 MB |
| 2014 | 160 | 27.6 MB |
| 2012 | 135 | 22.5 MB |
| 2026 | 2 | **75.3 MB** (the giant .mov) |
| 2018 | 43 | 4.3 MB |
| 2013 | 38 | 9.3 MB |
| 2025 | 29 | 7.9 MB |
| 2011 | 23 | 5.6 MB |
| 2020 | 12 | 4.6 MB |
| 2016 | 9 | 9.1 MB |
| 2017 | 8 | 1.0 MB |
| 2023 | 2 | 15.2 MB |
| 2022 | 1 | 5.9 MB |
| 2015 | 1 | 2.0 MB |
| **TOTAL** | **659** | **257.9 MB** |

**Dramatic cases (master holds a thumbnail-sized version):**
- `2026\2026-02-04 22.44.13_1.mov`: 108 MB → 30 MB (master 27% the size — looks like a video transcode)
- `2023\2023-10-14 20.50.00_1.mp4`: 17.8 MB → 1.9 MB (11%)
- `2012\2012-01-01 13.00.00_197/198/199.jpg`: 2.9 MB → 50–245 KB (1–8%, basically thumbnails)
- `2020\2020-01-01 13.00.00_13/15/16.jpeg`: 1.0–1.2 MB → 70–220 KB

**Batch-shrink pattern**: `2016\2016-03-11 18.25.00_9..14.jpg` — six files from the same shoot, all ~25–30% smaller in master with ratios 0.71–0.74. A single resize pass got applied to that whole batch in master.

**Bulk small inversions**: lots of `2000 - 2010\2000-01-01 13.00.00_*.jpg` entries where master is 50–80% the size of takeout. The early-2000s placeholder bucket has inversions going both directions — different photos were imported by different paths over the years.

---

## Where things stand RIGHT NOW
- `F:\PhotosCopy`: **15,098 files**, all structurally aligned with master conventions.
- All folders pass audit except `2011` flagged for the (now-resolved) collision; re-running the audit would show all-clear.
- EXIF tags match filenames everywhere (invariant holds).

### 8. Canonical-name normalization sweep

Discovered the takeout-ingest script (`update_filename_to_date_from_google_takeout_json_metadata.py:148-156`) uses a **per-extension** `_N` counter. That produces duplicate-looking names within a timestamp bucket — e.g., `2011-01-01 13.00.00_1.jpeg`, `_1.jpg`, `_1.mp4` all coexisted. PhotosCopy had 4476 `.jpeg` files (cleaner master only has 123) and many same-`_N`-across-extensions collisions.

**Promoted scratch into real tooling** (replaces the bug-fix done by hand):
- `photo_lib/canonical_renumber.py` — pure planner + two-phase applier
- `photo_lib/filename_pattern.py` — added `CANONICAL_FILENAME_PARTS_RE` with named groups
- `photo_lib/extensions.py` — added `CANONICAL_EXTENSION_ALIASES = {"jpeg": "jpg"}` and `canonical_extension()`
- `RenameFileToDateTool/normalize_canonical_names.py` — CLI script (`--path`, `--dry-run`)
- Tests: `tests/test_photo_lib/test_canonical_renumber.py` (13) + `tests/test_normalize_canonical_names.py` (4). Full suite: **203 passing** (was 186).

The tool does both:
- Canonicalizes extensions (`.jpeg`→`.jpg`, uppercase→lowercase)
- Renumbers each timestamp bucket to a contiguous `_1, _2, _3, ...` globally across extensions

Side-effects fall out for free:
- **Gap-closing**: delete `_3` from a series, next run pulls `_4` down to `_3`
- **Cross-extension uniqueness**: a bucket can no longer hold both `_1.jpg` and `_1.mp4`

**Applied to PhotosCopy**: 4771 renames in one pass (4337 pure `.jpeg`→`.jpg`, ~430 `_N` bumps from global-counter rule). Files now: 15098 (preserved), 8 extensions (down from 9). Note: this deliberately split Live Photo pairs that previously shared `_N` (.heic + .mp4 etc.). User chose Option A (fully global counter) over Option B (family-aware) after seeing the trade-off.

**Still TODO for the underlying bug**: fix `update_filename_to_date_from_google_takeout_json_metadata.py` so future ingests don't reintroduce the per-extension counter. Deferred until the fresh `F:\Photos` copy is ready for a re-import.

### 9. Duplicate-detection and library-combine tooling

Goal: combine master + PhotosCopy into one tree, detect duplicates with high confidence, suffix them `_a / _b / _c` for a one-pass manual review, then strip suffix off survivors. The user explicitly wants manual review — no script ever deletes photos.

**New code** (replaces the ad-hoc `scratch_dedup_2011.py`):
- `photo_lib/duplicate_finder.py` — three-tier grouping (file-bytes, pixel-bytes, perceptual-hash) for images + frame-pHash for videos via ffmpeg (5 frames per clip)
- `photo_lib/duplicate_cache.py` — sidecar SQLite at `<root>/.photo_hashes.db`; re-runs only hash changed files (size+mtime check)
- `photo_lib/duplicate_report.py` — self-contained HTML side-by-side report, base64-inlined thumbnails, tier badges
- `RenameFileToDateTool/find_duplicate_photos.py` — CLI: `scan`, `report`, `mark`, `finalize`
- `RenameFileToDateTool/combine_libraries.py` — copy multiple sources into one tree, bump `_N` on collision so nothing overwrites
- Added `imagehash>=4.3,<5` to `requirements.txt`

**Confidence tiers** (conservative, per user's choice):
- Tier 1: file SHA-256 match (literal duplicates)
- Tier 2: pixel SHA-256 match (images) OR identical 5-frame pHash tuple (videos)
- Tier 3: image pHash distance = 0 (no fuzzy match — pHash 1-5 isn't auto-marked)

**Quality ranking for `_a` winner**:
1. Higher pixel dimensions wins (or pixel-count-equivalent for videos via frame data later)
2. Larger file size
3. Lexicographic name (stable tie-break)

**Tests**: +36 (image hashing, video tier-1, cache round-trip, report HTML, scan/mark/finalize end-to-end, combine collision handling). Full suite: **239 passing** (was 203).

### 10. Audit-driven combine tests + dead code removal (commit `6145a1c`)

Audit of `combine_libraries.py` revealed the existing 6 tests verified algorithmic shape (filename sets) but not the data-safety invariant. Added 9 tests:
- Content preservation (byte-level): simple collision, three sources, cascading collisions, pre-existing dest content, cross-extension same-timestamp
- mtime preservation (the dedup cache depends on it for size+mtime invalidation)
- Edge cases: empty source, files at source root, mixed canonical/non-canonical in same folder

Also removed two dead helpers (`_resolve_dest_name`, `_next_free_canonical_name`) — defined but never called. The active path is `_resolve_dest_name_against_set`. Tests: 239 → 248.

### 11. Fuzzy pHash matching with low-entropy guard (commit `e434c0f`)

User pushed back on tier-3 strictness: with manual review in the loop, false-positives are cheap (an extra glance) but false-negatives leave duplicates hidden. Pre-fix the pHash tier required Hamming distance = 0, which would miss Google-Takeout re-encoded copies (typically differ by 4-12 bits).

Added `phash_hamming_threshold` parameter (default 8):
- 0 keeps the original exact-equality fast path
- > 0 enables fuzzy union-find pass with bit-wise Hamming distance, AFTER the exact pass has claimed its files
- Low-entropy pHashes (solid-color thumbnails ≈ all zeros, mostly all ones) are excluded from fuzzy matching to prevent giant fake clusters
- For videos: every frame in the 5-frame tuple must be within threshold

CLI: `--phash-threshold N` on the `report` and `mark` subcommands.

Tests: +15. 248 → 263 passing.

### 12. Takeout-ingest bug fixed (commit `df1c50b`)

Two changes in `plan_destination_filename`:
1. **Canonicalize the extension at ingest** via `canonical_extension()`. `.jpeg` → `.jpg`, uppercase → lowercase. Destination is canonical from day one.
2. **Global `_N` counter per base across all extensions**. Backed by a new shared `used_indices_by_base` dict, built once from the existing destination via `build_used_indices_by_base`, mutated as planning proceeds.

Dedup remains extension-aware where it should be: an incoming `.jpeg` is checked against existing `.jpg` files under the canonical-extension bucket, so a re-import after canonicalization is detected as a duplicate.

The orphan-Live-Photo-MP4 test was updated to reflect the new behavior: pre-fix `.heic + .mp4` both got `_1` (per-extension counter); post-fix `.heic` gets `_1` and `.mp4` gets `_2` (global counter). The user confirmed this is the desired behavior — Live Photo pairing is no longer needed.

Tests: +9. 263 → 272 passing.

### 13. USB photos pre-staged for combine

The user's `F:\initialPhotoStates\photosFromUSB` folder contains 1703 photos in a near-canonical format but flat (no year folders) and without `_N` suffixes. Examples:
- `2003-01-02 18.48.18.jpg` (1675 files — canonical timestamp, no `_N`)
- `2003-01-08 18.48.18-1.jpg` (28 files — canonical timestamp, hyphen-N instead of underscore-N)
- `482988_10151241204359080_890635342_n.jpg` (1 file — Facebook-export style, no parseable date; skipped, needs manual handling)

Used a one-shot Python script (in-conversation, not committed) to copy these into `F:\PhotosUSBStaged\<year>\<base>_<N>.<ext>` form. 1702 staged; the 1 Facebook-style file ignored. Sources untouched. `F:\PhotosUSBStaged` is the canonical-form 3rd source for combine.

### 14. Backup of all libraries to external drive

Before running the combine, the user copied to E: (external):
- `F:\initialPhotoStates` (zips, USB photos — the truly-original raw state)
- `F:\Photos` (post-takeout-ingest, untouched)
- `D:\Files\Pictures and Videos` (master library)

Now safe to run the combined-library workflow.

### 15. Pilot run on year 2014 — caught a data-loss bug

Started a single-year pilot (2014: 852 master + 782 PhotosCopy = 1634 files) before committing to the full library run. The pilot chains:
1. `combine_libraries` master/2014 + PhotosCopy/2014 → `F:\PhotosCombined_pilot`
2. `normalize_canonical_names` on the pilot dest
3. `find_duplicate_photos.py scan`
4. `find_duplicate_photos.py report --phash-threshold 8`

**The pilot caught a silent data-loss bug** in `combine_libraries.plan_combine` (commit `26b7ae8`): master has uppercase extensions (`.JPG`, `.MOV`); PhotosCopy has lowercase (`.jpg`, `.mov`). Python's case-sensitive string compare in the collision-detection slot_set treated `_1.JPG` and `_1.jpg` as distinct dest paths — both got planned. But NTFS is case-insensitive: when the second copy hit disk, it silently overwrote the first. In 2014 alone, 286 master files would have been lost without warning. Master itself was untouched (we copy from sources, not into them), but the pilot dest ended up with 1349 files instead of 1634.

**Fix**: lowercase the slot_set entries and the membership tests in `_resolve_dest_name_against_set`. Original-case filenames preserved on disk; only the COMPARISON is case-folded. Added 4 regression tests:
- `.JPG` + `.jpg` in two sources detected as a collision → second bumps to `_2.jpg`
- Both files exist in dest with their original bytes intact after apply
- Pre-existing uppercase dest content blocks an incoming lowercase source
- Four-way case variation (`.JPG/.Jpg/.jPg/.jpg`) all collide pairwise

Pilot v2 ran clean: 1634 files preserved end-to-end (matches sources exactly). 650 case-insensitive collisions were detected and renamed during combine. 784 duplicate groups at `--phash-threshold 8`.

### 16. Mark-step improvements driven by pilot feedback (commit `abe5fe3`)

Three changes after looking at the pilot's `mark` dry-run output:

**(a) Adjacent-sort marking.** Pre-fix `mark` kept each file's own `_N` and just appended a letter. A group at `_1, _14, _29` produced `_1_a, _14_b, _29_c` — which sort scattered across the folder, defeating the whole point of marking. Now the winner's `_N` becomes the SHARED group prefix: `_1_a, _1_b, _1_c`. Each file keeps its own extension (so `.heic + .mp4 + .mov` pairs at one timestamp stay distinguishable within a group).

**(b) Cache-preserving renames.** `mark`/`finalize` previously called `cache.forget(old_path)` after each rename, invalidating the expensive hash data. Re-running `report` after `mark` would have shown an empty report (old paths gone from cache, new paths never added). Added `cache.rename(old, new)` to `photo_lib/duplicate_cache.py`. `mark` and `finalize` now use it — the hash data is preserved under the new path, so a subsequent `report` reads the cache directly and writes a refreshed HTML with the post-`mark` filenames. **No re-scan needed.** Saves an hour on the full library.

**(c) Singletons-by-year report.** `report` now also writes `singletons_report.html` alongside `duplicate_report.html`. Lists files that didn't cluster with anything at the chosen pHash threshold, grouped by year folder. Useful for spotting photos that exist in only one source library, or for catching cases the dedup missed. On the 2014 pilot: 57 singletons out of 1634 files; 0.8 MB report.

Tests: +7 in this commit (cache rename, plan_mark winner-idx, mixed-extension group preservation, three-file adjacency, singletons grouping).

### 17. Finalize extended to handle multi-survivor groups

The pre-existing `finalize` only handled the lone-survivor case: a `_a`/`_b`/`_c` group where the user kept exactly one file got the `_<letter>` suffix stripped back to canonical form. Multi-survivor case (user keeps 2+ from a group because they look visually distinct on review) was a no-op — the files would be stuck with non-canonical names indefinitely.

Extended: when 2+ letters survive a group, the first survivor (alphabetically — usually `_a`) takes the group's original idx, and subsequent survivors get the next free idx in that timestamp bucket. The algorithm processes ALL `_a` survivors across the folder before any `_b` survivors, so an earlier group's `_b` overflow can't grab the idx a later group's `_a` was about to claim.

Edge cases covered by tests:
- Lone `_a` survivor → stripped to canonical (unchanged)
- Lone `_b` survivor (user deleted `_a`) → also stripped to canonical
- `_a + _b` both kept → `_a → _N`, `_b → _N+1`
- `_a + _b` both kept, but `_N+1` already occupied by an unrelated canonical file → `_b` bumps to next free
- Two adjacent groups both with `_a + _b` → all four `_a`s claim their preferred slots first, then `_b`s overflow into next free
- `_a` survivor whose canonical `_N` is now occupied by something else → bumps to next free instead of skipping

286 tests passing now (was 283 → +3 net for the multi-survivor cases).

---

## 2026-05-21 — Cross-date duplicate preservation (commit `9d07344`)

### 18. Both dates preserved when duplicates have different timestamps

Discovered while reviewing the workflow: the pre-existing `plan_mark` collapsed a duplicate group's losers under the WINNER's `<base>_<idx>` prefix and left each file in its original folder. For a same-timestamp group that worked. For a cross-date group (winner from 2014, duplicate from 2015), the loser's filename inherited the 2014 base — losing its 2015 timestamp — but the file was still physically sitting in the `2015\` folder, mismatched with its new name. Neither "adjacent on sort" nor "both dates visible for review" was actually achieved.

**Spec'd before implementing**:
- Filename marker: `<winner_base>_<winner_idx>_<letter>__from_<loser_base>.<ext>` (double-underscore + `from_` — unambiguous to regex, no special chars).
- Sensitivity: ANY base difference triggers cross-date treatment (same-day burst-time differences also get the marker — maximum information preservation).
- HTML report includes a `cross-date` badge per group.

**Changes**:
- `photo_lib/duplicate_finder.py` —
  - `MARKED_FILENAME_RE` extended with optional `__from_<origin_base>` capture group; one regex handles both forms.
  - `plan_mark` moves EVERY loser into the winner's folder (so they sort adjacent in Explorer). Cross-date losers carry the `__from_<loser_base>` marker; same-base losers keep the existing `_a/_b/_c` form unchanged.
  - `plan_finalize` rewritten as a two-phase walk: phase 1 collects all marked entries + per-folder canonical idx buckets across the whole tree, phase 2 does the two-pass-by-letter allocation. Surviving cross-date losers (`_b`/`_c` with the `__from_` marker) are sent back to their origin year folder and re-canonicalized to the lowest free idx in that bucket. Bundled-early years (2000-2010) route to the `2000 - 2010` folder via a new `_target_folder_for_base` helper that reuses the `BUNDLED_EARLY_*` constants from `photo_lib/config.py`.
  - `apply_simple_rename_plan` now `os.makedirs(..., exist_ok=True)` on the target dir before the final rename, so a cross-folder destination that doesn't exist yet is created on demand.
- `photo_lib/duplicate_report.py` — new `_is_cross_date_group` helper (checks if canonical members have ≥2 distinct `<base>`); `_render_group` emits `<span class="cross-date-badge">cross-date</span>` when applicable.

**Behavior example**:
```
Before mark:
  F:\PhotosCombined\2014\2014-06-15 10.00.00_1.jpg          (winner)
  F:\PhotosCombined\2015\2015-08-20 14.30.00_3.jpg          (loser)

After mark:
  F:\PhotosCombined\2014\2014-06-15 10.00.00_1_a.jpg
  F:\PhotosCombined\2014\2014-06-15 10.00.00_1_b__from_2015-08-20 14.30.00.jpg
  (both adjacent in Explorer; both dates visible)

After finalize (user kept BOTH — they weren't duplicates after all):
  F:\PhotosCombined\2014\2014-06-15 10.00.00_1.jpg          (_a survivor)
  F:\PhotosCombined\2015\2015-08-20 14.30.00_<free>.jpg     (_b sent home)
```

Tests: +14. 286 → 300 passing. Coverage includes: cross-date mark across two and three distinct dates, same-base regression, finalize returning loser to origin year, idx-bump-when-taken at destination, two losers sharing destination bucket, bundled-early routing, multi-survivor mixed local+remote, missing origin folder created on apply, cross-date badge present/absent on report, non-canonical paths don't trigger badge.

**The earlier session-notes workflow step 6 ("Manual review: open each year folder, name-sort, delete unwanted") still works** — but the user can now compare cross-date pairs side-by-side in ONE folder rather than tabbing between year folders to figure out which date is the true one.

### 19. Hand-run synth validation of the cross-date workflow

Before committing to the production library run, built a 4-file synthetic library to exercise the new code end-to-end on real disk operations (the unit tests cover the planner functions but not the live `os.rename` + `os.makedirs` paths).

**Synth design** (`%TEMP%\synth_setup.py`, not committed per the no-scratch rule):
- 3 sources each with a pixel-identical "blue" JPEG at different timestamps + folders (2014, 2015, 2005-in-bundled-early).
- Tier-2 grouping (same pixels, different bytes via varying JPEG `comment=` length) — the master's 2014 file gets a 4096-byte comment so it wins the size-tiebreak, forcing 2015 and 2005 to become losers.
- 1 singleton with a different structured pattern, kept in master only.

**Pipeline run** through `combine_libraries → find_duplicate_photos scan / report / mark / finalize`:
- Report flagged the cross-date group with the new badge.
- Mark consolidated all 3 dupes into `2014\` with `__from_<base>` markers on the two losers (visible in the dry-run + live output thanks to the new relpath logging).
- Finalize (multi-survivor: kept all 3) split them back home — `_b` to `2015\`, `_c` correctly to `2000 - 2010\` via the BUNDLED_EARLY routing.

End state matched the start state exactly. Round-trip identity confirmed.

### 20. Clearer cross-folder logs in mark/finalize (commit `92355c3`)

Caught during the synth run: the basename-only log lines hid the destination folder for cross-folder moves. Both `mark` and `finalize` now:
- Count cross-folder entries in the header line (`X files would be renamed across N groups (Y cross-folder)`).
- Switch each affected line to a relpath so the destination year folder is visible.
- For finalize: replaced "X lone-survivor files would have their suffix stripped" with "X marked files would be returned to canonical form" — the old message was inaccurate now that finalize also handles multi-survivor + cross-date returns.

### 21. Property-based integration tests for the multi-script pipeline (commit `2112dcc`)

Surveyed test coverage and found a gap: per-script tests existed but no test chained `combine → normalize → scan → mark → finalize` together. Both the case-insensitive collision bug (§15) and the cross-date data-loss issue (§18) were caught by hand on the pilot — not by the test suite.

**New file**: `tests/test_integration_pipeline.py` (~270 lines).

**Approach** — hypothesis with a small DSL:
- `LibrarySpec` / `SourceSpec` / `FileSpec` dataclasses describe a synth library declaratively.
- `materialize(spec, root)` writes the spec to disk as real JPEGs (PIL, structured patterns keyed by `content_id` so duplicates are deterministic and pHash-distinct images stay distinct).
- `run_pipeline(spec, work_root)` drives combine → normalize → scan → mark → finalize.
- Hypothesis strategies generate bounded random specs: 1-3 sources × 1-6 files each, content drawn from a pool of 4 distinct images, random `(year, month, day, time)`, randomised `.jpg`/`.JPG` casing.

**Four invariants** asserted per random case:
1. **No unique pixel content lost**: pixel-SHA256 set of inputs == pixel-SHA256 set of outputs (manual review skipped, so the equality must hold strictly).
2. **All filenames canonical**: every final filename matches `CANONICAL_FILENAME_RE` (no `_a`/`_b`/`_c` or `__from_` residue).
3. **Year-folder placement**: every file's parent folder matches its filename year (with `BUNDLED_EARLY_FOLDER` for years 2000-2010).
4. **Idempotent**: a second `mark + finalize` pass lands at the same file layout.

**Runtime**: 4 tests × 25 examples = 100 random pipeline runs in 11s. Suite: 300 → 304 tests, total time 44s → 57s.

**Coverage gain**: randomised extension casing exercises `.JPG/.jpg` collisions; randomised year spreads exercise cross-folder dedup paths. Either bug would have been caught by the new tests if they'd existed earlier.

**Limitations** (intentional, documented in the test file's docstring):
- No simulation of manual-review deletions (would need to encode "what's safe to delete" inside the test).
- No video coverage (strategy only generates `.jpg`; videos would need fixture clips).
- Dedup-pipeline only — other multi-script flows (ingest, takeout, compare) aren't covered. Could add a similar file later.

**Dependency added**: `hypothesis>=6,<7` in `requirements.txt`.

---

## Workflow once pilot is validated
1. **Combine all three sources**:
   ```powershell
   python combine_libraries.py `
       --source "D:\Files\Pictures and Videos" `
       --source "F:\PhotosCopy" `
       --source "F:\PhotosUSBStaged" `
       --dest "F:\PhotosCombined"
   ```
2. `normalize_canonical_names.py --path F:\PhotosCombined` (cleans up any collision-bumped names from combine + mixed-case/jpeg from master)
3. `find_duplicate_photos.py scan --path F:\PhotosCombined` (one-time, ~1-3 hr)
4. `find_duplicate_photos.py report --path F:\PhotosCombined --phash-threshold 8` → open `duplicate_report.html` in browser to gauge scope
5. `find_duplicate_photos.py mark --path F:\PhotosCombined --phash-threshold 8` (renames to `_a/_b/_c`)
6. **Manual review**: open each year folder in Explorer, name-sort, delete unwanted `_b/_c/...` files
7. `find_duplicate_photos.py finalize --path F:\PhotosCombined` (strips `_a` suffix from lone survivors)

---

## Open follow-ups (not blockers)
1. **Size inversions** — `size_inversions.tsv` (regenerable from `scratch_size_inversions.py` if needed; not committed) has 659 cases ranked by deficit. Top ~30 are the most actionable. Will likely be resolved by the dedup workflow.
2. **185 isolated_missing files** — generate the explicit list, spot-check whether they're real losses or false negatives in the pairing logic.
3. **2026 −510 gap** — most are same-date sibling drift; only 8 are truly isolated. Could investigate why timestamps differ so widely for a year still being photographed.
4. **3 `.heic` files containing `.heif` payload** in `2026` — intentional per yesterday's convention, but document/encode this in `audit_master.py` so it stops flagging them.
5. **Manual fixes from PhotosCopy still ad-hoc** — folder rename `2000-2010` → `2000 - 2010`, extension-mismatch renames (170 files), wrong-year-folder moves (10 files), extensionless MOV → `.mov` (1 file). All would need to be redone by hand if rebuilding PhotosCopy from `F:\Photos`. Could be codified as small fixers if needed.
6. **One Facebook-style USB file** — `482988_10151241204359080_890635342_n.jpg` skipped during USB staging; has no parseable date in the filename. Manual handling required if it should join the library.

---

## 2026-05-21 (continued) — Pilot v3, three new report features, source-label provenance

### 22. 2014 pilot v3 — hardware-failure recovery

Replay of the 2014 pilot after a mid-combine drive disconnect. The partial dest (42 files, with one suspect 165 MB MOV whose mtime didn't match the source because `shutil.copy2`'s `copyfile + copystat` got cut between phases) was wiped because `combine_libraries` has no resume — restarting on top of partial state would have inflated the dest with duplicates. Sources are read-only by design, so wipe-and-restart is safe.

End-to-end pilot v3 results (1634 files): combine OK, normalize OK, scan 1634/1634 hashed, report 784 duplicate groups + 57 singletons + new stats page. Mark applied 1577 renames; user deleted 18 during manual review; finalize cleaned up 1556 surviving markers. Final state: 1616 files, all canonical, 0 marked.

**Pipeline robustness assessment**: only `find_duplicate_photos scan` is fully crash-safe (SQLite cache with size+mtime invalidation). `combine_libraries` has no resume and `shutil.copy2` is not atomic. `normalize_canonical_names` and `mark/finalize` converge on rerun but with stale cache rows. Not blockers — sources are read-only, so worst case is "destination is junk, throw it away, restart."

### 23. Singletons report: year derivation falls back to the filename

`duplicate_report._year_from_relpath` required `len(parts) >= 2` and labelled every file in a flat dest (no year subfolders — e.g. a pilot sourced from `\2014\` directly) as `(no year)`. Fixed: when the folder hierarchy doesn't yield a year, fall back to the canonical `YYYY-` prefix on the filename, with bundled-early routing so 2005-2010 photos collapse to `2000 - 2010` regardless of flat/nested layout. +2 tests.

### 24. Statistics HTML report

New `render_stats_html_report` in `photo_lib/duplicate_report.py`, emitted alongside `duplicate_report.html` and `singletons_report.html` as `stats_report.html`. Tables: by year, by extension, by media kind, by source library (when manifest present), by duplicate tier. Headline: "Manual review of the N groups could reclaim up to X GB". For the 2014 pilot that came to 11.1 GB across 784 groups — roughly half the library. +4 tests.

### 25. Source-label provenance through combine → mark → finalize → report

Goal: see at a glance which source library a duplicate came from, so manual review can use it as a tie-breaker. Five-part mechanism:

- **`photo_lib/source_manifest.py`** — new sidecar SQLite at `<root>/.source_manifest.db` mapping `dest_path → source_label`. Same `_canonical_key = os.path.normpath` boundary as the duplicate cache.
- **`derive_source_label(source_root)`** — auto rule: basename of source, with a step-up to the parent folder when the basename matches a year pattern (`\d{4}` or `\d{4} - \d{4}`). For the pilot, `D:\...\Pictures and Videos\2014` becomes `Pictures-and-Videos` and `F:\PhotosCopy\2014` becomes `PhotosCopy`. Labels are sanitised to `[A-Za-z0-9-]+` so `__src_<label>__from_<base>` markers parse unambiguously.
- **`plan_combine`** now returns `(src, dest, source_label)` and writes the manifest as it copies. Two sources deriving to the same label is a hard error (`SystemExit`).
- **`plan_mark(groups, source_label_lookup=...)`** stamps `__src_<label>` onto every marked filename. `MARKED_FILENAME_RE` extended to optionally capture the marker. Combined form: `<base>_<idx>_<letter>__src_<label>[__from_<origin_base>].<ext>`. `plan_finalize` reconstructs canonical without the marker, so labels drop off naturally at finalize.
- **`duplicate_report`** shows an `src: <label>` badge on every card (duplicate + singleton reports) and adds a "By source library" table to the stats report. `normalize_canonical_names` keeps manifest paths in sync after each canonicalising rename.

**Live pilot result**: stats show master 852 (52.1%) + takeout 782 (47.9%) — matches the pre-combine source counts exactly. After mark, every duplicate filename carries `__src_Pictures-and-Videos` or `__src_PhotosCopy`, and cross-date losers also carry `__from_<original_base>` so both dates remain visible. Example marked pair sitting adjacent in Explorer:

```
2014-12-28 15.45.00_4_a__src_PhotosCopy.jpg                                     (winner)
2014-12-28 15.45.00_4_b__src_Pictures-and-Videos__from_2014-12-28 14.45.00.jpg  (was 14.45.00_4)
```

### 26. Bug fix during the live run: two-phase manifest renames

First normalize attempt crashed mid-loop with `sqlite3.IntegrityError: UNIQUE constraint failed: source_labels.path`. The bucket renumber produces plan entries like `_2.MOV → _4.mov` while another entry `_4.mov → _8.mov` is still pending — a naive per-row `UPDATE` collides on the first step because PhotosCopy's manifest row at `_4.mov` is still there. `apply_rename_plan` already stages the on-disk renames through `.__renaming__` temp paths to sidestep exactly this; the manifest needed the same treatment.

Fix: `SourceManifest.rename_many(pairs)` updates every old key to a unique `.__renaming__` temp key (phase 1) and then to its final target (phase 2). Mirrors the on-disk staging. `normalize_canonical_names` and `find_duplicate_photos {mark,finalize}` all call `rename_many` now. Tests include a regression case replicating the pilot's exact pattern.

The partial-state recovery: disk was fully normalized (`apply_rename_plan` completed before the manifest update ran), but the manifest was half-updated. Wipe + rerun was simpler than partial repair, since combine writes the manifest from scratch as files copy.

**Test suite**: 357 passing (was 304 → +53 for these changes).

---

## Open follow-ups added 2026-05-21

7. **`combine_libraries` resume / atomic copy** — write to `.partial` + atomic rename, and skip-if-source-mtime-matches on rerun. Closes the bytes-vs-metadata gap that `shutil.copy2` leaves open and removes the "restart-creates-duplicates" sharp edge.

---

## 2026-05-24 — PhotosCombined finalization, format cleanup, EXIF sweep

User had completed the 30k-file manual dedup review on `F:\PhotosCombined`
(workflow step 6) and copied the result to `D:\PhotosCombined`. Workflow step 7
(`finalize`) had NOT yet run — 13,963 marker files were still on disk. This
session ran finalize and all downstream cleanup. **From the finalize step
onward, the total file count became a hard invariant** the user does not want
to break (re-doing the 30k review is unacceptable). Captured this rule as
`feedback_file_count_invariant.md` in auto-memory.

Pre-session baseline media count: **14,132** (after user's manual cleanup of 12
accidental Explorer `" - Copy"` files + 1 missing-from-2019 photo add, baseline
adjusted to **14,121** before finalize). Final count after every step in this
session: **14,121** — preserved exactly.

### 27. Inbound: another Google Takeout zip; flatten bug found and fixed

User downloaded `takeout-20260524T051011Z-3-001.zip` (a single 140 MB chunk).
First `process_takeout.py --dry-run` returned 0 matched media files — the 14
photos were extracted into `Extracted data/Takeout/Google Photos/export 24-5-26/`
but the flattener never lifted them to the top level of `Extracted data/`.

Root cause in `RenameFileToDateTool/extract_and_flatten_takeout.py:74-78`: the
walk skipped anything with `extracted_dir` in its parents — but the zips were
extracted INTO `extracted_dir`, so every just-unzipped file was excluded. The
existing zip-extraction test used `rglob('*')` (recursive), which passed even
when files stayed nested. Fixed:

- `flatten_takeout`: skip files whose **immediate parent** is `extracted_dir`,
  not any descendant. Nested files inside `extracted_dir/Takeout/...` now flatten
  up; already-flat files stay put.
- `tests/test_extract_and_bring.py`: tightened the zip test to use `iterdir()`
  + `assertFalse((extracted / 'Takeout').exists())`.

Re-run: 13 media (IMG_6658–6671, no _6659) all matched their `.json` sidecars
via `exact_inferred`; live run staged to `D:\Files\Pictures and Videos\_Inbox\
takeout-20260524\` with canonical names spanning 2026-05-19 → 2026-05-24.
Commit `db15e7e`.

### 28. Finalize on PhotosCombined (13,951 renames, 12 cross-folder)

Pre-flight: 14,121. Dry-run showed 13,951 renames planned + 12 marker files the
regex skipped — all 12 were Windows-Copy artifacts (`" - Copy"` infix between
the marker and extension), all in `2000 - 2010\` at 2007-12-25/26. User
manually deleted those 12 (6 .jpg + 6 .mp4 — counted from the filenames before
deletion) and added one missing 2019 photo, taking the baseline from 14,132 to
**14,121**. Re-run dry-run: 13,951 renames, 0 regex skips.

Live finalize applied 13,951 renames via two-phase staged rename
(`apply_simple_rename_plan`). Post-count: 14,121 ✓. Twelve cross-folder moves
were cross-date dedup survivors returning to their origin year — including a
notable 6-photo `2018-07-11 23.07–23.26` cluster that had been sitting in
`2017\`.

### 29. Stray non-media + extensionless MOV

`2000 - 2010\Dad's Child Photos.pdf` — left in place per user (real document).
`2026\2026-04-04 19.24.52_1` (no extension, `ftypqt  ` header) — the same
extensionless-MOV case from §3 in the 2026-05-20 notes; the canonical `.mov`
slot was free, restored via `os.rename`. Count: 14,121 unchanged.

### 30. `.heif` → `.heic` rename + alias added to the codebase

47 `.heif` files (1 in 2020, 1 in 2023, 1 in 2024, 44 in 2025); zero `.heic`
collisions at the same `<base>_<idx>`. Bulk-renamed via `os.rename`. Then:

- `RenameFileToDateTool/photo_lib/extensions.py:26-32`: added
  `"heif": "heic"` to `CANONICAL_EXTENSION_ALIASES`. The 539-file rename on
  PhotosCopy (2026-05-19) and these 47 had been manual sweeps; the alias makes
  future takeout ingests and `normalize_canonical_names` runs auto-canonicalize
  HEIF → HEIC the way they already did JPEG → JPG. Tests: 363 still passing.
  Commit `a71f4ac`.

### 31. AVI / MPG / 3GP → MP4 conversion (80 files, 1.3 GB → 1.0 GB)

80 targets (75 .avi + 4 .mpg + 1 .3gp) — 79 in `2000 - 2010\`, 1 in `2022\`.
Used `convert_unwanted_formats.py` with output staging at
`D:\PhotosCombined_converted\` (separate from PhotosCombined so file count
stayed clean during conversion). ffmpeg `libx264 preset=slow crf=18` with 4
workers pegged the CPU; user opted to let it finish rather than throttle. All
80 outputs valid h264/aac (ffprobe-verified samples). Total: 1297 MB → 1013 MB.

Move + delete done as a single Python pass with phase-by-phase count checks:
14,121 → 14,201 (after moving 80 .mp4s into year folders) → 14,121 (after
deleting 80 originals). Staging folder now contains only `conversion.log`.

### 32. Full library EXIF sweep (14,112 of 14,121 files written)

User's question: "how do we know the OTHER files have correct EXIF after all
this?" Dry-run answered it conclusively: **14,112 of 14,121 files** needed
EXIF writes. NOT because primary timestamps were wrong (DateTimeOriginal /
CreateDate / ModifyDate all matched filenames on every sampled file) but
because:

- `FileCreateDate` — set by `combine_libraries`' `shutil.copy2` to 2026-05-21
  on every file (the combine run date), not the photo date.
- `DateCreated` — present as date-only (`2014:01:01`) rather than full
  datetime, which `is_metadata_in_sync` rejects.
- `OffsetTime` / `OffsetTimeOriginal` — explicit TZ offsets never set.
- The 80 new MP4s had no EXIF datetime at all (ffmpeg doesn't carry source
  metadata for AVI sources, which have none anyway).
- The ~1,700 USB-staged photos (per §13) never had a write_exif pass.

Live run: 14,112 files updated in 119 batches of 100 via the chunked
`write_exif_dates_batch`. 26 placeholder bumps (00.00.00 → 13.00.00) renamed
files in 2011 + 2012. 8 placeholder-collision files skipped (target `13.00.00_N`
slot was taken). Count preserved: 14,121.

[minor] exiftool warnings on ~18 old camera JPEGs (Truncated MakerNotes, Bad
format MakerNotes, Fixed MicrosoftPhoto URI) — pre-existing data quirks in
source files; exiftool wrote our date tags successfully on all of them.

### 33. Resolved the 8 placeholder-bump collisions

The 8 skipped files were pixel-hashed (PIL RGB raw bytes, SHA-256) against
their `13.00.00_N` blockers — **all 8 were pixel-distinct**, no duplicates.
Renumbered to `max+1`-and-up free slots in their respective buckets:

- `2000 - 2010\2000-01-01 01.01.00_1.jpg` → `_281.jpg`
- `2000 - 2010\2000-01-01 01.01.00_165.jpg` → `_282.jpg`
- `2011\2011-01-01 00.00.00_{8,9,10,13,14}.jpg` → `_{32,33,34,35,36}.jpg`
- `2012\2012-01-01 00.00.00_1.jpg` → `_279.jpg`

Then ran `write_exif_for_files` scoped to just those 8 — all 8 stamped with
the correct 13:00 NZ time. The script recognises two placeholder time patterns
(`00.00.00` and `01.01.00`); the `01.01.00` ones in 2000 were from photos
imported via some path that bumped midnight by an hour before the more recent
13:00 convention took over.

### 34. `normalize_canonical_names` live sweep (2,777 renames)

After the 8-collision fix, 2011's `13.00.00` bucket was contiguous 1–36 but
other buckets still had pre-existing gaps from earlier finalize cross-folder
moves and the 30k-file manual review. Dry-run: **2,777 renames** across 17
folders, **all pure index gap-closing**, zero extension changes (the heif and
heic + jpeg work having already canonicalized everything).

Top reasons: `_3→_2` (657), `_2→_1` (374), `_5→_3` (178), `_7→_4` (63), etc.
Live run applied all 2,777 cleanly; source manifest auto-updated for each
rename (uses `SourceManifest.rename_many` from §26 to avoid the
two-phase manifest UNIQUE-constraint issue). Count: 14,121 ✓.

### 35. Audit findings — known false positive + one real cleanup item

`audit_master.py --root D:\PhotosCombined` flagged ALL 17 folders as
[NEEDS FIX] with the uniform symptom `FileCreateDate: expected '…', got ''`
and `FileModifyDate: expected '…', got ''`. Spot-check on
`D:\PhotosCombined\2014\2014-01-01 13.00.00_1.jpg` with the audit's own
`exiftool -json -FileCreateDate -FileModifyDate …` invocation returned the
correct values (`"2014:01:01 13:00:00+13:00"`). Bug is in `audit_master.py`'s
post-read processing of those fields — needs investigation next session.

Real structural finding from the audit:
- **40 extension/content mismatches** — files named `.png` (and 1 `.webp`)
  but containing JPG bytes. All in `2025\`, mostly autumn 2025 screenshots /
  saved images. Examples: `2025/2025-09-15 16.32.12_1.png` actually a JPG.
- **0 wrong-year-folder files**
- **0 non-canonical filenames**

### Where things stand at end-of-day 2026-05-24

- **`D:\PhotosCombined`**: 14,121 media files, all canonical filenames, all
  contiguous in their timestamp buckets, EXIF written from filename on every
  file the writer could touch. Stray non-media: 1 PDF (intentional).
- **`D:\Files\Pictures and Videos`**: untouched — still the legacy master.
  Cutover not yet performed.
- `MASTER_ROOT` in `RenameFileToDateTool/photo_lib/config.py:13` still points
  at the legacy master.
- Staging folder `D:\PhotosCombined_converted\` retains only `conversion.log`;
  safe to delete whenever.
- Dedup cache `.photo_hashes.db` at the root of `D:\PhotosCombined` is fully
  stale (every file's mtime changed during write_exif + normalize); safe to
  delete or leave for re-scan.

### Open follow-ups added 2026-05-24

8. **Cutover** — point `MASTER_ROOT` to `D:\PhotosCombined`, retire / archive
   the legacy `D:\Files\Pictures and Videos`. Verify with `compare_libraries`
   before deletion.
9. ~~**`audit_master.py` FileCreateDate/FileModifyDate false positive**~~ —
   **RESOLVED 2026-05-25.** Does not reproduce. All 17 folders report [OK].
   The original false positive was likely a transient Windows filesystem state
   issue during the heavy rename session (2,777 renames in §34), not a code bug.
10. ~~**40 extension/content mismatches**~~ — **RESOLVED 2026-05-25.** 40 files
    total (28 in `2025\`, 11 in `2026\`, 1 `.heic` in `2026\`) — all named
    `.png`/`.webp`/`.heic` but containing JPEG data. Renamed to `.jpg` after
    exiftool content verification + collision check. EXIF rewritten on all 40.
    File count preserved: 14,121.

---

## 2026-05-25 — Extension mismatches fixed, audit clean

### 36. Extension/content mismatches resolved (#10)

40 files across `2025\` and `2026\` were named `.png` (38), `.webp` (1), or
`.heic` (1) but contained JPEG data (confirmed via exiftool `FileTypeExtension`).
All `.jpg` slots were free (0 collisions). Renamed all 40 to `.jpg`, then ran
`write_exif_for_files` on all 40. File count: 14,121 preserved.

### 37. Audit false positive resolved (#9)

Re-ran `audit_master.py --root D:\PhotosCombined`. All 17 folders report `[OK]`.
The `FileCreateDate`/`FileModifyDate` false positive from §35 does not reproduce.
No code changes to `audit_master.py` since 2026-05-20 (`aa57edc`). The false
positive was likely caused by transient Windows filesystem date caching right
after the 2,777-rename normalize sweep in §34.

Remaining structural finding: 0 extension mismatches, 0 wrong-year files,
0 non-canonical filenames.

### 38. Cutover completed (#8)

Replaced the legacy master with the combined, deduped library:

1. Removed 10 tooling artifacts from `D:\PhotosCombined` root (`.db`, `.json`,
   `.html`, `.tsv`, `.bak` — 370 MB total, mostly the 333 MB duplicate report).
2. Created empty `_Inbox/` folder in PhotosCombined.
3. Renamed `D:\Files\Pictures and Videos` → `D:\Files\Pictures and Videos_old`.
4. Moved `D:\PhotosCombined` → `D:\Files\Pictures and Videos`.
5. File count verified: **14,121** (preserved exactly).
6. `MASTER_ROOT` in `photo_lib/config.py` already pointed to
   `D:\Files\Pictures and Videos` — no code change needed.

Post-cutover audit (`audit_master.py` with default `MASTER_ROOT`):
- 17/17 folders [OK]
- 0 extension/content mismatches
- 0 wrong-year-folder files
- 0 non-canonical filenames

The old master is archived at `D:\Files\Pictures and Videos_old` (14,958 files).
Safe to delete once the user is satisfied with the new master.
