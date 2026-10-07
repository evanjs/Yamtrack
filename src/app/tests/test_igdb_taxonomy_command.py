from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class IGDBTaxonomyCommandTests(SimpleTestCase):
    """Test the manual resumable IGDB taxonomy backfill command."""

    @patch(
        "app.management.commands.backfill_igdb_game_taxonomies."
        "backfill_igdb_game_taxonomies.run",
    )
    def test_command_forwards_batch_cursor_and_prints_result(self, mock_run):
        """The command executes one bounded task batch and reports its cursor."""
        mock_run.return_value = {
            "processed": 1,
            "succeeded": 1,
            "failed_ids": [],
            "next_after_item_id": 42,
            "has_more": True,
        }
        output = StringIO()

        call_command(
            "backfill_igdb_game_taxonomies",
            batch_size=1,
            after_item_id=41,
            stdout=output,
        )

        mock_run.assert_called_once_with(batch_size=1, after_item_id=41)
        self.assertIn('"next_after_item_id": 42', output.getvalue())
        self.assertIn('"has_more": true', output.getvalue())
