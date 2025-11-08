# Release Checklist

Quick reference for maintainers creating a release. See [releasing.md](releasing.md) for full details.

## Quick Steps

### 1. Prepare Release

```bash
# Update version
vim pyproject.toml  # version = "X.Y.Z"

# Update changelog
vim CHANGELOG.md    # Move Unreleased → [X.Y.Z]

# Commit
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to X.Y.Z"
git push origin main
```

### 2. Create GitHub Release

1. Go to: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/releases/new
2. **Tag**: `vX.Y.Z` (creates tag automatically)
3. **Target**: `main`
4. **Title**: `vX.Y.Z - Brief description`
5. **Description**: Add release notes (see examples below)
6. Click **"Publish release"**

### 3. Verify

```bash
# Wait 5-10 minutes for PyPI to propagate

# Install and test
pip install --upgrade live-vlm-webui==X.Y.Z
python -c "import live_vlm_webui; print(live_vlm_webui.__version__)"
live-vlm-webui --help
```

### 4. Monitor

- GitHub Actions: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/actions
- PyPI: https://pypi.org/project/live-vlm-webui/

## Checklist Template

Copy this checklist when creating a release:

**Pre-Release:**
- [ ] All tests passing on `main`
- [ ] Update version in `pyproject.toml`
- [ ] Update `CHANGELOG.md` with release notes
- [ ] Review open PRs for any last-minute inclusions
- [ ] Commit and push version bump

**Release:**
- [ ] Create GitHub Release with tag `vX.Y.Z`
- [ ] Monitor GitHub Actions build
- [ ] Verify PyPI upload successful

**Post-Release:**
- [ ] Test PyPI installation
- [ ] Verify basic functionality works
- [ ] Update documentation if needed
- [ ] Announce release

## Release Notes Template

### For Feature Releases (minor version)

```markdown
## What's New in v0.2.0

### ✨ New Features
- Added configurable GPU monitoring interval (#2)
- New Mac platform support with Apple Silicon detection
- System stats UI with product-specific icons

### 🐛 Bug Fixes
- Fixed Docker workflow paths after PyPI restructuring (#3)
- Fixed Jetson Thor pip wheel upgrade issue (#4)

### 📚 Documentation
- Added comprehensive release process guide
- Improved installation instructions

### 🔧 Infrastructure
- Restructured project for PyPI packaging
- Added automated wheel building workflow

## Installation

\```bash
pip install live-vlm-webui==0.2.0
\```

See [CHANGELOG.md](CHANGELOG.md) for complete details.
```

### For Patch Releases (bug fixes)

```markdown
## Bug Fixes in v0.1.1

- Fixed critical GPU monitoring crash on Ubuntu 24.04
- Resolved Docker image build failures

## Installation

\```bash
pip install live-vlm-webui==0.1.1
\```
```

## Version Numbering Guide

- **Patch** (`v0.1.1`): Bug fixes only, no new features
- **Minor** (`v0.2.0`): New features, backwards compatible
- **Major** (`v1.0.0`): Breaking changes

**Pre-releases:**
- `v0.2.0-alpha.1` - Early testing
- `v0.2.0-beta.1` - Feature complete, needs testing
- `v0.2.0-rc.1` - Release candidate, final testing

## Done! 🚀

Full documentation: [releasing.md](releasing.md)

