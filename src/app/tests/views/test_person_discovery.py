from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.views import calculate_age_on_date


class PersonDiscoveryViewTests(TestCase):
    """Test discovering movies from TMDB person credits."""

    def setUp(self):
        """Create and log in a test user."""
        self.credentials = {"username": "person-search", "password": "12345"}
        get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.tmdb.search_people")
    def test_person_query_renders_person_choices(self, mock_search_people):
        """Render TMDB person search matches."""
        mock_search_people.return_value = [
            {"person_id": 123, "name": "Jordan Peele", "known_for": "Director"},
        ]

        response = self.client.get(
            reverse("discover_by_person"), {"q": "Jordan Peele"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jordan Peele")
        self.assertContains(response, "Director")
        self.assertContains(response, "Discover by person")
        self.assertContains(response, "width: 3rem;")
        self.assertContains(response, "flex: 0 0 3rem;")
        mock_search_people.assert_called_once_with("Jordan Peele")

    @patch("app.providers.tmdb.person_birthday", return_value="1958-05-27")
    @patch("app.providers.tmdb.person_movie_credits")
    def test_selected_person_renders_sorted_credits_and_release_age(
        self,
        mock_credits,
        mock_birthday,
    ):
        """Render sorted credits and the person's age on the release date."""
        mock_credits.return_value = [
            {
                "media_id": "1234",
                "title": "Return of the Living Dead",
                "release_date": "1985-08-16",
                "image": "https://image.tmdb.org/t/p/w500/return.jpg",
                "role": "Director",
            },
        ]

        response = self.client.get(
            reverse("discover_by_person"),
            {
                "person_id": "123",
                "person_name": "Linnea Quigley",
                "role": "director",
                "sort": "title_asc",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Return of the Living Dead")
        self.assertContains(response, "Director")
        self.assertContains(response, "27 yo")
        self.assertContains(response, "title_asc")
        self.assertContains(response, 'class="person-movie-grid"')
        self.assertContains(response, 'hx-get="/track_modal/tmdb/movie/1234"')
        self.assertContains(response, 'title="Add to tracker"')
        self.assertContains(response, 'title="Add to custom lists"')
        self.assertContains(response, 'title="View your activity history"')
        self.assertContains(response, "repeat(2, minmax(0, 1fr))")
        mock_credits.assert_called_once_with("123", "director", "title_asc")
        mock_birthday.assert_called_once_with("123")

    @patch("app.providers.tmdb.person_tv_credits")
    def test_tv_discovery_renders_selected_person_credits(self, mock_credits):
        """Render all-TMDB TV credits with TV-specific date and detail links."""
        mock_credits.return_value = [
            {
                "media_id": "1668",
                "title": "Friends",
                "first_air_date": "1994-09-22",
                "image": "https://image.tmdb.org/t/p/w500/friends.jpg",
                "role": "Chandler Bing",
            },
        ]

        response = self.client.get(
            reverse("discover_tv_by_person"),
            {"person_id": "123", "person_name": "Matthew Perry", "role": "all"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Discover TV by person")
        self.assertContains(response, "Friends")
        self.assertContains(response, "1994")
        self.assertContains(response, "/details/tmdb/tv/1668/friends")
        self.assertContains(response, 'hx-get="/track_modal/tmdb/tv/1668"')
        self.assertContains(response, 'title="Add to tracker"')
        mock_credits.assert_called_once_with("123", "all", "release_desc")


class CalculateAgeOnDateTests(TestCase):
    """Test release-date age calculations."""

    def test_age_is_exact_at_release_date(self):
        """Adjust age based on whether the birthday has passed that year."""
        self.assertEqual(calculate_age_on_date("1958-05-27", "1985-08-16"), 27)
        self.assertEqual(calculate_age_on_date("1958-05-27", "1985-01-01"), 26)

    def test_age_is_missing_for_unknown_or_invalid_dates(self):
        """Do not guess an age if either date is absent or malformed."""
        self.assertIsNone(calculate_age_on_date(None, "1985-08-16"))
        self.assertIsNone(calculate_age_on_date("1958-05-27", "1985"))


class PersonAgeEndpointTests(TestCase):
    """Test lazy cast-card age annotation requests."""

    def setUp(self):
        """Create and log in a test user."""
        credentials = {"username": "person-age", "password": "12345"}
        get_user_model().objects.create_user(**credentials)
        self.client.login(**credentials)

    @patch("app.providers.tmdb.person_birthday", return_value="1958-05-27")
    def test_returns_age_at_release_date(self, mock_birthday):
        """Calculate a card age from the cached person birthday and movie date."""
        response = self.client.get(
            reverse("tmdb_person_age", kwargs={"person_id": "678"}),
            {"release_date": "1985-04-25"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "26 yo at release")
        mock_birthday.assert_called_once_with("678")

    @patch("app.providers.tmdb.person_birthday", return_value=None)
    def test_omits_age_when_birthday_is_not_listed(self, mock_birthday):
        """Leave the card annotation empty when TMDB has no birthday."""
        response = self.client.get(
            reverse("tmdb_person_age", kwargs={"person_id": "678"}),
            {"release_date": "1985-04-25"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.strip(), b"")
        mock_birthday.assert_called_once_with("678")

    @patch("app.providers.tmdb.person_birthday")
    def test_omits_age_when_release_date_is_missing(self, mock_birthday):
        """Skip the provider request if the card has no full release date."""
        response = self.client.get(
            reverse("tmdb_person_age", kwargs={"person_id": "678"}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.strip(), b"")
        mock_birthday.assert_not_called()
