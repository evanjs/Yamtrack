import csv
from io import StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import translation
from django.utils.html import escape

from app.metadata import (
    store_igdb_game_taxonomies,
    store_tmdb_movie_taxonomies,
)
from app.models import (
    TV,
    Game,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
    SteamAppMapping,
    SteamAppRating,
)
from app.templatetags import app_tags
from users.forms import UserUpdateForm


class MediaListViewTests(TestCase):
    """Test the media list view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.external_credentials = {
            "username": "test2",
            "password": "12345",
            "profile_private": True,
        }
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.external_user = get_user_model().objects.create_user(
            **self.external_credentials
        )
        self.client.login(**self.credentials)

        movies_id = ["278", "238", "129", "424", "680"]
        num_completed = 3
        for i in range(1, 6):
            item = Item.objects.create(
                media_id=movies_id[i - 1],
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Test Movie {i}",
                image="http://example.com/image.jpg",
            )
            status = (
                Status.COMPLETED.value
                if i < num_completed
                else Status.IN_PROGRESS.value
            )
            Movie.objects.create(
                item=item,
                user=self.user,
                status=status,
                progress=1 if i < num_completed else 0,
                score=i,
            )

    def test_media_list_view(self):
        """Test the media list view displays media items."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_list.html")

        self.assertIn("media_list", response.context)
        self.assertEqual(response.context["media_list"].paginator.count, 5)

        self.assertIn("sort_choices", response.context)
        self.assertIn("status_choices", response.context)
        self.assertEqual(response.context["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(
            response.context["media_type_plural"],
            app_tags.media_type_readable_plural(MediaTypes.MOVIE.value).lower(),
        )

    def test_filtered_csv_export_is_unpaginated_and_private(self):
        """CSV export follows filters, includes every match, and denies other users."""
        items = [
            Item.objects.create(
                media_id=f"export-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Export Movie {index}",
                image="http://example.com/image.jpg",
            )
            for index in range(40)
        ]
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.user,
                    status=(
                        Status.COMPLETED.value
                        if index % 2 == 0
                        else Status.PLANNING.value
                    ),
                )
                for index, item in enumerate(items)
            ]
        )

        response = self.client.get(
            reverse("media_list_export_csv", args=[self.user.username, "movie"]),
            {"status": Status.COMPLETED.value, "search": "Export Movie"},
        )
        self.assertEqual(response.status_code, 200)
        exported = response.content.decode()
        self.assertEqual(exported.count("Export Movie"), 20)
        self.assertNotIn("Test Movie", exported)

        response = self.client.get(
            reverse(
                "media_list_export_csv",
                args=[self.external_user.username, "movie"],
            ),
        )
        self.assertEqual(response.status_code, 404)

    def test_single_type_list_shows_filtered_export_control_only_for_owner(self):
        """Filtered CSV control is rendered only for the current user's list."""
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])
        own = self.client.get(
            reverse("medialist", args=[self.user.username, "movie"]),
        )
        other = self.client.get(
            reverse("medialist", args=[self.external_user.username, "movie"]),
        )
        self.assertContains(own, "Export filtered CSV")
        self.assertNotContains(other, "Export filtered CSV")

    def test_export_columns_cover_anime_and_manga_progress(self):
        """Anime and manga CSVs expose their tracking progress columns."""
        for media_type, progress_column in (
            ("anime", "episodes_watched"),
            ("manga", "chapters_read"),
        ):
            with self.subTest(media_type=media_type):
                response = self.client.get(
                    reverse(
                        "media_list_export_csv",
                        args=[self.user.username, media_type],
                    ),
                )
                self.assertEqual(response.status_code, 200)
                header = response.content.decode().splitlines()[0]
                self.assertIn(progress_column, header)
                self.assertIn("status", header)
                self.assertNotIn("volumes_read", header)

    def test_csv_export_neutralizes_spreadsheet_formula_values(self):
        """Formula-like titles and notes are exported as text, not formulas."""
        item = Item.objects.create(
            media_id="formula-title",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="=1+1",
            image="http://example.com/image.jpg",
        )
        Movie.objects.bulk_create(
            [Movie(item=item, user=self.user, notes="\t=1+1")],
        )

        response = self.client.get(
            reverse("media_list_export_csv", args=[self.user.username, "movie"]),
        )
        rows = list(csv.reader(StringIO(response.content.decode())))
        exported = next(row for row in rows[1:] if row[1] == "formula-title")

        self.assertEqual(exported[0], "'=1+1")
        self.assertEqual(exported[9], "'\t=1+1")

    def test_empty_media_list_labels_use_singular_and_plural(self):
        """The empty state uses a singular title and plural description."""
        self.addCleanup(translation.deactivate)
        media_type = MediaTypes.COMIC.value

        for language, _name in settings.LANGUAGES:
            self.user.language = language
            self.user.save()

            response = self.client.get(
                reverse("medialist", args=[self.user.username, media_type])
            )

            with translation.override(language):
                expected_title = translation.gettext(
                    "No %(media_type_label)s Tracked Yet"
                ) % {
                    "media_type_label": escape(
                        app_tags.media_type_readable(media_type)
                    ),
                }
                expected_description = translation.gettext(
                    "Start tracking your %(media_type_plural)s to keep a record of "
                    "what you've %(past_verb)s."
                ) % {
                    "media_type_plural": escape(
                        app_tags.media_type_readable_plural(media_type)
                    ),
                    "past_verb": escape(app_tags.media_past_verb(media_type)),
                }

            with self.subTest(language=language):
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_title)
                self.assertContains(response, expected_description)

    def test_media_list_with_filters(self):
        """Test the media list view with filters."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?status=Completed&sort=score&layout=table",
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.context["current_status"],
            Status.COMPLETED.value,
        )
        self.assertEqual(response.context["current_sort"], "score")
        self.assertEqual(response.context["current_layout"], "table")

        self.assertEqual(response.context["media_list"].paginator.count, 2)
        self.assertContains(response, 'hx-trigger="submit"')
        self.assertContains(response, "htmx.trigger($refs.filterForm, 'submit')")
        self.assertContains(response, 'hx-trigger="input changed delay:300ms, search"')
        self.assertRegex(response.content.decode(), r"(?s)sortLabels:.*?End Date[^}]*}\s*}")

        self.user.refresh_from_db()
        self.assertEqual(self.user.movie_status, Status.COMPLETED.value)
        self.assertEqual(self.user.movie_sort, "score")
        self.assertEqual(self.user.movie_layout, "table")

    def test_game_list_filters_by_repeated_igdb_taxonomy_ids(self):
        """Game list combines repeated facet IDs across taxonomy kinds."""
        taxonomy_values = [
            ("101", "First Game", 4, "Fighting", 10, "Fantasy"),
            ("202", "Second Game", 5, "Adventure", 11, "Science fiction"),
        ]
        for media_id, title, genre_id, genre, theme_id, theme in taxonomy_values:
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value,
                title=title,
                image="http://example.com/image.jpg",
            )
            Game.objects.create(item=item, user=self.user, status=Status.COMPLETED)
            store_igdb_game_taxonomies(
                item,
                {
                    "genres": [{"id": genre_id, "name": genre}],
                    "themes": [{"id": theme_id, "name": theme}],
                    "keywords": [],
                },
            )

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.GAME.value])
            + "?genre=4&genre=5&theme_exclude=11",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IGDB facets")
        self.assertContains(response, 'role="tablist"')
        self.assertContains(response, 'role="tab"')
        self.assertContains(response, "Genre")
        self.assertContains(response, "Theme")
        self.assertContains(response, "Keyword")
        self.assertContains(response, 'name="genre"')
        self.assertContains(response, 'name="theme_exclude"')
        self.assertContains(response, 'value="4"')
        self.assertContains(response, "Click a value to include")
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertEqual(response.context["media_list"][0].item.title, "First Game")
        self.assertEqual(
            response.context["current_taxonomy_filters"],
            {
                "genre": {"include": [4, 5], "exclude": []},
                "theme": {"include": [], "exclude": [11]},
                "keyword": {"include": [], "exclude": []},
            },
        )
        self.assertEqual(
            [
                option["name"]
                for option in response.context["taxonomy_options"]["genre"]
            ],
            ["Adventure", "Fighting"],
        )

    def test_game_card_shows_only_the_viewers_selected_steam_rating(self):
        """A configured agency rating appears on a game card without API access."""
        item = Item.objects.create(
            media_id="777",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Rated Game",
            image="https://example.com/game.jpg",
        )
        Game.objects.create(item=item, user=self.user, status=Status.PLANNING)
        SteamAppMapping.objects.create(item=item, app_ids=["292030"])
        SteamAppRating.objects.create(
            item=item,
            steam_app_id="292030",
            ratings={"esrb": {"rating": "m"}, "pegi": {"rating": "18"}},
        )
        self.user.steam_rating_agency = "esrb"
        self.user.save(update_fields=["steam_rating_agency"])

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.GAME.value]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ESRB")
        self.assertContains(response, "ESRB: M")

    def test_movie_list_filters_by_tmdb_genre_and_keyword(self):
        """Movie list reuses the facet control for TMDB taxonomy values."""
        movie_items = []
        for media_id, title, genre, keyword in [
            ("321", "Horror Movie", (27, "Horror"), (12, "ghost")),
            ("654", "Thriller Movie", (53, "Thriller"), (13, "mystery")),
        ]:
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=title,
                image="https://example.com/poster.jpg",
            )
            Movie.objects.bulk_create(
                [Movie(item=item, user=self.user, status=Status.COMPLETED)],
            )
            store_tmdb_movie_taxonomies(
                item,
                {
                    "genres": [{"id": genre[0], "name": genre[1]}],
                    "keywords": [{"id": keyword[0], "name": keyword[1]}],
                },
            )
            movie_items.append(item)

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?genre=27&genre=53&keyword_exclude=13",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_taxonomy_options"])
        self.assertContains(response, "TMDB facets")
        self.assertContains(response, 'name="keyword_exclude"')
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertEqual(response.context["media_list"][0].item, movie_items[0])
        self.assertEqual(
            response.context["current_taxonomy_filters"]["genre"],
            {"include": [27, 53], "exclude": []},
        )

    def test_media_list_htmx_request(self):
        """Test the media list view with HTMX request."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?layout=grid",
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/media_grid_items.html")

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?layout=table",
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/media_table_items.html")

    def test_media_list_soft_navigation_returns_full_page(self):
        """Soft-navigation body swaps (after an edit modal) get the full page."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value]),
            headers={"hx-request": "true", "x-soft-navigation": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_list.html")

    def test_public_media_list_ignores_invalid_filters(self):
        """Test invalid public filters fall back to the target user's preferences."""
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
            + "?status=invalid&sort=bad_field&layout=invalid",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["current_status"], self.external_user.movie_status
        )
        self.assertEqual(
            response.context["current_sort"], self.external_user.movie_sort
        )
        self.assertEqual(
            response.context["current_layout"], self.external_user.movie_layout
        )

    def test_anonymous_user_can_view_public_media_list(self):
        """Test anonymous users can view public media lists."""
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])
        self.client.logout()

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("media_list", response.context)

    def test_profile_private_defaults_to_true(self):
        """Test new users have private profiles by default."""
        user = get_user_model().objects.create_user(
            username="private-default",
        )

        self.assertTrue(user.profile_private)

    def test_private_media_list(self):
        """Test the private media list view."""
        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )
        self.assertEqual(response.status_code, 404)

        form = UserUpdateForm(
            data={"username": "test2", "profile_private": False},
            instance=self.external_user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        external_user = form.save()
        external_user.refresh_from_db()

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("media_list", response.context)


class SeasonCardObfuscationTests(TestCase):
    """Test season card obfuscation in the tracked-season list."""

    def setUp(self):
        """Create an unseen tracked season without provider calls."""
        self.user = get_user_model().objects.create_user(
            username="season-card-user",
            obfuscate_unseen_seasons=True,
        )
        self.client.force_login(self.user)
        tv_item = Item.objects.create(
            media_id="tv-card",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test Show",
            image="http://example.com/tv.jpg",
        )
        season_item = Item.objects.create(
            media_id="tv-card",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/season.jpg",
            season_number=1,
        )
        tv = TV(item=tv_item, user=self.user, status=Status.IN_PROGRESS.value)
        TV.save_base(tv)
        season = Season(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.PLANNING.value,
        )
        Season.save_base(season)

    def test_unseen_season_card_has_ordinal_and_reveal_control(self):
        """Unseen card titles stay neutral and offer an explicit reveal action."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.SEASON.value]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Show — Season 1")
        self.assertContains(response, 'aria-label="Reveal season details"')
