from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.metadata import (
    store_igdb_game_taxonomies,
    store_tmdb_movie_taxonomies,
)
from app.models import (
    Game,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
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
