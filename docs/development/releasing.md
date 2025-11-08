# Release Process

This document describes how to create and publish a new release of `live-vlm-webui` to PyPI.

## Overview

We use **GitHub Releases** to trigger automated publishing to PyPI. This approach:
- ✅ Creates a tagged release with release notes
- ✅ Automatically builds and publishes to PyPI
- ✅ Attaches wheel artifacts to the GitHub Release
- ✅ Uses PyPI Trusted Publishing (no API tokens needed)

## Prerequisites

### One-time Setup: PyPI Trusted Publishing

Configure PyPI to trust GitHub Actions (more secure than API tokens):

1. Go to [PyPI Publishing Settings](https://pypi.org/manage/account/publishing/)
2. Add a new "pending publisher":
   - **PyPI Project Name**: `live-vlm-webui`
   - **Owner**: `NVIDIA-AI-IOT`
   - **Repository**: `live-vlm-webui`
   - **Workflow**: `build-wheel.yml`
   - **Environment**: (leave blank or use `release`)

3. Click "Add"

Once configured, GitHub Actions can publish without API tokens!

## Version Numbering

Follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html):

- **Patch** (`v0.1.1`): Bug fixes, no API changes
- **Minor** (`v0.2.0`): New features, backwards compatible
- **Major** (`v1.0.0`): Breaking changes, incompatible API changes

**Pre-releases:**
- Alpha: `v0.2.0-alpha.1` (early testing, unstable)
- Beta: `v0.2.0-beta.1` (feature complete, testing)
- Release Candidate: `v0.2.0-rc.1` (final testing before release)

## Release Workflow

### 1. Prepare the Release

On the `main` branch:

```bash
# 1. Pull latest changes
git checkout main
git pull origin main

# 2. Update version in pyproject.toml
vim pyproject.toml
# Change: version = "0.2.0"

# 3. Update CHANGELOG.md
vim CHANGELOG.md
# Move [Unreleased] items to [0.2.0] section
# Add release date: ## [0.2.0] - 2025-11-08

# 4. Commit version bump
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to 0.2.0"

# 5. Push to main
git push origin main
```

### 2. Create GitHub Release

1. Go to [Releases](https://github.com/NVIDIA-AI-IOT/live-vlm-webui/releases)
2. Click **"Draft a new release"**
3. Fill in:
   - **Tag**: `v0.2.0` (creates tag automatically)
   - **Target**: `main`
   - **Title**: `v0.2.0 - Brief description`
   - **Description**:
     ```markdown
     ## What's New

     - Added configurable GPU monitoring interval
     - Fixed Docker build issues
     - Improved system stats display

     ## Installation

     ```bash
     pip install live-vlm-webui==0.2.0
     ```

     See [CHANGELOG.md](https://github.com/NVIDIA-AI-IOT/live-vlm-webui/blob/main/CHANGELOG.md) for full details.
     ```

4. Check **"Set as the latest release"** (for production releases)
5. Check **"Create a discussion for this release"** (optional)
6. Click **"Publish release"**

### 3. Automated Publishing

GitHub Actions will automatically:

1. ✅ Build the wheel
2. ✅ Run tests (if configured)
3. ✅ Publish to PyPI (via trusted publishing)
4. ✅ Attach wheel to GitHub Release

**Monitor the workflow:**
- Go to [Actions](https://github.com/NVIDIA-AI-IOT/live-vlm-webui/actions)
- Check the `build-wheel.yml` workflow run
- Verify all steps complete successfully

### 4. Verify the Release

```bash
# 1. Wait ~5-10 minutes for PyPI to update

# 2. Install from PyPI
pip install --upgrade live-vlm-webui==0.2.0

# 3. Verify version
python -c "import live_vlm_webui; print(live_vlm_webui.__version__)"

# 4. Test basic functionality
live-vlm-webui --help
```

### 5. Post-Release

- [ ] Update documentation if needed
- [ ] Announce on relevant channels
- [ ] Close related milestone (if using GitHub Milestones)
- [ ] Update any downstream projects

## Release Checklist

Quick reference for releases:

**Pre-release:**
- [ ] All tests passing on main
- [ ] Update version in `pyproject.toml`
- [ ] Update `CHANGELOG.md` with release notes
- [ ] Review open issues/PRs for blockers
- [ ] Test Docker builds locally (optional)

**Release:**
- [ ] Commit and push version bump
- [ ] Create GitHub Release with tag `vX.Y.Z`
- [ ] Monitor GitHub Actions workflow
- [ ] Verify PyPI upload successful

**Post-release:**
- [ ] Test PyPI installation
- [ ] Verify wheel functionality
- [ ] Update documentation
- [ ] Announce release

## Emergency: Yanking a Release

If you need to remove a broken release from PyPI:

```bash
# Install twine
pip install twine

# Yank the release (requires PyPI credentials)
twine yank live-vlm-webui 0.2.0 --reason "Critical bug, use 0.2.1 instead"
```

Then immediately:
1. Fix the issue
2. Release a patch version (e.g., `v0.2.1`)

## Troubleshooting

### PyPI Upload Fails

**Error: "Project name not found on PyPI"**
- Solution: First release must be uploaded manually or project must exist on PyPI
- Create project on PyPI first, or use TestPyPI for testing

**Error: "Invalid or non-existent authentication"**
- Solution: Verify PyPI Trusted Publishing is configured correctly
- Check repository, workflow name, and owner match exactly

**Error: "File already exists"**
- Solution: You cannot re-upload the same version
- Increment version and create a new release

### GitHub Actions Workflow Fails

1. Check [Actions logs](https://github.com/NVIDIA-AI-IOT/live-vlm-webui/actions)
2. Look for specific error messages
3. Common issues:
   - Build failures: Check dependencies in `pyproject.toml`
   - Test failures: Fix tests before releasing
   - Upload failures: Check PyPI Trusted Publishing config

## Testing Releases

Use [TestPyPI](https://test.pypi.org/) for testing the release process:

1. Configure TestPyPI trusted publishing (separate from PyPI)
2. Modify workflow to upload to TestPyPI
3. Test installation:
   ```bash
   pip install --index-url https://test.pypi.org/simple/ live-vlm-webui
   ```

## References

- [Semantic Versioning](https://semver.org/)
- [Keep a Changelog](https://keepachangelog.com/)
- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
- [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github)

