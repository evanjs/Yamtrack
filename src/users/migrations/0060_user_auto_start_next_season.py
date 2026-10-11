from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0059_user_obfuscate_unseen_seasons"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="auto_start_next_season",
            field=models.BooleanField(
                default=True,
                help_text="Automatically start the next TV season when appropriate",
            ),
        ),
    ]
