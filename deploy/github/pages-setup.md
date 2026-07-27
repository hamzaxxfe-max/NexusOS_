# NexusOS GitHub Pages Deployment Guide

## Overview

This guide covers deploying the NexusOS website to GitHub Pages at `https://<username>.github.io/nexusos/`. The site serves as the public-facing landing page, documentation hub, and download portal for NexusOS ISO images hosted via GitHub Releases.

## Prerequisites

- GitHub account with Pages enabled
- Git installed locally
- NexusOS repository on GitHub
- Release pipeline configured (see `release-pipeline.yml`)

## 1. Repository Setup

### Create the Repository

```bash
# Create repository named nexusos (or nexusos-website for a separate repo)
gh repo create username/nexusos --public --description "NexusOS - Immutable Linux Distribution"
```

### Directory Structure

```
nexusos/
├── website/                  # Static site source
│   ├── index.html            # Landing page
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   └── main.js
│   ├── docs/                 # Documentation
│   │   ├── index.html
│   │   ├── installation.html
│   │   ├── architecture.html
│   │   └── contributing.html
│   ├── downloads/
│   │   └── index.html        # Dynamic download page
│   └── assets/
│       ├── logo.svg
│       └── screenshots/
├── manifest.json             # Version manifest for OTA updates
├── deploy/
│   ├── github/
│   │   ├── pages-deploy.yml
│   │   ├── release-pipeline.yml
│   │   └── build-iso.sh
│   └── ota/
│       ├── ota-updater.py
│       ├── nexusos-ota.service
│       └── nexusos-ota.timer
└── .github/
    └── workflows/
        ├── pages-deploy.yml
        └── release-pipeline.yml
```

## 2. Static Site Files

### Landing Page (website/index.html)

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NexusOS - Immutable Linux Distribution</title>
    <link rel="stylesheet" href="css/style.css">
    <meta name="description" content="NexusOS is an immutable, snapshot-based Linux distribution with atomic updates and rollback support.">
    <meta property="og:title" content="NexusOS">
    <meta property="og:description" content="Immutable Linux Distribution with atomic updates">
    <meta property="og:type" content="website">
</head>
<body>
    <header>
        <nav>
            <a href="/" class="logo">NexusOS</a>
            <ul>
                <li><a href="/nexusos/docs/">Docs</a></li>
                <li><a href="/nexusos/downloads/">Downloads</a></li>
                <li><a href="https://github.com/username/nexusos">GitHub</a></li>
            </ul>
        </nav>
    </header>
    <main>
        <section class="hero">
            <h1>Immutable Linux. Zero Downtime.</h1>
            <p>Btrfs snapshot-based updates with atomic rollback. Update without fear.</p>
            <a href="/nexusos/downloads/" class="btn-primary">Download NexusOS 1.0.0</a>
        </section>
        <section class="features">
            <div class="feature">
                <h3>Immutable Root</h3>
                <p>Read-only root filesystem prevents accidental modifications and tampering.</p>
            </div>
            <div class="feature">
                <h3>Atomic Updates</h3>
                <p>Every update creates a new Btrfs snapshot. Boot into it or roll back instantly.</p>
            </div>
            <div class="feature">
                <h3>OTA Updates</h3>
                <p>Incremental diffs download only changed blocks. Updates happen in the background.</p>
            </div>
        </section>
    </main>
    <footer>
        <p>Licensed under GPLv3. Built with care.</p>
    </footer>
    <script src="js/main.js"></script>
</body>
</html>
```

### Downloads Page (website/downloads/index.html)

This page fetches the manifest.json to display current versions and download links.

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Downloads - NexusOS</title>
    <link rel="stylesheet" href="../css/style.css">
</head>
<body>
    <header>
        <nav>
            <a href="/nexusos/" class="logo">NexusOS</a>
            <ul>
                <li><a href="../docs/">Docs</a></li>
                <li><a href="../downloads/">Downloads</a></li>
                <li><a href="https://github.com/username/nexusos">GitHub</a></li>
            </ul>
        </nav>
    </header>
    <main>
        <h1>Download NexusOS</h1>
        <div id="download-cards"></div>
        <div id="version-info"></div>
    </main>
    <script src="../js/main.js"></script>
</body>
</html>
```

### JavaScript for Dynamic Downloads (website/js/main.js)

```javascript
const MANIFEST_URL = 'https://raw.githubusercontent.com/username/nexusos/main/manifest.json';

async function loadDownloads() {
    const container = document.getElementById('download-cards');
    if (!container) return;

    try {
        const response = await fetch(MANIFEST_URL);
        const manifest = await response.json();

        const card = document.createElement('div');
        card.className = 'download-card';
        card.innerHTML = `
            <h2>NexusOS ${manifest.latest_version}</h2>
            <p class="release-date">${new Date(manifest.release_date).toLocaleDateString()}</p>
            <p class="release-notes">${manifest.release_notes}</p>
            <a href="${manifest.download_url}" class="btn-primary" download>
                Download ISO (${manifest.size || 'Unknown'} MB)
            </a>
            <p class="checksum">SHA256: <code>${manifest.sha256}</code></p>
            <details>
                <summary>Release Notes</summary>
                <pre>${manifest.full_release_notes || manifest.release_notes}</pre>
            </details>
        `;
        container.appendChild(card);

        const versionInfo = document.getElementById('version-info');
        if (versionInfo) {
            versionInfo.innerHTML = `
                <p>Latest: <strong>${manifest.latest_version}</strong></p>
                <p>Minimum required: <strong>${manifest.min_required_version}</strong></p>
            `;
        }
    } catch (err) {
        container.innerHTML = '<p>Failed to load release information. <a href="https://github.com/username/nexusos/releases">View releases on GitHub.</a></p>';
    }
}

document.addEventListener('DOMContentLoaded', loadDownloads);
```

## 3. Automated ISO Download Links

The download page reads `manifest.json` from the main branch. The release pipeline (`release-pipeline.yml`) updates this file automatically when a new tag is pushed. The flow:

```
Tag push (v1.0.0)
  → release-pipeline.yml builds ISO
  → Uploads ISO to GitHub Release
  → Generates SHA256 checksum
  → Updates manifest.json on main branch
  → pages-deploy.yml triggers on main push
  → Website rebuilds with new download links
```

### manifest.json Format

```json
{
    "latest_version": "1.0.0",
    "download_url": "https://github.com/username/nexusos/releases/download/v1.0.0/nexusos-1.0.0.iso",
    "sha256": "a1b2c3d4e5f6...",
    "release_date": "2026-07-27T00:00:00Z",
    "release_notes": "Initial stable release",
    "min_required_version": "0.9.0",
    "size": 2400,
    "incremental_patches": [
        {
            "from_version": "0.9.0",
            "to_version": "1.0.0",
            "patch_url": "https://github.com/username/nexusos/releases/download/v1.0.0/patch-0.9.0-to-1.0.0.xdelta",
            "sha256": "f7e8d9c0b1a2...",
            "size": 340
        }
    ]
}
```

## 4. GitHub Actions Workflow: Pages Deployment

The workflow file lives at `.github/workflows/pages-deploy.yml`. Full source is in `deploy/github/pages-deploy.yml`.

### Key Configuration

```yaml
# Triggered on push to main
# Deploys website/ directory to GitHub Pages
# Uses actions/upload-pages-artifact + actions/deploy-pages
```

### Setup Steps

1. Copy `deploy/github/pages-deploy.yml` to `.github/workflows/pages-deploy.yml`
2. Go to Repository Settings → Pages
3. Set Source to "GitHub Actions"
4. Push to main to trigger first deployment

### Permissions Required

The workflow requests these permissions:

```yaml
permissions:
    contents: read
    pages: write
    id-token: write
```

- `contents: read` — read repository files
- `pages: write` — deploy to GitHub Pages
- `id-token: write` — OIDC token for Pages deployment

### Environment Configuration

```yaml
environment:
    name: github-pages
    url: ${{ steps.deployment.outputs.page_url }}
```

The deployment step outputs the live URL.

## 5. GitHub Actions Workflow: Release Pipeline

The workflow file lives at `.github/workflows/release-pipeline.yml`. Full source is in `deploy/github/release-pipeline.yml`.

### Trigger Configuration

```yaml
on:
    push:
        tags:
            - 'v*'
```

Only runs when a tag matching `v*` is pushed (e.g., `v1.0.0`, `v1.2.3-beta`).

### Release Process

```bash
# 1. Create and push a tag
git tag -a v1.0.0 -m "Release 1.0.0"
git push origin v1.0.0

# The workflow will:
# - Build the ISO using build-iso.sh
# - Generate SHA256 checksum
# - Create GitHub Release with auto-generated notes
# - Upload ISO and checksum as release assets
# - Update manifest.json on main branch
```

### What the Pipeline Does

1. Checks out the repository at the tagged commit
2. Runs `build-iso.sh` to produce the ISO image
3. Generates `SHA256SUMS` file containing checksums
4. Creates a GitHub Release using `softprops/action-gh-release`
5. Uploads `nexusos-{version}.iso` and `SHA256SUMS` as release assets
6. Updates `manifest.json` with the new version, download URL, and checksum
7. Commits and pushes the updated manifest to main

## 6. Build Script Reference

The ISO build script is at `deploy/github/build-iso.sh`. It uses `mkarchiso` to produce a bootable ISO.

### Prerequisites for Building

- Arch Linux or Arch-based system
- `archiso` package installed (`sudo pacman -S archiso`)
- Root access (required by mkarchiso)

### Local Build

```bash
sudo bash deploy/github/build-iso.sh
```

The script reads the version from the repository tag or defaults to `0.0.0-dev`. Output goes to `build/nexusos-{version}.iso`.

## 7. Custom Domain Setup (Optional)

### Step 1: Configure DNS

Add these DNS records with your domain registrar:

| Type  | Name  | Value                        | TTL   |
|-------|-------|------------------------------|-------|
| A     | @     | 185.199.108.153              | 3600  |
| A     | @     | 185.199.109.153              | 3600  |
| A     | @     | 185.199.110.153              | 3600  |
| A     | @     | 185.199.111.153              | 3600  |
| CNAME | www   | username.github.io           | 3600  |

For a subdomain like `nexusos.example.com`:

| Type  | Name     | Value                        | TTL   |
|-------|----------|------------------------------|-------|
| CNAME | nexusos  | username.github.io           | 3600  |

### Step 2: Add CNAME File

Create `website/CNAME` with your domain:

```
nexusos.example.com
```

### Step 3: Configure in GitHub

1. Go to Repository Settings → Pages
2. Under "Custom domain", enter your domain
3. Check "Enforce HTTPS"
4. Save

### Step 4: Update manifest.json URLs

Update the `download_url` in `manifest.json` to use your custom domain:

```json
{
    "download_url": "https://nexusos.example.com/releases/nexusos-1.0.0.iso"
}
```

Note: This requires hosting the ISO files yourself or keeping GitHub Releases as the primary source.

## 8. Verification Checklist

After deployment, verify:

- [ ] Site loads at `https://username.github.io/nexusos/`
- [ ] Landing page renders correctly
- [ ] Documentation pages are accessible
- [ ] Downloads page loads manifest.json
- [ ] Download links point to correct GitHub Release
- [ ] SHA256 checksum is displayed
- [ ] HTTPS is enforced
- [ ] Custom domain resolves (if configured)

## 9. Troubleshooting

### Pages Not Deploying

Check the Actions tab for workflow run logs. Common issues:
- Missing `pages: write` permission
- No `index.html` in the deploy directory
- Workflow file not on the default branch

### Download Links Broken

- Verify `manifest.json` exists on the main branch
- Check that the release tag was pushed correctly
- Confirm the ISO was uploaded as a release asset

### Custom Domain Not Working

- DNS propagation can take up to 24 hours
- Verify DNS records using `dig nexusos.example.com`
- Ensure the CNAME file contains only the domain name
- Re-save the custom domain in GitHub settings

## 10. Updating the Site

```bash
# Edit website files
vim website/index.html

# Commit and push
git add website/
git commit -m "Update landing page"
git push origin main

# Pages deploys automatically via pages-deploy.yml
```

For CSS or JS changes, the build step in the workflow minifies assets automatically. No manual build step is required for static HTML.

## 11. Adding New Documentation Pages

1. Create an HTML file in `website/docs/`
2. Link to it from the navigation in other pages
3. Push to main — it deploys automatically

Example:

```bash
vim website/docs/security.html
git add website/docs/security.html
git commit -m "Add security documentation"
git push origin main
```
