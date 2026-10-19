from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class SteamAppRatingsCommandTests(SimpleTestCase):
    """Test the manual resumable Steam app ratings backfill command."""

    @patch(
        "app.management.commands.backfill_steam_app_ratings."
        "backfill_steam_app_ratings.run",
    )
    def test_command_forwards_cursor_and_reports_progress(self, mock_run):
        """The command executes one bounded batch and prints its cursor."""
        mock_run.return_value = {
            "processed": 2,
            "succeeded": 1,
            "failed_ids": [44],
            "next_after_item_id": 42,
            "has_more": True,
        }
        output = StringIO()

        call_command(
            "backfill_steam_app_ratings",
            batch_size=2,
            after_item_id=40,
            stdout=output,
        )

        mock_run.assert_called_once_with(batch_size=2, after_item_id=40)
        self.assertIn('"next_after_item_id": 42', output.getvalue())
        self.assertIn('"has_more": true', output.getvalue())
