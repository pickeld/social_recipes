# Changelog

## [1.3.0] - 2026-01-14

### New Features

- Centralized logging system across all modules for better debugging and traceability
- HuggingFace token configuration for authenticated model downloads
- Added `ingredientReferences` field to Mealie recipe instructions

### Bug Fixes

- Fixed database file path consistency with `ui/database.py`
- Updated Dockerfile to install curl and unzip dependencies
- Fixed PWA share_target to use POST method with proper enctype
