---
status: complete
phase: 5
updated: 2026-05-21
---

# Implementation Plan

## Goal
Package CPA as both a pip-installable Python package and a PyInstaller one-file standalone binary for Linux and Windows, with release automation and versioning.

## Context & Decisions
| Decision | Rationale | Source |
|----------|-----------|--------|
| Both pip + PyInstaller | User explicitly chose both approaches | user preference |
| PyInstaller one-file mode | Cleaner distribution, single executable | user preference |
| Package name `ruida-protocol-analyzer` | Descriptive, no PyPI conflicts; CLI stays `cpa` | user preference |
| Shell scripts kept as-is | No need to convert; repo helpers remain | user preference |
| No project restructuring needed | Flat-layout works with setuptools `py-modules` + `packages.find` | project structure analysis |
| Version from single source | `pyproject.toml` is the canonical version; `importlib.metadata` reads it | discussion |
| Versioned release directory | `release/cpa-<version>/` keeps artifacts organized | discussion |

## Phase 1: Packaging Fixes [COMPLETE]
- [x] Fix `pyproject.toml` build backend (`setuptools.backends._legacy` → `setuptools.build_meta`)
- [x] Verify `pip install -e .` works
- [x] Verify all module imports (cpa, cpalib, protocols.ruida) work after install
- [x] Update `decode` script to prefer `cpa` command over `python cpa.py`

## Phase 2: Build Infrastructure [COMPLETE]
- [x] Create `cpa.spec` — PyInstaller spec for one-file binary
- [x] Create `build.sh` — Linux build script with venv auto-setup
- [x] Create `build.ps1` — Windows build script with venv auto-setup
- [x] Make `build.sh` executable
- [x] Add "Building a Standalone Binary" section to README

## Phase 3: Build & Test Binary [COMPLETE]
- [x] 3.1 Verify PyInstaller build succeeds
- [x] 3.2 Run `dist/cpa --help` to verify binary works
- [x] 3.3 Run binary against a test capture from `discovery/`
- [x] Fix Fedora flexiblas compatibility (exclude flexiblas from bundled binaries in `cpa.spec`)
- [x] Bundle package metadata (`copy_metadata`) so `--version` shows `0.1.0` not `0.1.0-dev`
- [x] Add `--version` / `-v` flags to `cpa.py`

## Phase 4: Release Automation & Versioning [COMPLETE]
- [x] 4.1 Create `release.sh` — builds pip wheel, PyInstaller binary, SHA256 checksums
- [x] 4.2 Create `release.ps1` — Windows equivalent
- [x] 4.3 Add `release/` to `.gitignore`
- [x] 4.4 Verify release artifacts built in `release/cpa-0.1.0/`: pip wheel (55K), standalone binary (41MB), SHA256SUMS
- [x] 4.5 Fix `build.sh --onefile` conflict with `.spec` file

## Phase 5: Distribution [COMPLETE]
- [x] 5.1 Write a script to publish to PyPI (`publish.sh`, `publish.ps1`)
- [x] 5.2 Write a script to create GitHub release with attached binaries (`github-release.sh`, `github-release.ps1`)
- [x] 5.3 Add GitHub Actions workflow for automated builds (`.github/workflows/release.yml`)
- [x] 5.4 Add build matrix (Linux, Windows, macOS) in CI workflow

## Notes
- 2026-05-21: Phase 3 completed — PyInstaller builds on Fedora 43 with flexiblas workaround, binary tested against capture logs. `--version` flag added to `cpa.py`.
- 2026-05-21: Phase 4 completed — release.sh/ps1 created with versioned artifacts, SHA256 checksums, mutual-exclusive flag validation.
- 2026-05-21: Phase 5 completed — publish.sh/ps1 (PyPI upload), github-release.sh/ps1 (GitHub release), and .github/workflows/release.yml (CI build matrix for Linux/Windows/macOS) created.
