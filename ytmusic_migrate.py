#!/usr/bin/env python3
"""
YTMusic Migrate - YouTube Music Account Migration CLI Tool

Migrate playlists, liked songs, watch later, subscriptions between YouTube Music accounts.

Usage:
    python ytmusic_migrate.py --source SOURCE_CHANNEL --target TARGET_CHANNEL --all
    python ytmusic_migrate.py --watch-later --liked-songs
    python ytmusic_migrate.py -h

Authentication:
    First run will open browser for Google OAuth2 authentication for each account.
    Tokens are cached locally in ~/.ytmusic_migrate/tokens.json
    
    Provide credentials via:
    1. JSON file: credentials.json (or custom path with --credentials)
    2. Environment variables: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    3. CLI arguments: --client-id, --client-secret
    
    You need to register a Google Cloud project and enable YouTube Data API v3
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Dict, List, Any

# Try to import required packages
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError as e:
    print(f"Error: Required Google packages not found: {e}")
    print("Install them with: pip install google-auth-oauthlib google-api-python-client")
    sys.exit(1)

# Configuration
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/youtube",
]

TOKEN_CACHE_DIR = Path.home() / ".ytmusic_migrate"
TOKEN_CACHE_FILE = TOKEN_CACHE_DIR / "tokens.json"
STATE_FILE = TOKEN_CACHE_DIR / "migration_state.json"
CREDENTIALS_FILE = Path("credentials.json")

# Special YouTube playlist IDs
SPECIAL_PLAYLISTS = {
    "liked_videos": "LL",      # Liked Videos
    "liked_music": "LM",       # Liked Music (YouTube Music)
    "watch_later": "WL",       # Watch Later
    "history": "HL",           # History
}


class YTMusicMigrationTool:
    """Main class for YouTube Music account migration."""

    def __init__(self, args):
        self.args = args
        self.source_yt = None
        self.target_yt = None
        self.token_cache: Dict[str, Dict[str, Any]] = {}
        
        # Load token cache
        self._load_token_cache()
        
        # Load migration state
        self._load_migration_state()
        
        # Reset state if requested
        if args.reset_state:
            self.migration_state = {
                'playlists': {},
                'liked_songs': {'playlist_id': None, 'video_ids': []}
            }
            self._save_migration_state()
            print("Migration state reset")
        
        # Validate and setup clients
        self._setup_clients()

    def _load_token_cache(self):
        """Load cached tokens from file."""
        if TOKEN_CACHE_FILE.exists():
            try:
                with open(TOKEN_CACHE_FILE, 'r') as f:
                    self.token_cache = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.token_cache = {}
        else:
            self.token_cache = {}
            TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _save_token_cache(self):
        """Save token cache to file."""
        try:
            with open(TOKEN_CACHE_FILE, 'w') as f:
                json.dump(self.token_cache, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save token cache: {e}")

    def _load_migration_state(self):
        """Load migration state from file."""
        self.migration_state = {
            'playlists': {},  # playlist_id -> list of video_ids
            'liked_songs': {
                'playlist_id': None,
                'video_ids': []
            }
        }
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    self.migration_state = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Could not load migration state: {e}")
                self.migration_state = {
                    'playlists': {},
                    'liked_songs': {'playlist_id': None, 'video_ids': []}
                }

    def _save_migration_state(self):
        """Save migration state to file."""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.migration_state, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save migration state: {e}")

    def _load_credentials_from_file(self, file_path: Path = None) -> Dict[str, str]:
        """Load credentials from a JSON file."""
        # Check if custom credentials path was provided via CLI
        if file_path is None and hasattr(self.args, 'credentials') and self.args.credentials:
            file_path = Path(self.args.credentials)
        
        path = file_path or CREDENTIALS_FILE
        
        if not path.exists():
            return {}
        
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load credentials from {path}: {e}")
            return {}

    def _get_auth_config(self):
        """Get authentication configuration from JSON file, args, or environment."""
        # Priority order: CLI args > Environment vars > JSON file
        
        client_id = getattr(self.args, 'client_id', None)
        client_secret = getattr(self.args, 'client_secret', None)
        
        # Try environment variables
        if not client_id:
            client_id = os.getenv('GOOGLE_CLIENT_ID')
        if not client_secret:
            client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        
        # Try credentials JSON file
        if not client_id or not client_secret:
            creds = self._load_credentials_from_file()
            if not client_id:
                client_id = creds.get('client_id') or creds.get('GOOGLE_CLIENT_ID') or creds.get('installed', {}).get('client_id')
            if not client_secret:
                client_secret = creds.get('client_secret') or creds.get('GOOGLE_CLIENT_SECRET') or creds.get('installed', {}).get('client_secret')
        
        if not client_id or not client_secret:
            print("Error: Google OAuth client ID and client secret are required.")
            print("Provide credentials via:")
            print("  1. CLI arguments: --client-id and --client-secret")
            print("  2. Environment variables: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET")
            print("  3. JSON file: credentials.json in current directory")
            print("\nRegister your app at: https://console.cloud.google.com/")
            print("Enable YouTube Data API v3 for your project.")
            print("\nExample credentials.json:")
            print('  {')
            print('    "installed": {')
            print('      "client_id": "your_client_id.apps.googleusercontent.com",')
            print('      "client_secret": "your_client_secret",')
            print('      "redirect_uris": ["http://localhost:8080"]')
            print('    }')
            print('  }')
            sys.exit(1)
            
        return client_id, client_secret

    def _get_credentials(self, account_type: str, user_label: Optional[str] = None) -> Credentials:
        """Get Google credentials for a specific account, using cache if available."""
        client_id, client_secret = self._get_auth_config()
        cache_key = f"{account_type}_{user_label or 'default'}"
        
        # Check if we have cached tokens
        cached_token = self.token_cache.get(cache_key)
        
        if cached_token:
            try:
                creds = Credentials.from_authorized_user_info(cached_token)
                if creds and creds.valid:
                    return creds
                elif creds and creds.expired and creds.refresh_token:
                    # Refresh the token
                    creds.refresh(Request())
                    self.token_cache[cache_key] = {
                        'token': creds.token,
                        'refresh_token': creds.refresh_token,
                        'token_uri': creds.token_uri,
                        'client_id': creds.client_id,
                        'client_secret': creds.client_secret,
                        'scopes': creds.scopes
                    }
                    self._save_token_cache()
                    return creds
            except Exception as e:
                print(f"Warning: Could not use cached token for {account_type}: {e}")
        
        # Need to authenticate
        print(f"Authenticating {account_type} account...")
        
        # Try multiple ports if 8080 is in use
        ports_to_try = [8080, 8081, 8082, 8083, 8084]
        creds = None
        last_error = None
        
        for port in ports_to_try:
            try:
                # Update redirect URI to match the port
                redirect_uri = f"http://localhost:{port}"
                
                # Create OAuth flow
                flow = InstalledAppFlow.from_client_config(
                    {
                        "installed": {
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "redirect_uris": [redirect_uri],
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token"
                        }
                    },
                    scopes=DEFAULT_SCOPES
                )
                
                # Run the flow
                creds = flow.run_local_server(port=port)
                print(f"  Using port {port} for OAuth")
                break
            except OSError as e:
                last_error = e
                print(f"  Port {port} in use, trying next...")
                continue
        
        if creds is None:
            if last_error:
                raise last_error
            raise RuntimeError("No available ports for OAuth server")
        
        # Save the token
        self.token_cache[cache_key] = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }
        self._save_token_cache()
        
        return creds

    def _create_yt_service(self, account_type: str, user_label: Optional[str] = None):
        """Create a YouTube Data API service for a specific account."""
        creds = self._get_credentials(account_type, user_label)
        
        try:
            # Get channel info to verify
            service = build('youtube', 'v3', credentials=creds)
            
            # Get the authenticated user's channel
            request = service.channels().list(
                part="snippet",
                mine=True,
                maxResults=1
            )
            response = request.execute()
            
            if response.get('items'):
                channel = response['items'][0]
                channel_id = channel['id']
                channel_title = channel['snippet']['title']
                print(f"✓ Connected to {account_type} account: {channel_title} ({channel_id})")
            else:
                print(f"✓ Connected to {account_type} account")
            
            return service
        except HttpError as e:
            print(f"✗ Failed to connect to {account_type} account: {e}")
            raise

    def _setup_clients(self):
        """Setup source and target YouTube clients."""
        print("\n=== Setting up YouTube connections ===\n")
        
        # Source client
        if self.args.source:
            print(f"Connecting to source account: {self.args.source}")
            self.source_yt = self._create_yt_service("source", self.args.source)
        else:
            print("Connecting to source account (current user)")
            self.source_yt = self._create_yt_service("source")
        
        # Target client
        if self.args.target:
            print(f"Connecting to target account: {self.args.target}")
            self.target_yt = self._create_yt_service("target", self.args.target)
        else:
            print("Connecting to target account (current user)")
            self.target_yt = self._create_yt_service("target")

    def _get_user_playlists(self, service, include_special: bool = False) -> List[Dict]:
        """Get all user playlists from a YouTube account."""
        playlists = []
        next_page_token = None
        
        try:
            while True:
                request = service.playlists().list(
                    part="snippet,contentDetails,status",
                    mine=True,
                    maxResults=50,
                    pageToken=next_page_token
                )
                response = request.execute()
                playlists.extend(response.get('items', []))
                
                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break
        except HttpError as e:
            if e.resp.status == 404:
                # Account has no YouTube channel - return empty list
                return []
            raise
        
        # Filter out special playlists if not including them
        if not include_special:
            special_ids = set(SPECIAL_PLAYLISTS.values())
            playlists = [p for p in playlists if p['id'] not in special_ids]
        
        return playlists

    def _get_playlist_items(self, service, playlist_id: str) -> List[Dict]:
        """Get all items from a playlist."""
        items = []
        next_page_token = None
        
        while True:
            request = service.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            items.extend(response.get('items', []))
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        
        return items

    def _get_existing_video_ids(self, service, playlist_id: str) -> set:
        """Get set of video IDs already in a playlist."""
        existing = set()
        try:
            items = self._get_playlist_items(service, playlist_id)
            for item in items:
                if 'videoId' in item['snippet'].get('resourceId', {}):
                    existing.add(item['snippet']['resourceId']['videoId'])
        except HttpError as e:
            if 'playlistNotFound' in str(e) or 'forbidden' in str(e):
                # Playlist doesn't exist yet or no permission - return empty
                pass
            else:
                print(f"  Warning: Could not check existing videos in playlist {playlist_id}: {e}")
        return existing

    def _create_playlist(self, service, title: str, description: str = "", privacy: str = "private") -> str:
        """Create a new playlist and return its ID."""
        request_body = {
            'snippet': {
                'title': title,
                'description': description
            },
            'status': {
                'privacyStatus': privacy
            }
        }
        
        request = service.playlists().insert(
            part="snippet,status",
            body=request_body
        )
        response = request.execute()
        return response['id']

    def _add_items_to_playlist(self, service, playlist_id: str, video_ids: List[str], 
                               track_id: str = None, check_existing: bool = True):
        """Add multiple video IDs to a playlist.
        
        Args:
            service: YouTube service
            playlist_id: Target playlist ID
            video_ids: List of video IDs to add
            track_id: Optional ID to track progress in state (playlist ID or 'liked_songs')
            check_existing: Whether to check for existing videos
        
        Returns (success_count, failed_count, skipped_count) tuple.
        Skips videos that fail (deleted, private, quota issues) or already exist.
        """
        success_count = 0
        failed_count = 0
        skipped_count = 0
        
        # Get already-added videos from state if tracking
        already_added = set()
        if track_id and track_id in self.migration_state.get('playlists', {}):
            already_added = set(self.migration_state['playlists'][track_id])
        elif track_id == 'liked_songs' and self.migration_state.get('liked_songs', {}).get('playlist_id'):
            already_added = set(self.migration_state['liked_songs']['video_ids'])
        
        # Also check YouTube if enabled (fallback for stale state)
        existing_on_youtube = set()
        if check_existing:
            existing_on_youtube = self._get_existing_video_ids(service, playlist_id)
        
        total_videos = len(video_ids)
        for i, vid in enumerate(video_ids):
            # Skip if already in state or on YouTube
            if vid in already_added or vid in existing_on_youtube:
                skipped_count += 1
                continue
                
            try:
                item = {
                    'snippet': {
                        'playlistId': playlist_id,
                        'resourceId': {
                            'kind': 'youtube#video',
                            'videoId': vid
                        }
                    }
                }
                service.playlistItems().insert(
                    part="snippet",
                    body=item
                ).execute()
                success_count += 1
                
                # Print progress every 50 videos for large batches
                if total_videos > 50 and (success_count + skipped_count) % 50 == 0:
                    print(f"  Progress: {success_count + skipped_count}/{total_videos} videos", end="\r")
                
                # Update state if tracking
                if track_id:
                    if track_id == 'liked_songs':
                        if 'liked_songs' not in self.migration_state:
                            self.migration_state['liked_songs'] = {'playlist_id': None, 'video_ids': []}
                        if vid not in self.migration_state['liked_songs']['video_ids']:
                            self.migration_state['liked_songs']['video_ids'].append(vid)
                    else:
                        if 'playlists' not in self.migration_state:
                            self.migration_state['playlists'] = {}
                        if track_id not in self.migration_state['playlists']:
                            self.migration_state['playlists'][track_id] = []
                        if vid not in self.migration_state['playlists'][track_id]:
                            self.migration_state['playlists'][track_id].append(vid)
                
            except HttpError as e:
                error_reason = e.resp.reason if hasattr(e, 'resp') else str(e)
                if 'quotaExceeded' in str(e) or 'SERVICE_UNAVAILABLE' in str(e):
                    # Save state before stopping
                    if track_id:
                        self._save_migration_state()
                    print(f"  ✗ Stopping: {e}")
                    raise
                # For individual video errors (deleted, private, etc.), just skip
                failed_count += 1
        
        # Save state after batch
        if track_id:
            self._save_migration_state()
        
        # Print newline if we printed progress
        if total_videos > 50:
            print()  # Newline after progress
        
        return success_count, failed_count, skipped_count

    def _get_existing_playlists_map(self, service) -> Dict[str, str]:
        """Get a mapping of playlist name -> playlist ID for target account.
        
        Returns dict of {playlist_name: playlist_id} for non-special playlists.
        Warns about duplicate playlist names.
        """
        playlist_map = {}
        name_counts = {}
        try:
            playlists = self._get_user_playlists(service, include_special=False)
            for p in playlists:
                name = p['snippet']['title']
                playlist_id = p['id']
                
                # Count occurrences of each name
                name_counts[name] = name_counts.get(name, 0) + 1
                
                # Use the first occurrence if there are duplicates
                if name not in playlist_map:
                    playlist_map[name] = p['id']
            
            # Warn about duplicates
            for name, count in name_counts.items():
                if count > 1:
                    # Find all playlist IDs with this name
                    duplicate_ids = [p['id'] for p in playlists if p['snippet']['title'] == name]
                    used_id = playlist_map[name]
                    others = [pid for pid in duplicate_ids if pid != used_id]
                    print(f"  ⚠️  Duplicate playlist: '{name}' exists {count} times on target")
                    print(f"      Using: {used_id}")
                    if others:
                        print(f"      Orphaned duplicates: {', '.join(others)}")
        except HttpError as e:
            print(f"  Warning: Could not fetch existing playlists: {e}")
        return playlist_map

    def migrate_playlists(self):
        """Migrate all user-created playlists from source to target."""
        print("\n=== Migrating Playlists ===\n")
        
        # Get all playlists from source (excluding special ones)
        source_playlists = self._get_user_playlists(self.source_yt, include_special=False)
        
        # Build a map of existing playlist names on target
        target_playlist_map = {}
        if self.args.check_existing:
            print("Checking existing playlists on target account...")
            target_playlist_map = self._get_existing_playlists_map(self.target_yt)
            print(f"  Found {len(target_playlist_map)} existing playlists on target")
        
        # Determine which playlists to create vs reuse
        total_count = len(source_playlists)
        existing_count = sum(1 for p in source_playlists if p['snippet']['title'] in target_playlist_map)
        to_migrate_count = total_count - existing_count
        
        print(f"Found {total_count} total playlists on source")
        print(f"Already on target: {existing_count}")
        print(f"To create: {to_migrate_count} playlists\n")
        
        migrated_count = 0
        reused_count = 0
        
        for playlist in source_playlists:
            playlist_id = playlist['id']
            playlist_name = playlist['snippet']['title']
            
            # Check if playlist with same name already exists on target
            if self.args.check_existing and playlist_name in target_playlist_map:
                new_playlist_id = target_playlist_map[playlist_name]
                print(f"Reusing existing playlist: {playlist_name} ({new_playlist_id})")
                reused_count += 1
            else:
                # Create new playlist
                print(f"Migrating playlist: {playlist_name}")
                playlist_description = playlist['snippet'].get('description', '')
                playlist_privacy = playlist['status'].get('privacyStatus', 'private')
                
                try:
                    # Create the playlist on target account
                    new_playlist_id = self._create_playlist(
                        self.target_yt,
                        title=playlist_name,
                        description=playlist_description,
                        privacy=playlist_privacy
                    )
                    print(f"  Created playlist: {new_playlist_id}")
                    migrated_count += 1
                except HttpError as e:
                    print(f"  ✗ Error creating playlist: {e}")
                    continue
            
            # Get all items from source playlist
            try:
                items = self._get_playlist_items(self.source_yt, playlist_id)
                video_ids = [item['snippet']['resourceId']['videoId'] 
                           for item in items 
                           if 'videoId' in item['snippet']['resourceId']]
                
                source_count = len(video_ids)
                if video_ids:
                    print(f"  Source has {source_count} videos, adding...")
                    success, failed, skipped = self._add_items_to_playlist(
                        self.target_yt, new_playlist_id, video_ids,
                        track_id=new_playlist_id,
                        check_existing=self.args.check_existing
                    )
                    total_in_target = success + skipped
                    status = "✓ COMPLETE" if total_in_target >= source_count else "⚠ INCOMPLETE"
                    print(f"  {status}: {total_in_target}/{source_count} videos ({success} added, {failed} failed, {skipped} already there)")
                else:
                    print(f"  Playlist has 0 videos")
            except HttpError as e:
                print(f"  ✗ Error reading source playlist items: {e}")
        
        print(f"\n✓ Successfully migrated {migrated_count} new playlists")
        print(f"  (Reused {reused_count} existing playlists)")

    def migrate_watch_later(self):
        """Migrate Watch Later list from source to target."""
        print("\n=== Migrating Watch Later ===\n")
        
        # Get Watch Later items from source
        items = self._get_playlist_items(self.source_yt, SPECIAL_PLAYLISTS['watch_later'])
        video_ids = [item['snippet']['resourceId']['videoId'] for item in items if 'videoId' in item['snippet']['resourceId']]
        
        print(f"Found {len(video_ids)} videos in Watch Later\n")
        
        if not video_ids:
            print("No videos in Watch Later to migrate")
            return
        
        # Add videos to target's Watch Later
        # Note: Target's WL playlist already exists, we just add to it
        try:
            print(f"Adding {len(video_ids)} videos to target's Watch Later...")
            success, failed, skipped = self._add_items_to_playlist(
                self.target_yt, SPECIAL_PLAYLISTS['watch_later'], video_ids,
                track_id='watch_later',
                check_existing=self.args.check_existing
            )
            print(f"✓ Added {success} videos to Watch Later ({failed} failed, {skipped} already there)")
        except HttpError as e:
            print(f"✗ Error migrating Watch Later: {e}")

    def migrate_liked_songs(self):
        """Migrate Liked Songs from source to target.
        
        Note: YouTube API doesn't allow modifying the built-in 'Liked Music' playlist,
        so this creates a regular playlist called 'Liked Music (Migrated)'.
        """
        print("\n=== Migrating Liked Songs ===\n")
        print("Note: Creating regular playlist (YouTube API cannot modify built-in Liked Music)")
        
        # Try LM (Liked Music) first, fall back to LL (Liked Videos)
        all_video_ids = []
        for playlist_name, playlist_id in [('Liked Music', 'LM'), ('Liked Videos', 'LL')]:
            try:
                items = self._get_playlist_items(self.source_yt, playlist_id)
                video_ids = [item['snippet']['resourceId']['videoId'] 
                           for item in items 
                           if 'videoId' in item['snippet']['resourceId']]
                all_video_ids.extend(video_ids)
                print(f"Found {len(video_ids)} videos from {playlist_name}")
            except HttpError as e:
                print(f"  Warning: Could not read {playlist_name} ({playlist_id}): {e}")
                continue
        
        if not all_video_ids:
            print("No Liked Songs/Music found to migrate on source account")
            return
        
        # Deduplicate
        all_video_ids = list(set(all_video_ids))
        print(f"Total unique videos to migrate: {len(all_video_ids)}\n")
        
        # Check if Liked Music (Migrated) playlist already exists
        liked_playlist_id = None
        liked_playlist_name = "Liked Music (Migrated)"
        
        if self.args.check_existing:
            target_playlist_map = self._get_existing_playlists_map(self.target_yt)
            if liked_playlist_name in target_playlist_map:
                liked_playlist_id = target_playlist_map[liked_playlist_name]
                print(f"Reusing existing playlist: {liked_playlist_name} ({liked_playlist_id})")
            else:
                # Create a new playlist for liked songs
                try:
                    liked_playlist_id = self._create_playlist(
                        self.target_yt,
                        title=liked_playlist_name,
                        description="Liked songs migrated from another account",
                        privacy="private"
                    )
                    print(f"Created playlist: {liked_playlist_id}")
                except HttpError as e:
                    print(f"✗ Error creating playlist: {e}")
                    return
        else:
            # Always create new if check_existing is disabled
            try:
                liked_playlist_id = self._create_playlist(
                    self.target_yt,
                    title=liked_playlist_name,
                    description="Liked songs migrated from another account",
                    privacy="private"
                )
                print(f"Created playlist: {liked_playlist_id}")
            except HttpError as e:
                print(f"✗ Error creating playlist: {e}")
                return
        
        # Add videos to the playlist and track progress
        try:
            source_count = len(all_video_ids)
            print(f"Source has {source_count} videos, adding...")
            # Save the playlist ID in state for tracking
            if 'liked_songs' not in self.migration_state:
                self.migration_state['liked_songs'] = {'playlist_id': None, 'video_ids': []}
            self.migration_state['liked_songs']['playlist_id'] = liked_playlist_id
            self._save_migration_state()
            
            success, failed, skipped = self._add_items_to_playlist(
                self.target_yt, liked_playlist_id, all_video_ids,
                track_id='liked_songs',
                check_existing=self.args.check_existing
            )
            total_in_target = success + skipped
            status = "✓ COMPLETE" if total_in_target >= source_count else "⚠ INCOMPLETE"
            print(f"  {status}: {total_in_target}/{source_count} videos ({success} added, {failed} failed, {skipped} already there)")
        except HttpError as e:
            print(f"✗ Error adding videos: {e}")

    def _get_existing_subscriptions(self, service) -> set:
        """Get set of channel IDs that the account is already subscribed to."""
        existing = set()
        next_page_token = None
        
        while True:
            try:
                request = service.subscriptions().list(
                    part="snippet",
                    mine=True,
                    maxResults=50,
                    pageToken=next_page_token
                )
                response = request.execute()
                for sub in response.get('items', []):
                    if 'channelId' in sub['snippet'].get('resourceId', {}):
                        existing.add(sub['snippet']['resourceId']['channelId'])
                
                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break
            except HttpError as e:
                print(f"  Warning: Could not fetch existing subscriptions: {e}")
                break
        
        return existing

    def _find_duplicate_playlists(self, service) -> Dict[str, List[str]]:
        """Find duplicate playlists on target account.
        
        Returns dict of {playlist_name: [playlist_id1, playlist_id2, ...]} for duplicates.
        """
        playlists = self._get_user_playlists(service, include_special=False)
        name_to_ids = {}
        
        for p in playlists:
            name = p['snippet']['title']
            if name not in name_to_ids:
                name_to_ids[name] = []
            name_to_ids[name].append(p['id'])
        
        # Only keep entries with duplicates
        return {name: ids for name, ids in name_to_ids.items() if len(ids) > 1}

    def _delete_playlist(self, service, playlist_id: str) -> bool:
        """Delete a playlist. Returns True if successful."""
        try:
            service.playlists().delete(id=playlist_id).execute()
            return True
        except HttpError as e:
            print(f"  ✗ Error deleting playlist {playlist_id}: {e}")
            return False

    def _get_duplicate_videos_in_playlist(self, service, playlist_id: str) -> Dict[str, List[str]]:
        """Find duplicate video IDs within a playlist.
        
        Returns dict of {video_id: [position1, position2, ...]} for duplicates.
        """
        try:
            items = self._get_playlist_items(service, playlist_id)
        except HttpError as e:
            print(f"  ✗ Could not read playlist {playlist_id}: {e}")
            return {}
        
        video_to_positions = {}
        for idx, item in enumerate(items):
            if 'videoId' in item['snippet'].get('resourceId', {}):
                vid = item['snippet']['resourceId']['videoId']
                if vid not in video_to_positions:
                    video_to_positions[vid] = []
                video_to_positions[vid].append(idx)
        
        # Only keep videos that appear more than once
        return {vid: positions for vid, positions in video_to_positions.items() if len(positions) > 1}

    def _remove_playlist_item(self, service, playlist_id: str, item_id: str) -> bool:
        """Remove a specific item from a playlist. Returns True if successful."""
        try:
            service.playlistItems().delete(id=item_id).execute()
            return True
        except HttpError as e:
            print(f"  ✗ Error removing item {item_id} from playlist {playlist_id}: {e}")
            return False

    def _verify_playlist_completion(self, source_service, target_service, 
                                    source_playlist_id: str, target_playlist_id: str) -> Dict:
        """Verify that target playlist matches source playlist exactly.
        
        Returns dict with:
        - source_count: number of videos in source
        - target_count: number of videos in target  
        - missing_in_target: set of video IDs in source but not in target
        - extra_in_target: set of video IDs in target but not in source
        - duplicates_in_target: dict of duplicate videos in target
        """
        # Get source videos
        try:
            source_items = self._get_playlist_items(source_service, source_playlist_id)
            source_videos = set()
            for item in source_items:
                if 'videoId' in item['snippet'].get('resourceId', {}):
                    source_videos.add(item['snippet']['resourceId']['videoId'])
        except HttpError as e:
            return {'error': str(e), 'source_playlist_id': source_playlist_id}
        
        # Get target videos
        try:
            target_items = self._get_playlist_items(target_service, target_playlist_id)
            target_videos = []  # Keep order for duplicate detection
            for item in target_items:
                if 'videoId' in item['snippet'].get('resourceId', {}):
                    target_videos.append(item['snippet']['resourceId']['videoId'])
            target_videos_set = set(target_videos)
        except HttpError as e:
            return {'error': str(e), 'target_playlist_id': target_playlist_id}
        
        # Calculate differences
        missing_in_target = source_videos - target_videos_set
        extra_in_target = target_videos_set - source_videos
        
        # Check for duplicates in target
        duplicates_in_target = {}
        for vid in target_videos:
            if vid in duplicates_in_target:
                duplicates_in_target[vid].append(len(duplicates_in_target[vid]) + 1)
            elif target_videos.count(vid) > 1:
                positions = [i for i, v in enumerate(target_videos) if v == vid]
                duplicates_in_target[vid] = positions[1:]  # Store extra positions
        
        return {
            'source_count': len(source_videos),
            'target_count': len(target_videos),
            'unique_target_count': len(target_videos_set),
            'missing_in_target': missing_in_target,
            'extra_in_target': extra_in_target,
            'duplicates_in_target': duplicates_in_target,
            'is_complete': len(missing_in_target) == 0 and len(extra_in_target) == 0 and len(duplicates_in_target) == 0
        }

    def migrate_subscriptions(self):
        """Migrate subscribed channels from source to target."""
        print("\n=== Migrating Subscriptions ===\n")
        
        # Get all subscriptions from source
        subscriptions = []
        next_page_token = None
        
        while True:
            request = self.source_yt.subscriptions().list(
                part="snippet",
                mine=True,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            subscriptions.extend(response.get('items', []))
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        
        source_channel_ids = [sub['snippet']['resourceId']['channelId'] 
                             for sub in subscriptions 
                             if 'channelId' in sub['snippet']['resourceId']]
        
        # Check existing subscriptions on target if enabled
        target_existing = set()
        if self.args.check_existing:
            print("Checking existing subscriptions on target account...")
            target_existing = self._get_existing_subscriptions(self.target_yt)
            print(f"  Found {len(target_existing)} existing subscriptions on target")
        
        # Filter to only channels not already on target
        channels_to_migrate = [cid for cid in source_channel_ids if cid not in target_existing]
        
        print(f"Found {len(source_channel_ids)} total subscribed channels on source")
        print(f"Already on target: {len(target_existing)}")
        print(f"To migrate: {len(channels_to_migrate)} channels\n")
        
        # Subscribe to channels on target account
        subscribed_count = 0
        skipped_count = 0
        
        for channel_id in source_channel_ids:
            if channel_id in target_existing:
                skipped_count += 1
                continue
                
            try:
                request = self.target_yt.subscriptions().insert(
                    part="snippet",
                    body={
                        'snippet': {
                            'resourceId': {
                                'kind': 'youtube#channel',
                                'channelId': channel_id
                            }
                        }
                    }
                )
                request.execute()
                subscribed_count += 1
                
                if subscribed_count % 10 == 0:
                    print(f"  Subscribed to {subscribed_count}/{len(channels_to_migrate)} new channels")
            except HttpError as e:
                print(f"  ✗ Error subscribing to channel {channel_id}: {e}")
        
        print(f"\n✓ Successfully migrated {subscribed_count} new subscriptions")
        print(f"  (Skipped {skipped_count} already present on target)")

    def scan_migration_status(self):
        """Scan and report migration status without making changes."""
        print("\n=== SCAN ONLY MODE - No changes will be made ===\n")
        
        total_quota_needed = 0
        
        # Scan playlists
        if self.args.all or self.args.playlists:
            print("=== Playlists ===")
            source_playlists = self._get_user_playlists(self.source_yt, include_special=False)
            target_playlist_map = self._get_existing_playlists_map(self.target_yt)
            
            for playlist in source_playlists:
                playlist_id = playlist['id']
                playlist_name = playlist['snippet']['title']
                
                # Get source video count
                try:
                    source_items = self._get_playlist_items(self.source_yt, playlist_id)
                    source_videos = [item['snippet']['resourceId']['videoId'] 
                                  for item in source_items 
                                  if 'videoId' in item['snippet']['resourceId']]
                    source_count = len(source_videos)
                except HttpError as e:
                    print(f"  ✗ Could not read source playlist '{playlist_name}': {e}")
                    continue
                
                # Check target
                if playlist_name in target_playlist_map:
                    target_id = target_playlist_map[playlist_name]
                    try:
                        target_items = self._get_playlist_items(self.target_yt, target_id)
                        target_videos = [item['snippet']['resourceId']['videoId'] 
                                      for item in target_items 
                                      if 'videoId' in item['snippet']['resourceId']]
                        target_count = len(target_videos)
                        
                        # Also check state
                        state_videos = set()
                        if playlist_id in self.migration_state.get('playlists', {}):
                            state_videos = set(self.migration_state['playlists'][playlist_id])
                        
                        missing = source_count - len(state_videos | set(target_videos))
                        status = "✓ COMPLETE" if missing <= 0 else f"⚠ NEEDS {missing}"
                        print(f"  {status}: {playlist_name} ({target_count}/{source_count} videos)")
                        
                        if missing > 0:
                            total_quota_needed += missing * 50  # insert cost
                    except HttpError as e:
                        print(f"  ⚠ Could not read target playlist '{playlist_name}': {e}")
                else:
                    print(f"  ✗ MISSING: {playlist_name} (0/{source_count} videos)")
                    total_quota_needed += source_count * 50 + 1  # create + inserts
                    total_quota_needed += 1  # for playlist creation
            print()
        
        # Scan Liked Songs
        if self.args.all or self.args.liked_songs:
            print("=== Liked Songs ===")
            all_video_ids = []
            for playlist_name, playlist_id in [('Liked Music', 'LM'), ('Liked Videos', 'LL')]:
                try:
                    items = self._get_playlist_items(self.source_yt, playlist_id)
                    video_ids = [item['snippet']['resourceId']['videoId'] 
                               for item in items 
                               if 'videoId' in item['snippet']['resourceId']]
                    all_video_ids.extend(video_ids)
                except HttpError:
                    continue
            
            all_video_ids = list(set(all_video_ids))
            source_count = len(all_video_ids)
            
            if source_count > 0:
                # Check state
                state_videos = set()
                if 'liked_songs' in self.migration_state:
                    state_videos = set(self.migration_state['liked_songs'].get('video_ids', []))
                
                missing = source_count - len(state_videos)
                status = "✓ COMPLETE" if missing <= 0 else f"⚠ NEEDS {missing}"
                print(f"  {status}: Liked Music (Migrated) ({len(state_videos)}/{source_count} videos)")
                
                if missing > 0:
                    # Need to create playlist (50 units) + inserts (50 each)
                    total_quota_needed += 50 + missing * 50
            else:
                print("  No liked songs found on source")
            print()
        
        # Scan subscriptions
        if self.args.all or self.args.subscriptions:
            print("=== Subscriptions ===")
            
            # Get source subscriptions
            source_subs = []
            next_page_token = None
            while True:
                try:
                    request = self.source_yt.subscriptions().list(
                        part="snippet", mine=True, maxResults=50, pageToken=next_page_token
                    )
                    response = request.execute()
                    source_subs.extend(response.get('items', []))
                    next_page_token = response.get('nextPageToken')
                    if not next_page_token:
                        break
                except HttpError as e:
                    print(f"  ✗ Could not read source subscriptions: {e}")
                    break
            
            source_channel_ids = [sub['snippet']['resourceId']['channelId'] 
                                for sub in source_subs 
                                if 'channelId' in sub['snippet'].get('resourceId', {})]
            source_count = len(source_channel_ids)
            
            # Get target subscriptions
            target_subs = []
            next_page_token = None
            while True:
                try:
                    request = self.target_yt.subscriptions().list(
                        part="snippet", mine=True, maxResults=50, pageToken=next_page_token
                    )
                    response = request.execute()
                    target_subs.extend(response.get('items', []))
                    next_page_token = response.get('nextPageToken')
                    if not next_page_token:
                        break
                except HttpError as e:
                    print(f"  ✗ Could not read target subscriptions: {e}")
                    break
            
            target_channel_ids = [sub['snippet']['resourceId']['channelId'] 
                                for sub in target_subs 
                                if 'channelId' in sub['snippet'].get('resourceId', {})]
            target_count = len(target_channel_ids)
            
            missing = len(set(source_channel_ids) - set(target_channel_ids))
            status = "✓ COMPLETE" if missing <= 0 else f"⚠ NEEDS {missing}"
            print(f"  {status}: {target_count}/{source_count} channels")
            
            if missing > 0:
                total_quota_needed += missing * 50
            print()
        
        if total_quota_needed > 0:
            print(f"Quota needed for remaining items: ~{total_quota_needed} units")
            print(f"(Current daily quota: typically 10,000 units)")
        else:
            print("✓ Everything is already migrated!")

    def prune_duplicate_playlists(self):
        """Remove orphaned duplicate playlists from target account."""
        print("\n=== Pruning Duplicate Playlists ===\n")
        
        if not self.args.check_existing:
            print("Error: --check-existing is required for --prune-duplicate-playlists")
            return
        
        # Find duplicates
        duplicates = self._find_duplicate_playlists(self.target_yt)
        
        if not duplicates:
            print("No duplicate playlists found on target account")
            return
        
        print(f"Found {len(duplicates)} duplicate playlist names on target:\n")
        
        for name, playlist_ids in duplicates.items():
            print(f"  '{name}': {len(playlist_ids)} playlists")
            # Keep the first one, delete the rest
            keep_id = playlist_ids[0]
            delete_ids = playlist_ids[1:]
            
            for playlist_id in delete_ids:
                if self.args.dry_run:
                    print(f"    Would delete: {playlist_id}")
                else:
                    print(f"    Deleting: {playlist_id}...")
                    if self._delete_playlist(self.target_yt, playlist_id):
                        print(f"      ✓ Deleted playlist {playlist_id}")
                    else:
                        print(f"      ✗ Failed to delete playlist {playlist_id}")
            print()

    def deduplicate_videos(self):
        """Remove duplicate videos from all playlists on target account."""
        print("\n=== Deduplicating Videos in Playlists ===\n")
        
        # Get all playlists
        playlists = self._get_user_playlists(self.target_yt, include_special=False)
        
        total_removed = 0
        total_duplicates_found = 0
        
        # Add Liked Music (Migrated) playlist to the list if it exists
        target_playlist_map = self._get_existing_playlists_map(self.target_yt)
        liked_playlist_name = "Liked Music (Migrated)"
        liked_playlist = None
        if liked_playlist_name in target_playlist_map:
            liked_playlist = {
                'id': target_playlist_map[liked_playlist_name],
                'snippet': {'title': liked_playlist_name}
            }
        
        # Add Watch Later playlist ID
        wl_id = SPECIAL_PLAYLISTS['watch_later']
        
        # Process user-created playlists
        if not playlists:
            print("No user-created playlists found on target account")
        
        for playlist in playlists:
            playlist_id = playlist['id']
            playlist_name = playlist['snippet']['title']
            
            # Find duplicates in this playlist
            duplicates = self._get_duplicate_videos_in_playlist(self.target_yt, playlist_id)
            
            if not duplicates:
                continue
            
            total_duplicates_found += 1
            print(f"Playlist '{playlist_name}' ({playlist_id}):")
            
            # Get all items to find their IDs
            try:
                items = self._get_playlist_items(self.target_yt, playlist_id)
            except HttpError as e:
                print(f"  ✗ Could not read items: {e}")
                continue
            
            # Build map of video_id -> [item_ids to keep, item_ids to delete]
            # Keep first occurrence, delete subsequent ones
            video_to_items = {}
            for item in items:
                if 'videoId' in item['snippet'].get('resourceId', {}):
                    vid = item['snippet']['resourceId']['videoId']
                    if vid not in video_to_items:
                        video_to_items[vid] = {'keep': item['id'], 'delete': []}
                    else:
                        video_to_items[vid]['delete'].append(item['id'])
            
            # Delete duplicate items
            for vid, item_data in video_to_items.items():
                if item_data['delete']:
                    print(f"  Video {vid}: keeping {item_data['keep']}, removing {len(item_data['delete'])} duplicates")
                    for item_id in item_data['delete']:
                        if self.args.dry_run:
                            print(f"    Would delete item: {item_id}")
                        else:
                            if self._remove_playlist_item(self.target_yt, playlist_id, item_id):
                                total_removed += 1
            print()
        
        # Also deduplicate Liked Music (Migrated) playlist
        if liked_playlist:
            playlist_id = liked_playlist['id']
            playlist_name = liked_playlist['snippet']['title']
            
            duplicates = self._get_duplicate_videos_in_playlist(self.target_yt, playlist_id)
            
            if duplicates:
                total_duplicates_found += 1
                print(f"Playlist '{playlist_name}' ({playlist_id}):")
                
                try:
                    items = self._get_playlist_items(self.target_yt, playlist_id)
                except HttpError as e:
                    print(f"  ✗ Could not read items: {e}")
                    return
                
                video_to_items = {}
                for item in items:
                    if 'videoId' in item['snippet'].get('resourceId', {}):
                        vid = item['snippet']['resourceId']['videoId']
                        if vid not in video_to_items:
                            video_to_items[vid] = {'keep': item['id'], 'delete': []}
                        else:
                            video_to_items[vid]['delete'].append(item['id'])
                
                for vid, item_data in video_to_items.items():
                    if item_data['delete']:
                        print(f"  Video {vid}: keeping {item_data['keep']}, removing {len(item_data['delete'])} duplicates")
                        for item_id in item_data['delete']:
                            if self.args.dry_run:
                                print(f"    Would delete item: {item_id}")
                            else:
                                if self._remove_playlist_item(self.target_yt, playlist_id, item_id):
                                    total_removed += 1
                print()
        
        # Also deduplicate Watch Later playlist
        try:
            wl_items = self._get_playlist_items(self.target_yt, wl_id)
        except HttpError as e:
            wl_items = []
        
        if wl_items:
            # Check for duplicates in Watch Later
            video_to_items = {}
            for item in wl_items:
                if 'videoId' in item['snippet'].get('resourceId', {}):
                    vid = item['snippet']['resourceId']['videoId']
                    if vid not in video_to_items:
                        video_to_items[vid] = {'keep': item['id'], 'delete': []}
                    else:
                        video_to_items[vid]['delete'].append(item['id'])
            
            # Remove duplicates from Watch Later
            has_wl_duplicates = False
            for vid, item_data in video_to_items.items():
                if item_data['delete']:
                    has_wl_duplicates = True
                    break
            
            if has_wl_duplicates:
                total_duplicates_found += 1
                print(f"Playlist 'Watch Later' ({wl_id}):")
                for vid, item_data in video_to_items.items():
                    if item_data['delete']:
                        print(f"  Video {vid}: keeping {item_data['keep']}, removing {len(item_data['delete'])} duplicates")
                        for item_id in item_data['delete']:
                            if self.args.dry_run:
                                print(f"    Would delete item: {item_id}")
                            else:
                                if self._remove_playlist_item(self.target_yt, wl_id, item_id):
                                    total_removed += 1
                print()
        
        if self.args.dry_run:
            if total_duplicates_found > 0:
                print(f"Dry run: Would remove {total_removed} duplicate video entries from {total_duplicates_found} playlists")
            else:
                print("No duplicate videos found in any playlist")
        else:
            if total_removed > 0:
                print(f"✓ Removed {total_removed} duplicate video entries from {total_duplicates_found} playlists")
            else:
                print("No duplicate videos found in any playlist")

    def _verify_watch_later_completion(self) -> Dict:
        """Verify that Watch Later on target matches source WL playlist.
        
        Returns dict with completion status.
        """
        wl_id = SPECIAL_PLAYLISTS['watch_later']
        
        # Get source Watch Later videos
        try:
            source_items = self._get_playlist_items(self.source_yt, wl_id)
            source_videos = set()
            for item in source_items:
                if 'videoId' in item['snippet'].get('resourceId', {}):
                    source_videos.add(item['snippet']['resourceId']['videoId'])
        except HttpError as e:
            return {'error': f"Source WL: {e}", 'type': 'watch_later'}
        
        if not source_videos:
            return {'error': "No videos in Watch Later on source", 'type': 'watch_later'}
        
        # Get target Watch Later videos
        try:
            target_items = self._get_playlist_items(self.target_yt, wl_id)
            target_videos = []
            for item in target_items:
                if 'videoId' in item['snippet'].get('resourceId', {}):
                    target_videos.append(item['snippet']['resourceId']['videoId'])
            target_videos_set = set(target_videos)
        except HttpError as e:
            return {'error': f"Target WL: {e}", 'type': 'watch_later'}
        
        # Calculate differences
        missing_in_target = source_videos - target_videos_set
        extra_in_target = target_videos_set - source_videos
        
        # Check for duplicates in target
        duplicates_in_target = {}
        for vid in target_videos:
            if target_videos.count(vid) > 1 and vid not in duplicates_in_target:
                positions = [i for i, v in enumerate(target_videos) if v == vid]
                duplicates_in_target[vid] = positions[1:]
        
        return {
            'type': 'watch_later',
            'source_count': len(source_videos),
            'target_count': len(target_videos),
            'unique_target_count': len(target_videos_set),
            'missing_in_target': missing_in_target,
            'extra_in_target': extra_in_target,
            'duplicates_in_target': duplicates_in_target,
            'is_complete': len(missing_in_target) == 0 and len(extra_in_target) == 0 and len(duplicates_in_target) == 0
        }

    def _verify_liked_music_completion(self) -> Dict:
        """Verify that Liked Music on target matches source LM playlist.
        
        Returns dict with completion status.
        """
        # Get source Liked Music videos (LM = Liked Music special playlist)
        try:
            source_items = self._get_playlist_items(self.source_yt, SPECIAL_PLAYLISTS['liked_music'])
            source_videos = set()
            for item in source_items:
                if 'videoId' in item['snippet'].get('resourceId', {}):
                    source_videos.add(item['snippet']['resourceId']['videoId'])
        except HttpError as e:
            return {'error': f"Source LM: {e}", 'type': 'liked_music'}
        
        # Also try Liked Videos (LL) as fallback
        try:
            ll_items = self._get_playlist_items(self.source_yt, SPECIAL_PLAYLISTS['liked_videos'])
            for item in ll_items:
                if 'videoId' in item['snippet'].get('resourceId', {}):
                    source_videos.add(item['snippet']['resourceId']['videoId'])
        except HttpError:
            pass  # LL might not exist, that's okay
        
        if not source_videos:
            return {'error': "No liked music found on source", 'type': 'liked_music'}
        
        # Find target Liked Music (Migrated) playlist
        target_playlist_map = self._get_existing_playlists_map(self.target_yt)
        liked_playlist_name = "Liked Music (Migrated)"
        
        if liked_playlist_name not in target_playlist_map:
            return {
                'error': f"Target playlist '{liked_playlist_name}' not found",
                'type': 'liked_music',
                'source_count': len(source_videos)
            }
        
        target_id = target_playlist_map[liked_playlist_name]
        
        # Get target videos
        try:
            target_items = self._get_playlist_items(self.target_yt, target_id)
            target_videos = []
            for item in target_items:
                if 'videoId' in item['snippet'].get('resourceId', {}):
                    target_videos.append(item['snippet']['resourceId']['videoId'])
            target_videos_set = set(target_videos)
        except HttpError as e:
            return {'error': f"Target: {e}", 'type': 'liked_music'}
        
        # Calculate differences
        missing_in_target = source_videos - target_videos_set
        extra_in_target = target_videos_set - source_videos
        
        # Check for duplicates in target
        duplicates_in_target = {}
        for vid in target_videos:
            if target_videos.count(vid) > 1 and vid not in duplicates_in_target:
                positions = [i for i, v in enumerate(target_videos) if v == vid]
                duplicates_in_target[vid] = positions[1:]
        
        return {
            'type': 'liked_music',
            'source_count': len(source_videos),
            'target_count': len(target_videos),
            'unique_target_count': len(target_videos_set),
            'missing_in_target': missing_in_target,
            'extra_in_target': extra_in_target,
            'duplicates_in_target': duplicates_in_target,
            'is_complete': len(missing_in_target) == 0 and len(extra_in_target) == 0 and len(duplicates_in_target) == 0
        }

    def verify_completion(self):
        """Verify that all migrated playlists on target match source exactly."""
        print("\n=== Verifying Playlist Completion ===\n")
        
        # Get source playlists
        source_playlists = self._get_user_playlists(self.source_yt, include_special=False)
        
        all_complete = True
        total_missing = 0
        total_extra = 0
        total_duplicates = 0
        
        # Verify user-created playlists
        if source_playlists:
            # Build target playlist map
            target_playlist_map = self._get_existing_playlists_map(self.target_yt)
            
            for playlist in source_playlists:
                playlist_name = playlist['snippet']['title']
                source_id = playlist['id']
                
                if playlist_name not in target_playlist_map:
                    print(f"  ✗ MISSING: Playlist '{playlist_name}' not found on target")
                    all_complete = False
                    continue
                
                target_id = target_playlist_map[playlist_name]
                result = self._verify_playlist_completion(
                    self.source_yt, self.target_yt, source_id, target_id
                )
                
                if 'error' in result:
                    print(f"  ✗ ERROR checking '{playlist_name}': {result['error']}")
                    all_complete = False
                    continue
                
                is_complete = result['is_complete']
                status_icon = "✓" if is_complete else "✗"
                
                print(f"  {status_icon} {playlist_name}:")
                print(f"      Source: {result['source_count']} videos")
                print(f"      Target: {result['unique_target_count']} unique videos ({result['target_count']} total)")
                
                if result['missing_in_target']:
                    print(f"      Missing in target: {len(result['missing_in_target'])} videos")
                    total_missing += len(result['missing_in_target'])
                    all_complete = False
                
                if result['extra_in_target']:
                    print(f"      Extra in target: {len(result['extra_in_target'])} videos")
                    total_extra += len(result['extra_in_target'])
                    all_complete = False
                
                if result['duplicates_in_target']:
                    print(f"      Duplicates in target: {len(result['duplicates_in_target'])} videos")
                    total_duplicates += len(result['duplicates_in_target'])
                    all_complete = False
        else:
            print("No user-created playlists found on source account")
        
        # Verify Liked Music
        print("\n=== Verifying Liked Music ===\n")
        liked_result = self._verify_liked_music_completion()
        
        if 'error' in liked_result:
            if "not found" in liked_result['error']:
                print(f"  ⚠ {liked_result['error']}")
                if 'source_count' in liked_result:
                    print(f"      Source has {liked_result['source_count']} liked videos")
                all_complete = False
            else:
                print(f"  ✗ ERROR: {liked_result['error']}")
                all_complete = False
        else:
            is_complete = liked_result['is_complete']
            status_icon = "✓" if is_complete else "✗"
            
            print(f"  {status_icon} Liked Music (Migrated):")
            print(f"      Source: {liked_result['source_count']} videos")
            print(f"      Target: {liked_result['unique_target_count']} unique videos ({liked_result['target_count']} total)")
            
            if liked_result['missing_in_target']:
                print(f"      Missing in target: {len(liked_result['missing_in_target'])} videos")
                total_missing += len(liked_result['missing_in_target'])
                all_complete = False
            
            if liked_result['extra_in_target']:
                print(f"      Extra in target: {len(liked_result['extra_in_target'])} videos")
                total_extra += len(liked_result['extra_in_target'])
                all_complete = False
            
            if liked_result['duplicates_in_target']:
                print(f"      Duplicates in target: {len(liked_result['duplicates_in_target'])} videos")
                total_duplicates += len(liked_result['duplicates_in_target'])
                all_complete = False
        
        # Verify Watch Later
        print("\n=== Verifying Watch Later ===\n")
        wl_result = self._verify_watch_later_completion()
        
        if 'error' in wl_result:
            if "No videos" in wl_result['error']:
                print(f"  ⚠ {wl_result['error']}")
            else:
                print(f"  ✗ ERROR: {wl_result['error']}")
                all_complete = False
        else:
            is_complete = wl_result['is_complete']
            status_icon = "✓" if is_complete else "✗"
            
            print(f"  {status_icon} Watch Later:")
            print(f"      Source: {wl_result['source_count']} videos")
            print(f"      Target: {wl_result['unique_target_count']} unique videos ({wl_result['target_count']} total)")
            
            if wl_result['missing_in_target']:
                print(f"      Missing in target: {len(wl_result['missing_in_target'])} videos")
                total_missing += len(wl_result['missing_in_target'])
                all_complete = False
            
            if wl_result['extra_in_target']:
                print(f"      Extra in target: {len(wl_result['extra_in_target'])} videos")
                total_extra += len(wl_result['extra_in_target'])
                all_complete = False
            
            if wl_result['duplicates_in_target']:
                print(f"      Duplicates in target: {len(wl_result['duplicates_in_target'])} videos")
                total_duplicates += len(wl_result['duplicates_in_target'])
                all_complete = False
        
        print()
        if all_complete and total_missing == 0 and total_extra == 0 and total_duplicates == 0:
            print("✓ All playlists, liked music, and watch later match exactly!")
        else:
            print(f"✗ {total_missing} missing videos, {total_extra} extra videos, {total_duplicates} duplicate videos found")

    def scan_duplicates(self):
        """Scan for duplicate playlists and duplicate videos without making changes."""
        print("\n=== Scanning for Duplicates (Read-Only) ===\n")
        
        # Scan for duplicate playlists
        print("=== Duplicate Playlists ===")
        duplicates = self._find_duplicate_playlists(self.target_yt)
        
        if not duplicates:
            print("No duplicate playlist names found on target account")
        else:
            for name, playlist_ids in duplicates.items():
                print(f"  ⚠ '{name}': {len(playlist_ids)} duplicate playlists")
                print(f"      Using: {playlist_ids[0]}")
                print(f"      Orphaned: {', '.join(playlist_ids[1:])}")
        print()
        
        # Scan for duplicate videos within playlists
        print("=== Duplicate Videos within Playlists ===")
        playlists = self._get_user_playlists(self.target_yt, include_special=False)
        
        has_duplicates = False
        
        if not playlists:
            print("No user-created playlists found on target account")
        else:
            for playlist in playlists:
                playlist_name = playlist['snippet']['title']
                playlist_id = playlist['id']
                
                duplicates = self._get_duplicate_videos_in_playlist(self.target_yt, playlist_id)
                
                if duplicates:
                    has_duplicates = True
                    print(f"  ⚠ '{playlist_name}' ({playlist_id}):")
                    for vid, positions in duplicates.items():
                        print(f"      Video {vid}: appears at positions {positions}")
        
        # Also check Liked Music (Migrated) playlist
        target_playlist_map = self._get_existing_playlists_map(self.target_yt)
        liked_playlist_name = "Liked Music (Migrated)"
        if liked_playlist_name in target_playlist_map:
            liked_id = target_playlist_map[liked_playlist_name]
            duplicates = self._get_duplicate_videos_in_playlist(self.target_yt, liked_id)
            if duplicates:
                has_duplicates = True
                print(f"  ⚠ '{liked_playlist_name}' ({liked_id}):")
                for vid, positions in duplicates.items():
                    print(f"      Video {vid}: appears at positions {positions}")
        
        # Also check Watch Later playlist
        wl_id = SPECIAL_PLAYLISTS['watch_later']
        try:
            wl_duplicates = self._get_duplicate_videos_in_playlist(self.target_yt, wl_id)
            if wl_duplicates:
                has_duplicates = True
                print(f"  ⚠ 'Watch Later' ({wl_id}):")
                for vid, positions in wl_duplicates.items():
                    print(f"      Video {vid}: appears at positions {positions}")
        except HttpError as e:
            # Watch Later might not exist or be inaccessible
            pass
        
        if not has_duplicates:
            print("No duplicate videos found within any playlist")
        
        print("\nScan complete. Use --prune-duplicate-playlists or --deduplicate-videos to fix issues.")

    def run(self):
        """Run the migration based on command line arguments."""
        try:
            # Handle scan-only modes first (no modifications)
            if self.args.scan_only:
                self.scan_migration_status()
                return
            
            if self.args.scan_duplicates:
                self.scan_duplicates()
                return
            
            # Handle verification and cleanup modes
            if self.args.verify_completion:
                self.verify_completion()
                return
            
            if self.args.prune_duplicate_playlists:
                self.prune_duplicate_playlists()
                return
            
            if self.args.deduplicate_videos:
                self.deduplicate_videos()
                return
            
            # Regular migration
            if self.args.all:
                print("Starting full migration...\n")
                self.migrate_playlists()
                self.migrate_watch_later()
                self.migrate_liked_songs()
                self.migrate_subscriptions()
            else:
                if self.args.playlists:
                    self.migrate_playlists()
                if self.args.watch_later:
                    self.migrate_watch_later()
                if self.args.liked_songs:
                    self.migrate_liked_songs()
                if self.args.subscriptions:
                    self.migrate_subscriptions()
            
            print("\n=== Migration Complete ===")
            
        except KeyboardInterrupt:
            print("\n\nMigration interrupted by user.")
            sys.exit(1)
        except Exception as e:
            print(f"\n\nError: {e}")
            if self.args.debug:
                import traceback
                traceback.print_exc()
            sys.exit(1)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog='ytmusic_migrate',
        description='Migrate YouTube Music data between accounts',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Migrate all data from one account to another
  ytmusic_migrate --source old_channel --target new_channel --all

  # Migrate only playlists
  ytmusic_migrate --playlists

  # Migrate Watch Later and Liked Songs
  ytmusic_migrate --watch-later --liked-songs

  # Use with JSON credentials file
  ytmusic_migrate -c yt_credentials.json --all

  # Use with environment variables for credentials
  export GOOGLE_CLIENT_ID=your_client_id
  export GOOGLE_CLIENT_SECRET=your_client_secret
  ytmusic_migrate --all
        """
    )
    
    # Account arguments
    parser.add_argument(
        '--source', '-s',
        type=str,
        help='Source YouTube channel ID or "me" (optional, defaults to authenticated user)',
        default=None
    )
    
    parser.add_argument(
        '--target', '-t',
        type=str,
        help='Target YouTube channel ID or "me" (optional, defaults to authenticated user)',
        default=None
    )
    
    # Authentication arguments
    parser.add_argument(
        '--credentials', '-c',
        type=str,
        metavar='FILE',
        help='Path to JSON credentials file (default: credentials.json in current directory)',
        default=None
    )
    
    parser.add_argument(
        '--client-id',
        type=str,
        help='Google OAuth client ID (or set GOOGLE_CLIENT_ID env var, or use --credentials)',
        default=None
    )
    
    parser.add_argument(
        '--client-secret',
        type=str,
        help='Google OAuth client secret (or set GOOGLE_CLIENT_SECRET env var, or use --credentials)',
        default=None
    )
    
    # Migration type arguments (at least one required)
    migration_group = parser.add_argument_group(
        'Migration Options',
        'Specify what to migrate (at least one required)'
    )
    
    migration_group.add_argument(
        '--all', '-a',
        action='store_true',
        help='Migrate all data (playlists, watch later, liked songs, subscriptions)'
    )
    
    migration_group.add_argument(
        '--playlists', '-p',
        action='store_true',
        help='Migrate user-created playlists'
    )
    
    migration_group.add_argument(
        '--watch-later', '-w',
        action='store_true',
        help='Migrate Watch Later list'
    )
    
    migration_group.add_argument(
        '--liked-songs', '-l',
        action='store_true',
        help='Migrate Liked Songs (from Liked Music playlist)'
    )
    
    migration_group.add_argument(
        '--subscriptions',
        action='store_true',
        help='Migrate subscribed channels'
    )
    
    # Other arguments
    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        help='Enable debug output'
    )
    
    parser.add_argument(
        '--check-existing',
        action='store_true',
        default=True,
        help='Check target account for existing subscriptions/playlists before migrating (default: enabled)'
    )
    
    parser.add_argument(
        '--no-check-existing',
        action='store_false',
        dest='check_existing',
        help='Skip checking existing items on target (faster but may create duplicates)'
    )
    
    parser.add_argument(
        '--reset-state',
        action='store_true',
        help='Reset migration state (forces re-migration of all items)'
    )
    
    parser.add_argument(
        '--scan-only',
        action='store_true',
        help='Scan and report what needs to be migrated without making changes (uses minimal quota)'
    )

    # Duplicate and verification options
    parser.add_argument(
        '--verify-completion',
        action='store_true',
        help='Verify that migrated playlists on target match source exactly'
    )

    parser.add_argument(
        '--prune-duplicate-playlists',
        action='store_true',
        help='Delete orphaned duplicate playlists on target account (requires --check-existing)'
    )

    parser.add_argument(
        '--deduplicate-videos',
        action='store_true',
        help='Check and remove duplicate videos within playlists on target account'
    )

    parser.add_argument(
        '--scan-duplicates',
        action='store_true',
        help='Scan for duplicate playlists and videos without making changes (quota-light)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without actually making them (use with --prune-duplicate-playlists or --deduplicate-videos)'
    )
    
    return parser.parse_args()


def validate_args(args):
    """Validate command line arguments."""
    # Check that at least one migration type is specified
    if not any([args.all, args.playlists, args.watch_later, args.liked_songs, args.subscriptions]):
        print("Error: You must specify at least one migration type.")
        print("Use --all, --playlists, --watch-later, --liked-songs, or --subscriptions")
        sys.exit(1)
    
    # Check if --all is combined with other flags
    if args.all and any([args.playlists, args.watch_later, args.liked_songs, args.subscriptions]):
        print("Warning: --all overrides other migration type flags")


def main():
    """Main entry point."""
    print("""
    ╔══════════════════════════════════════════════════════╗
    ║        YTMusic Migrate - YouTube Music Migration Tool       ║
    ║                  v1.2.0 | github.com/gitgatgit             ║
    ╚══════════════════════════════════════════════════════╝
    """)
    
    args = parse_args()
    validate_args(args)
    
    # Print configuration summary
    print(f"\nConfiguration:")
    print(f"  Source account: {args.source or 'current user'}")
    print(f"  Target account: {args.target or 'current user'}")
    print(f"  Migration types: ", end="")
    types = []
    if args.all:
        types.append("ALL")
    else:
        if args.playlists:
            types.append("playlists")
        if args.watch_later:
            types.append("watch_later")
        if args.liked_songs:
            types.append("liked_songs")
        if args.subscriptions:
            types.append("subscriptions")
    print(", ".join(types))
    
    # Run the migration
    tool = YTMusicMigrationTool(args)
    tool.run()


if __name__ == "__main__":
    main()
