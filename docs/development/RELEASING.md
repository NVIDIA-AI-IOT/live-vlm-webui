# Release Process Guide

This document describes the step-by-step process for creating and publishing a new release of live-vlm-webui.

## Table of Contents

1. [Pre-Release Checklist](#pre-release-checklist)
2. [Version Bump](#version-bump)
3. [Create Git Tag](#create-git-tag)
4. [GitHub Release](#github-release)
5. [Docker Image Verification](#docker-image-verification)
6. [PyPI Upload](#pypi-upload)
7. [Post-Release Tasks](#post-release-tasks)

## Pre-Release Checklist

Before starting the release process, ensure:

- [ ] All planned features and bug fixes are merged to `main`
- [ ] All tests pass: `pytest tests/`
- [ ] Code quality checks pass: `./scripts/pre_commit_check.sh`
- [ ] Documentation is up to date
- [ ] CHANGELOG.md is updated with all changes since last release
- [ ] Version number follows [Semantic Versioning](https://semver.org/)
  - **MAJOR**: Breaking changes
  - **MINOR**: New features (backwards compatible)
  - **PATCH**: Bug fixes (backwards compatible)

## Version Bump

### 1. Update Version in pyproject.toml

Edit `pyproject.toml` and update the version:

```toml
[project]
name = "live-vlm-webui"
version = "0.2.0"  # Update this
```

### 2. Update CHANGELOG.md

Add a new section for the release:

```markdown
## [0.2.0] - 2025-01-15

### Added
- Feature X
- Feature Y

### Changed
- Improved Z

### Fixed
- Bug fix A
```

### 3. Commit Version Bump

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "Bump version to 0.2.0"
git push origin main
```

## Create Git Tag

Git tags trigger the Docker image builds with version-specific tags.

### 1. Create and Push Tag

```bash
# Create annotated tag (recommended for releases)
git tag -a v0.2.0 -m "Release version 0.2.0"

# Push tag to GitHub
git push origin v0.2.0
```

**Important**: The tag format must be `vX.Y.Z` (with the `v` prefix) for the Docker workflow to recognize it as a semver tag.

### 2. Verify Tag

```bash
# List tags
git tag -l

# Show tag details
git show v0.2.0
```

## GitHub Release

### 1. Navigate to Releases

Go to: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/releases

### 2. Create New Release

Click "Draft a new release"

### 3. Fill Release Information

- **Tag**: Select the tag you just pushed (v0.2.0)
- **Release title**: `v0.2.0 - Brief Description`
- **Description**: Copy relevant sections from CHANGELOG.md and add:
  - Overview of changes
  - Installation instructions
  - Breaking changes (if any)
  - Known issues (if any)
  - Contributors acknowledgment

### 4. Publish Release

Click "Publish release"

## Docker Image Verification

After pushing the git tag, the GitHub Actions workflow automatically builds and publishes Docker images.

### 1. Monitor Workflow

1. Go to: https://github.com/NVIDIA-AI-IOT/live-vlm-webui/actions
2. Find the "Build and Push Docker Images" workflow
3. Verify all builds complete successfully:
   - Multi-arch (amd64 + arm64)
   - Jetson Orin
   - Jetson Thor
   - Mac

### 2. Verify Published Images

Check that the following tags exist at:
https://github.com/NVIDIA-AI-IOT/live-vlm-webui/pkgs/container/live-vlm-webui

**Base multi-arch images:**
- `ghcr.io/nvidia-ai-iot/live-vlm-webui:0.2.0`
- `ghcr.io/nvidia-ai-iot/live-vlm-webui:0.2`
- `ghcr.io/nvidia-ai-iot/live-vlm-webui:latest`

**Platform-specific images:**
- `ghcr.io/nvidia-ai-iot/live-vlm-webui:0.2.0-jetson-orin`
- `ghcr.io/nvidia-ai-iot/live-vlm-webui:0.2.0-jetson-thor`
- `ghcr.io/nvidia-ai-iot/live-vlm-webui:0.2.0-mac`
- `ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson-orin`
- `ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-jetson-thor`
- `ghcr.io/nvidia-ai-iot/live-vlm-webui:latest-mac`

### 3. Test Docker Images

Test the version selection feature:

```bash
# List available versions
./scripts/start_container.sh --list-versions

# Test interactive version picker
./scripts/start_container.sh

# Test specific version
./scripts/start_container.sh --version 0.2.0

# Test latest
./scripts/start_container.sh --version latest
```

## PyPI Upload

### 1. Build Distribution

```bash
# Clean previous builds
rm -rf dist/ build/ *.egg-info

# Build wheel and source distribution
python -m build
```

### 2. Test Upload (TestPyPI)

```bash
# Upload to TestPyPI first
python -m twine upload --repository testpypi dist/*

# Test installation from TestPyPI
pip install --index-url https://test.pypi.org/simple/ live-vlm-webui==0.2.0
```

### 3. Production Upload

```bash
# Upload to PyPI
python -m twine upload dist/*
```

### 4. Verify PyPI Page

Visit: https://pypi.org/project/live-vlm-webui/

Ensure:
- Correct version is displayed
- README renders correctly
- Project links work
- Classifiers are correct

## Post-Release Tasks

### 1. Announce Release

- [ ] Update project README if needed
- [ ] Post announcement in project channels
- [ ] Update documentation links if API changed
- [ ] Notify downstream users of breaking changes (if any)

### 2. Monitor Issues

- [ ] Watch for issues related to the new release
- [ ] Respond promptly to installation/upgrade problems
- [ ] Create patch release if critical bugs found

### 3. Update Roadmap

- [ ] Mark completed items in ROADMAP.md or TODO.md
- [ ] Plan next release milestones

## Quick Reference Commands

```bash
# Complete release workflow
git tag -a v0.2.0 -m "Release version 0.2.0"
git push origin v0.2.0

# Monitor workflow
gh workflow view "Build and Push Docker Images"

# Verify Docker images
docker pull ghcr.io/nvidia-ai-iot/live-vlm-webui:0.2.0
./scripts/start_container.sh --version 0.2.0

# Build and upload to PyPI
python -m build
python -m twine upload dist/*
```

## Rollback Procedure

If a critical issue is discovered after release:

### 1. Delete Git Tag (if not widely used)

```bash
# Delete local tag
git tag -d v0.2.0

# Delete remote tag
git push origin :refs/tags/v0.2.0
```

### 2. Delete GitHub Release

Go to the release page and click "Delete release"

### 3. Yank PyPI Release

```bash
# Mark as yanked (users can still install explicitly)
# This is done through PyPI web interface
```

### 4. Issue Patch Release

Fix the issue and release v0.2.1 immediately.

## Troubleshooting

### Docker Workflow Fails

1. Check GitHub Actions logs
2. Verify Dockerfile syntax
3. Ensure GITHUB_TOKEN has correct permissions
4. Test Docker build locally:
   ```bash
   docker build -f docker/Dockerfile -t test:latest .
   ```

### PyPI Upload Fails

1. Verify package name is not taken
2. Check ~/.pypirc credentials
3. Ensure version number is unique
4. Verify MANIFEST.in includes all needed files

### Version Tags Not Appearing

1. Ensure tag follows `vX.Y.Z` format
2. Check workflow triggers in `.github/workflows/docker-publish.yml`
3. Verify workflow completed successfully

## See Also

- [Semantic Versioning](https://semver.org/)
- [GitHub Releases Documentation](https://docs.github.com/en/repositories/releasing-projects-on-github)
- [Docker Metadata Action](https://github.com/docker/metadata-action)
- [Python Packaging Guide](https://packaging.python.org/)
