# Documentation

## General Documentation (Committed to Git)

User-facing and contributor documentation:

### User Documentation
- **`troubleshooting.md`** - Common issues and solutions
- **`setup/`** - Setup guides for different platforms
- **`usage/`** - Usage guides and operational information
  - **`gpu-monitoring.md`** - Understanding GPU utilization behavior
  - **`advanced-configuration.md`** - Advanced configuration options

### Developer Documentation (`development/`)

Testing guides for developers:

- **`development/testing.md`** - Comprehensive testing and performance guide
- **`development/testing-quickstart.md`** - Quick start guide for all testing commands
- **`development/performance-regression-testing.md`** - Complete regression testing strategy
- **`development/multi-hardware-testing.md`** - Strategy for testing across different hardware
- **`development/regression-testing-quickref.md`** - Quick reference for regression testing

Also see:
- `../tests/README.md` - Testing infrastructure guide
- `../tests/e2e/README.md` - E2E testing with Playwright
- `../CONTRIBUTING.md` - Contribution guidelines

## Per-Developer Personal Reference Documentation (Cursor User Only, Not Committed)

Located in `cursor/` directory - **Cursor SDK places per-developer, development-specific reference docs here.**

This directory is for your personal reference only:
- ✅ Created by Cursor AI during your development sessions
- ✅ Explains decisions and context specific to your work
- ✅ Addresses your specific questions and issues athe point of development time
- ❌ **NOT found in upstream repo** (each developer has their own)
- ❌ **NOT committed to git** (`.gitignore`d)

**Example files you might have** (not in repo):
- `INTEGRATION_VS_E2E_EXPLANATION.md` - Explanation of testing approaches
- `YOUR_FEATURE_DESIGN.md` - Design decisions for your feature
- `DEBUGGING_SESSION_NOTES.md` - Notes from debugging a specific issue

These provide context for your development work but aren't shared with other developers.

## Documentation Guidelines

### Commit to Git (docs/)
- ✅ General user/contributor documentation
- ✅ Written for any reader
- ✅ Objective, not conversational
- ✅ Permanent reference material

### Keep Local (docs/cursor/)
- 📝 Conversational explanations
- 📝 Addresses specific issues ("your Docker issue")
- 📝 Context from AI development sessions
- 📝 Decision rationale for specific features

