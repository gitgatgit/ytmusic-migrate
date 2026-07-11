# YTMusic Migrate

**YouTube Music Account Migration CLI Tool** - Migrate your playlists, liked songs, watch later, and subscriptions between YouTube accounts.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![YouTube Data API](https://img.shields.io/badge/YouTube-Data_API_v3-FF0000.svg)](https://developers.google.com/youtube/v3)

---

## Features

- **Playlist Migration** - Transfer all your custom playlists with videos
- **Liked Songs** - Migrate your Liked Music and Liked Videos
- **Watch Later** - Sync your Watch Later queue
- **Subscriptions** - Copy all your channel subscriptions
- **Resume Support** - Continues from where it left off if interrupted
- **Duplicate Detection** - Skips already migrated items automatically
- **Scan Mode** - Preview what needs migrating without making changes

---

## Quick Start

### Prerequisites

- Python 3.8+
- pip (Python package manager)
- Google Cloud account with YouTube Data API v3 enabled

### Install Dependencies

```bash
pip install google-auth-oauthlib google-api-python-client
```

### Set Up Google Cloud Project

1. Go to: [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable **YouTube Data API v3**
4. Create OAuth credentials (Desktop app type)
5. Note your Client ID and Client Secret

### Save Credentials

Create `credentials.json`:

```json
{
  "installed": {
    "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
    "client_secret": "YOUR_CLIENT_SECRET",
    "redirect_uris": ["http://localhost:8080"],
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token"
  }
}
```

**Never commit this file to version control.**

---

## Usage

```bash
# Migrate everything
python ytmusic_migrate.py --all

# Migrate specific items
python ytmusic_migrate.py --playlists
python ytmusic_migrate.py --liked-songs
python ytmusic_migrate.py --watch-later
python ytmusic_migrate.py --subscriptions

# Preview without migrating
python ytmusic_migrate.py --all --scan-only

# Reset migration state
python ytmusic_migrate.py --all --reset-state
```

---

## Authentication

On first run, you'll need to:
1. Sign in with your Google account in the browser
2. Accept YouTube's Terms of Service at https://www.youtube.com/t/terms
3. Grant the requested permissions

The target account needs a YouTube channel created.

---

## Cache and Credentials Management

### File Locations

| File | Location | Purpose | Deleting it... |
|------|----------|---------|----------------|
| **tokens.json** | `~/.ytmusic_migrate/tokens.json` | OAuth credentials for Google API access | Forces re-authentication, **keeps migration progress** |
| **migration_state.json** | `~/.ytmusic_migrate/migration_state.json` | Tracks which videos/playlists have been migrated | **Resets migration progress** - will start from scratch |

### Delete Cache and Credentials

**Delete everything (tokens + state):**
```bash
rm -rf ~/.ytmusic_migrate
```

**Delete tokens only (keep progress):**
```bash
rm ~/.ytmusic_migrate/tokens.json
# Your migration_state.json remains intact - will resume after re-auth
```

**Delete state only (keep tokens):**
```bash
rm ~/.ytmusic_migrate/migration_state.json
# Will start fresh but keep OAuth credentials
```

**Delete specific account token:**
```bash
python3 -c "
import json
from pathlib import Path
cache = Path.home() / '.ytmusic_migrate/tokens.json'
if cache.exists():
    with open(cache) as f:
        data = json.load(f)
    data.pop('source_default', None)
    data.pop('target_default', None)
    with open(cache, 'w') as f:
        json.dump(data, f)
    print('Token removed')
"
```

---

## Quota Information

- Read operations: 1-3 units
- Write operations: 50 units
- Daily default: 10,000 units
- Recommended: 100,000 units for large migrations

---

## Troubleshooting

- **youtubeSignupRequired:** Accept Terms at https://www.youtube.com/t/terms
- **Channel not found:** Create YouTube channel on target account
- **quotaExceeded:** Wait for reset or request increase
- **videoNotFound:** Skipped automatically
- **Port 8080 already in use (OAuth server conflict):**
  ```bash
  # Kill the hanging OAuth server without losing progress
  lsof -ti :8080 | xargs kill -9 2>/dev/null || true
  # OR
  pkill -f "python.*8080" 2>/dev/null || true
  # Verify migration state is intact
  ls -la ~/.ytmusic_migrate/migration_state.json
  # Run again - will resume from saved state
  python3 ytmusic_migrate.py --all
  ```
  Your progress is safe - the state file is separate from the OAuth server.

---

## Legal

- Privacy Policy: https://gitgatgit.github.io/ytmusic-migrate/privacy.html
- Terms of Service: https://gitgatgit.github.io/ytmusic-migrate/terms.html
- License: MIT

---

## Links

- Repository: https://github.com/gitgatgit/ytmusic-migrate
- Documentation: https://gitgatgit.github.io/ytmusic-migrate/
