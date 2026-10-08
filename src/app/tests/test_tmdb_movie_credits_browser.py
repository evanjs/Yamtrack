import os

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from playwright.sync_api import expect, sync_playwright

from app.metadata import store_tmdb_movie_credits
from app.models import Item, MediaTypes, Movie, Sources, Status


class TMDBMovieCreditFilterBrowserTests(StaticLiveServerTestCase):
    """Exercise the role/person filter's browser interaction."""

    @classmethod
    def setUpClass(cls):
        """Start one Chromium instance for the browser tests."""
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()
        cls.page = cls.browser.new_page()
        cls.page.set_default_timeout(10000)

    @classmethod
    def tearDownClass(cls):
        """Close Chromium after browser tests finish."""
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        """Create a public movie library with one hydrated director credit."""
        self.user = get_user_model().objects.create_user(username="credit-browser")
        self.user.profile_private = False
        self.user.save(update_fields=["profile_private"])
        self.item = Item.objects.create(
            media_id="501",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Credit Browser Movie",
            image="https://example.com/poster.jpg",
        )
        Movie.objects.bulk_create(
            [Movie(item=self.item, user=self.user, status=Status.COMPLETED)],
        )
        store_tmdb_movie_credits(
            self.item,
            {
                "cast": [{"id": 701, "name": "Test Actor", "character": "Lead"}],
                "crew": [
                    {
                        "id": 702,
                        "name": "Test Director",
                        "department": "Directing",
                        "job": "Director",
                    },
                    {
                        "id": 703,
                        "name": "Clive Barker",
                        "department": "Production",
                        "job": "Executive Producer",
                    },
                ],
            },
        )

    def test_add_rule_and_enter_select_person_without_closing_filter(self):
        """Searchable crew jobs and people selection leave the filter open."""
        self.page.goto(f"{self.live_server_url}/{self.user.username}/movie")
        disclosure = self.page.locator("details").filter(
            has=self.page.locator("summary").filter(has_text="Credits"),
        )
        disclosure.locator("summary").click()

        self.page.locator("#add-credit-rule").click()
        rule = self.page.locator("#credit-rules .credit-rule")
        expect(rule).to_have_count(1)
        role = rule.locator(".credit-role-search")
        expect(role).to_have_attribute("list", "credit-role-options")
        expect(
            self.page.locator('#credit-role-options option[value="Cast"]'),
        ).to_have_count(1)
        expect(
            self.page.locator('#credit-role-options option[value="Crew"]'),
        ).to_have_count(1)
        role_menu = rule.locator(".credit-role-options")
        role.focus()
        expect(role_menu).to_be_visible()
        self.assertEqual(
            role_menu.evaluate("element => getComputedStyle(element).backgroundColor"),
            "rgb(32, 37, 42)",
        )
        expect(
            role_menu.locator(".credit-role-option").filter(has_text="Cast"),
        ).to_be_visible()
        expect(
            role_menu.locator(".credit-role-option").filter(has_text="Crew"),
        ).to_be_visible()
        expect(
            role_menu.locator(".credit-role-option").filter(
                has_text="Executive Producer",
            ),
        ).to_be_visible()
        role.fill("Executive")
        expect(role_menu.locator(".credit-role-option:visible")).to_have_count(1)
        role_input_text = role.input_value()
        self.page.locator("h1.text-3xl").click()
        expect(role_menu).to_be_hidden()
        expect(role).to_have_value(role_input_text)
        role.focus()
        expect(role_menu.locator(".credit-role-option:visible")).to_have_count(1)
        expect(
            role_menu.locator(".credit-role-option:visible").first,
        ).to_contain_text("Executive Producer")
        role.fill("Executive Producer")
        role.press("Enter")
        expect(role).to_have_value("Executive Producer")
        search = rule.locator(".credit-person")
        search.fill("Clive Barker")
        suggestion = self.page.locator(".credit-suggestions button").filter(
            has_text="Clive Barker",
        )
        expect(suggestion).to_be_visible()
        self.assertEqual(
            rule.locator(".credit-suggestions").evaluate(
                "element => getComputedStyle(element).backgroundColor",
            ),
            "rgb(32, 37, 42)",
        )
        search.press("Enter")

        expect(rule.locator('input[name="credit_person"]')).to_have_value("703")
        expect(disclosure).to_have_attribute("open", "")
        expect(self.page).to_have_url(
            f"{self.live_server_url}/{self.user.username}/movie",
        )
