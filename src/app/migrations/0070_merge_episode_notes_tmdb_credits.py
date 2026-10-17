# Merge migration branches for episode notes and TMDB movie credits.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0065_episode_notes_historicalepisode_notes"),
        ("app", "0069_tmdbmoviemetadata_credits_synced_at"),
    ]

    operations = []
