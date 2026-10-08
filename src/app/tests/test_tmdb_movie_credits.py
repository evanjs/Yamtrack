from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.metadata import store_tmdb_movie_credits
from app.models import (
    BasicMedia,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
    TMDBMovieCreditAssignment,
    TMDBMovieMetadata,
)
from users.models import MediaStatusChoices


class TMDBMovieCreditPersistenceTests(TestCase):
    """Persist TMDB people and role-specific movie credit relationships."""

    def setUp(self):
        """Create a shared TMDB movie catalog item."""
        self.item = Item.objects.create(
            media_id="321",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Example Movie",
            image="https://example.com/poster.jpg",
        )

    def test_persists_cast_and_crew_roles_and_relationship_details(self):
        """Stored credits preserve person IDs, crew jobs, and cast details."""
        metadata = store_tmdb_movie_credits(
            self.item,
            {
                "cast": [
                    {
                        "id": 10,
                        "name": "A Person",
                        "character": "Lead",
                        "order": 0,
                    },
                ],
                "crew": [
                    {
                        "id": 11,
                        "name": "A Director",
                        "department": "Directing",
                        "job": "Director",
                    },
                    {
                        "id": 12,
                        "name": "A Writer",
                        "department": "Writing",
                        "job": "Screenplay",
                    },
                ],
            },
        )

        self.assertEqual(
            set(
                metadata.credit_assignments.values_list(
                    "person__provider_id",
                    "credit_type",
                    "job",
                    "character",
                    "cast_order",
                ),
            ),
            {
                (10, TMDBMovieCreditAssignment.CreditType.CAST, "Actor", "Lead", 0),
                (11, TMDBMovieCreditAssignment.CreditType.CREW, "Director", "", None),
                (12, TMDBMovieCreditAssignment.CreditType.CREW, "Screenplay", "", None),
            },
        )

    def test_successful_empty_credits_replace_previous_relationships(self):
        """A successful empty response removes prior credit assignments."""
        store_tmdb_movie_credits(
            self.item,
            {"cast": [{"id": 10, "name": "A Person"}], "crew": []},
        )

        metadata = store_tmdb_movie_credits(self.item, {"cast": [], "crew": []})

        self.assertEqual(metadata.credit_assignments.count(), 0)
        self.assertTrue(TMDBMovieMetadata.objects.filter(item=self.item).exists())

    def test_invalid_credits_preserve_last_good_relationships(self):
        """Invalid provider data leaves the previous credit relationships intact."""
        store_tmdb_movie_credits(
            self.item,
            {"cast": [{"id": 10, "name": "A Person"}], "crew": []},
        )

        with self.assertRaises(ValueError):
            store_tmdb_movie_credits(
                self.item,
                {"cast": [{"id": 0, "name": "Invalid"}], "crew": []},
            )

        self.assertEqual(
            list(
                self.item.tmdb_movie_metadata.credit_assignments.values_list(
                    "person__provider_id",
                    flat=True,
                ),
            ),
            [10],
        )


class TMDBMovieCreditFilterTests(TestCase):
    """Filter movie libraries by additive role/person credit rules."""

    def setUp(self):
        """Create two tracked movies with overlapping cast credits."""
        self.user = get_user_model().objects.create_user(username="credits-test")
        self.items = []
        for media_id, title in (("101", "Both Credits"), ("202", "Cast Only")):
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
            self.items.append(item)
        store_tmdb_movie_credits(
            self.items[0],
            {
                "cast": [{"id": 20, "name": "Actor Person"}],
                "crew": [{"id": 30, "name": "Director Person", "job": "Director"}],
            },
        )
        store_tmdb_movie_credits(
            self.items[1],
            {"cast": [{"id": 20, "name": "Actor Person"}], "crew": []},
        )

    def test_credit_rules_filter_by_any_or_all_match(self):
        """Any and All combine additive role/person rules as advertised."""
        base = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter=None,
            credit_filters=[
                {"role": "cast", "person_id": 20},
                {"role": "Director", "person_id": 30},
            ],
            credit_match="all",
        )

        self.assertEqual([entry.item.title for entry in base], ["Both Credits"])

        any_matches = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter=None,
            credit_filters=[
                {"role": "cast", "person_id": 20},
                {"role": "Director", "person_id": 30},
            ],
            credit_match="any",
        )

        self.assertEqual(
            {entry.item.title for entry in any_matches},
            {"Both Credits", "Cast Only"},
        )

    def test_crew_catchall_matches_people_with_any_crew_job(self):
        """The Crew role matches all crew jobs, not a literal job named Crew."""
        movies = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter=None,
            credit_filters=[{"role": "crew", "person_id": 30}],
        )

        self.assertEqual([entry.item.title for entry in movies], ["Both Credits"])

    def test_movie_list_exposes_role_person_filter_and_applies_selection(self):
        """The movie list renders and applies the selected person credit."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?credit_role=Director&credit_person=30",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertEqual(response.context["media_list"][0].item.title, "Both Credits")
        self.assertContains(response, "Credits")
        self.assertContains(response, "Director Person")
        self.assertContains(response, 'name="credit_person"')
        self.assertContains(response, 'hx-trigger="change, submit"')

    def test_movie_list_accepts_the_display_label_for_crew_catchall(self):
        """The Crew label maps to all crew jobs in a submitted filter rule."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value]),
            {"credit_role": "Crew", "credit_person": "30"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media_list"].paginator.count, 1)
        self.assertEqual(response.context["media_list"][0].item.title, "Both Credits")

    def test_credit_autocomplete_searches_only_tracked_movie_credits(self):
        """Autocomplete returns people represented in the tracked library."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("tmdb_movie_credit_search", args=[self.user.username]),
            {"q": "Actor", "role": "cast"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"],
            [{"id": 20, "name": "Actor Person"}],
        )

    def test_crew_autocomplete_includes_all_crew_jobs(self):
        """Crew suggestions include people regardless of their specific job."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("tmdb_movie_credit_search", args=[self.user.username]),
            {"q": "Director", "role": "crew"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"],
            [{"id": 30, "name": "Director Person"}],
        )

    def test_empty_credit_filter_results_keep_a_visible_result_target(self):
        """An HTMX filter miss returns a recoverable empty-state fragment."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value]),
            {"credit_role": "Director", "credit_person": "999"},
            headers={"HX-Request": "true", "HX-Target": ".media-grid"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No movies match these filters.")
