"""Opt-in music sources sharing the bounded subprocess transport."""

from .yt_dlp import YtDlpProvider


class SoundCloudProvider(YtDlpProvider):
    """Search SoundCloud and acquire a complete, unprotected recording."""

    name = "soundcloud"
    worker_module = "local_music_acquisition.providers._music_sites_worker"


class BandcampProvider(YtDlpProvider):
    """Search Bandcamp and acquire an artist-enabled original download."""

    name = "bandcamp"
    worker_module = "local_music_acquisition.providers._music_sites_worker"
