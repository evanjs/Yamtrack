from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0058_user_include_unknown_taxonomy"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="obfuscate_unseen_seasons",
            field=models.BooleanField(
                default=False,
                help_text="Obfuscate details for unseen TV seasons",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="obfuscate_unseen_season_titles",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="user",
            name="obfuscate_unseen_season_artwork",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="user",
            name="obfuscate_unseen_season_episode_counts",
            field=models.BooleanField(default=True),
        ),
    ]
