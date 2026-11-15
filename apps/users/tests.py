from datetime import date

from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from .models import CustomUser, ProfilePhoto
from . import services


class CustomUserDefaultsTests(TestCase):
    def test_new_user_defaults(self):
        user = CustomUser.objects.create_user(username="alice", password="pass1234")
        self.assertEqual(user.preference, "everyone")
        self.assertEqual(user.gender, "")
        self.assertEqual(user.bio, "")


class ProfilePhotoTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="alice", password="pass1234")

    def test_ordered_by_rank(self):
        ProfilePhoto.objects.create(user=self.user, image="photo-b", rank=2)
        ProfilePhoto.objects.create(user=self.user, image="photo-a", rank=1)
        photos = list(self.user.photos.all())
        self.assertEqual([str(p.image) for p in photos], ["photo-a", "photo-b"])

    def test_cascade_delete_with_user(self):
        ProfilePhoto.objects.create(user=self.user, image="photo-a", rank=1)
        self.user.delete()
        self.assertEqual(ProfilePhoto.objects.count(), 0)


class ProfileServiceTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="alice", password="pass1234")

    def test_update_profile_only_changes_passed_fields(self):
        services.update_profile(self.user, {"bio": "hello", "location": "Lagos"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.bio, "")  # bios were removed: profiles are music-only
        self.assertEqual(self.user.location, "Lagos")
        self.assertEqual(self.user.gender, "")
        self.assertEqual(self.user.preference, "everyone")

    def test_update_profile_blank_birthdate_clears_it(self):
        services.update_profile(self.user, {"birthdate": ""})
        self.user.refresh_from_db()
        self.assertIsNone(self.user.birthdate)

    def test_update_profile_parses_birthdate(self):
        services.update_profile(self.user, {"birthdate": "1999-04-12"})
        self.user.refresh_from_db()
        self.assertEqual(str(self.user.birthdate), "1999-04-12")

    def test_update_profile_ignores_invalid_choices(self):
        services.update_profile(self.user, {"gender": "robot", "preference": "robots"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.gender, "")
        self.assertEqual(self.user.preference, "everyone")

    def test_add_and_delete_profile_photo(self):
        photo = services.add_profile_photo(self.user, "photo-a")
        self.assertEqual(self.user.photos.count(), 1)
        self.assertEqual(photo.rank, 0)

        services.delete_profile_photo(self.user, photo.id)
        self.assertEqual(self.user.photos.count(), 0)


class SignupLoginTests(TestCase):
    def test_signup_creates_account_and_starts_onboarding(self):
        response = self.client.post("/signup/", {
            "first_name": "Ada", "email": "Ada@Example.com", "password": "tune-lover-2026",
        })
        self.assertRedirects(response, "/welcome/about/", fetch_redirect_response=False)
        user = CustomUser.objects.get(email="ada@example.com")
        self.assertTrue(user.check_password("tune-lover-2026"))
        self.assertEqual(self.client.session["_auth_user_id"], str(user.id))

    def test_signup_rejects_duplicate_email_case_insensitively(self):
        CustomUser.objects.create_user(username="ada@example.com", email="ada@example.com", password="x")
        response = self.client.post("/signup/", {
            "first_name": "Ada", "email": "ADA@example.com", "password": "tune-lover-2026",
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "already exists", status_code=400)

    def test_signup_rejects_weak_password(self):
        response = self.client.post("/signup/", {"first_name": "Ada", "email": "ada@example.com", "password": "12345678"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CustomUser.objects.filter(email="ada@example.com").exists())

    def test_login(self):
        CustomUser.objects.create_user(username="ada@example.com", email="ada@example.com", password="tune-lover-2026")
        bad = self.client.post("/login/", {"email": "ada@example.com", "password": "wrong"})
        self.assertEqual(bad.status_code, 400)
        good = self.client.post("/login/", {"email": "ADA@example.com", "password": "tune-lover-2026"})
        self.assertRedirects(good, "/dashboard/", fetch_redirect_response=False)


class AboutYouTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="ada@example.com", email="ada@example.com", password="x", first_name="Ada")
        self.client.force_login(self.user)

    def test_saves_profile_and_moves_to_taste(self):
        self.user.latitude, self.user.longitude, self.user.location = 6.52, 3.38, "Lagos, Nigeria"
        self.user.save()
        response = self.client.post("/welcome/about/", {
            "gender": "woman", "preference": "man", "birthdate": "1998-05-02", "location": "Typed Town",
        })
        self.assertRedirects(response, "/taste/build/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual((self.user.gender, self.user.preference), ("woman", "man"))
        self.assertEqual(str(self.user.birthdate), "1998-05-02")
        self.assertEqual(self.user.location, "Lagos, Nigeria")  # GPS city wins; typed text is ignored

    def test_location_is_compulsory(self):
        response = self.client.post("/welcome/about/", {"gender": "woman", "preference": "man", "birthdate": "1998-05-02"})
        self.assertContains(response, "Share your location to continue", status_code=400)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.birthdate)

    def test_page_has_gps_button_and_no_city_box(self):
        page = self.client.get("/welcome/about/").content.decode()
        self.assertIn("Share my location", page)
        self.assertNotIn('name="location"', page)
        self.assertIn('id="about-continue" disabled', page)

    def test_rejects_under_18(self):
        too_young = date.today().replace(year=date.today().year - 17).isoformat()
        response = self.client.post("/welcome/about/", {"gender": "woman", "preference": "man", "birthdate": too_young})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "18 or older", status_code=400)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.birthdate)

    def test_rejects_unknown_choices(self):
        response = self.client.post("/welcome/about/", {"gender": "robot", "preference": "man", "birthdate": "1998-05-02"})
        self.assertEqual(response.status_code, 400)

    def test_needs_login(self):
        self.client.logout()
        response = self.client.get("/welcome/about/")
        self.assertEqual(response.status_code, 302)


class AuthUrlRefreshTests(TestCase):
    def test_get_on_signup_and_login_goes_home(self):
        for url in ("/signup/", "/login/"):
            self.assertRedirects(self.client.get(url), "/", fetch_redirect_response=False)


def stored_photo(user, public_id):
    photo = ProfilePhoto.objects.create(user=user, image=public_id, rank=user.photos.count())
    return ProfilePhoto.objects.get(id=photo.id)


class AboutYouPhotoTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="ada@example.com", email="ada@example.com", password="x")
        self.client.force_login(self.user)

    def _jpeg(self, name="me.jpg"):
        return SimpleUploadedFile(name, b"\xff\xd8\xff\xe0\x00\x10JFIF\x00fake-jpeg-body", content_type="image/jpeg")

    @mock.patch("apps.users.services.add_profile_photo")
    def test_uploads_right_away_and_returns_stored_url(self, add_photo):
        add_photo.side_effect = lambda user, f: stored_photo(user, "stored-1")
        response = self.client.post("/welcome/about/photo/", {"photo": self._jpeg()})
        self.assertEqual(response.status_code, 200)
        self.assertIn("stored-1", response.json()["url"])
        self.assertEqual(self.user.photos.count(), 1)

    @mock.patch("apps.users.services.add_profile_photo")
    def test_changing_photo_replaces_the_previous_one(self, add_photo):
        old = ProfilePhoto.objects.create(user=self.user, image="old", rank=0)
        add_photo.side_effect = lambda user, f: stored_photo(user, "new")
        self.client.post("/welcome/about/photo/", {"photo": self._jpeg(), "replace_id": str(old.id)})
        self.assertEqual([str(p.image) for p in self.user.photos.all()], ["new"])

    @mock.patch("apps.users.services.add_profile_photo")
    def test_cannot_delete_someone_elses_photo(self, add_photo):
        other = CustomUser.objects.create_user(username="bob", password="x")
        theirs = ProfilePhoto.objects.create(user=other, image="theirs", rank=0)
        add_photo.side_effect = lambda user, f: stored_photo(user, "new")
        self.client.post("/welcome/about/photo/", {"photo": self._jpeg(), "replace_id": str(theirs.id)})
        self.assertTrue(ProfilePhoto.objects.filter(id=theirs.id).exists())

    @mock.patch("apps.users.services.add_profile_photo")
    def test_rejects_non_images_and_missing_file(self, add_photo):
        doc = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        bad = self.client.post("/welcome/about/photo/", {"photo": doc})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("isn't a photo", bad.json()["error"])
        self.assertEqual(self.client.post("/welcome/about/photo/").status_code, 400)
        add_photo.assert_not_called()

    @mock.patch("apps.users.services.add_profile_photo", side_effect=RuntimeError("cloudinary down"))
    def test_storage_failure_is_reported(self, _):
        response = self.client.post("/welcome/about/photo/", {"photo": self._jpeg()})
        self.assertEqual(response.status_code, 502)
        self.assertIn("error", response.json())


class LocateTests(TestCase):
    """POST /welcome/locate/ saves the phone's rough position, and names the city when the lookup works."""
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = CustomUser.objects.create_user(username="loc", password="pass1234")
        self.client.force_login(self.user)

    def locate(self, lat="6.5244", lon="3.3792"):
        return self.client.post("/welcome/locate/", {"lat": lat, "lon": lon})

    @mock.patch("apps.users.geocode.requests.get")
    def test_saves_rounded_position_and_city(self, get):
        get.return_value.json.return_value = {"address": {"city": "Lagos", "country": "Nigeria"}}
        get.return_value.raise_for_status.return_value = None
        self.assertEqual(self.locate().json(), {"saved": True, "city": "Lagos, Nigeria"})
        self.user.refresh_from_db()
        self.assertEqual((self.user.latitude, self.user.longitude, self.user.location), (6.52, 3.38, "Lagos, Nigeria"))
        self.assertEqual(get.call_args.kwargs["params"]["lat"], 6.52)  # rounded before leaving the server

    @mock.patch("apps.users.geocode.time.sleep")
    @mock.patch("apps.users.geocode.requests.get")
    def test_city_lookup_down_still_saves_position(self, get, _sleep):
        import requests
        get.side_effect = requests.ConnectionError()
        response = self.locate()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"saved": True, "city": None})
        self.assertEqual(get.call_count, 2)  # retried once
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_position)

    @mock.patch("apps.users.geocode.time.sleep")
    @mock.patch("apps.users.geocode.requests.get")
    def test_retry_recovers_from_one_dropped_request(self, get, _sleep):
        import requests
        ok = mock.Mock()
        ok.json.return_value = {"address": {"town": "Ikeja", "country": "Nigeria"}}
        get.side_effect = [requests.ConnectionError(), ok]
        self.assertEqual(self.locate("6.6", "3.35").json()["city"], "Ikeja, Nigeria")

    @mock.patch("apps.users.geocode.requests.get")
    def test_small_moves_keep_the_city_name(self, get):
        self.user.location, self.user.latitude, self.user.longitude = "Lagos, Nigeria", 6.52, 3.38
        self.user.save()
        self.locate("6.55", "3.40")  # ~4 km away
        get.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual((self.user.location, self.user.latitude), ("Lagos, Nigeria", 6.55))

    def test_bad_coordinates_and_method(self):
        self.assertEqual(self.locate("x", "1").status_code, 400)
        self.assertEqual(self.locate("100", "1").status_code, 400)
        self.assertEqual(self.client.get("/welcome/locate/", {"lat": "1", "lon": "1"}).status_code, 405)

    def test_needs_login(self):
        self.client.logout()
        self.assertEqual(self.locate().status_code, 302)


class ProfileDistanceTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="pd", password="pass1234")
        self.client.force_login(self.user)

    def test_default_is_50_km(self):
        self.assertEqual(self.user.max_distance_km, 50)

    def test_change_distance_and_bad_values_are_ignored(self):
        self.client.post("/profile/update/", {"max_distance_km": "100"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.max_distance_km, 100)
        self.client.post("/profile/update/", {"max_distance_km": "7"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.max_distance_km, 100)
        self.assertContains(self.client.get("/profile/tab/"), "Show me people within")

    def test_profile_cannot_type_a_location(self):
        self.user.location = "Lagos, Nigeria"
        self.user.save()
        self.client.post("/profile/update/", {"location": "Somewhere Else"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.location, "Lagos, Nigeria")


class FillCityLaterTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = CustomUser.objects.create_user(username="fc", password="pass1234", latitude=6.52, longitude=3.38)

    @mock.patch("apps.users.geocode.city_for", return_value="Lagos, Nigeria")
    def test_background_job_fills_city(self, _):
        from .tasks import fill_city_name
        fill_city_name.apply(args=[str(self.user.id)])
        self.user.refresh_from_db()
        self.assertEqual(self.user.location, "Lagos, Nigeria")

    @mock.patch("apps.users.geocode.city_for", return_value=None)
    def test_background_job_retries_then_gives_up_quietly(self, city_for):
        from .tasks import fill_city_name, RETRY_DELAYS
        result = fill_city_name.apply(args=[str(self.user.id)])
        self.assertEqual(city_for.call_count, len(RETRY_DELAYS) + 1)
        self.assertFalse(result.failed())
        self.user.refresh_from_db()
        self.assertEqual(self.user.location, "")

    @mock.patch("apps.users.tasks.fill_city_name.delay")
    @mock.patch("apps.users.geocode.city_for", return_value=None)
    def test_failed_lookup_queues_background_job(self, _, delay):
        self.client.force_login(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post("/welcome/locate/", {"lat": "6.52", "lon": "3.38"})
        delay.assert_called_once_with(str(self.user.id))


from django.core import mail
from django.test import override_settings
from apps.events.testing import EagerEventsMixin
from .models import Block, Report


def music_person(username, gender, preference, **extra):
    from apps.spotify_match.models import TopArtist
    u = CustomUser.objects.create_user(username=username, email=f"{username}@example.com", password="pass12345!",
                                       first_name=username.title(), gender=gender, preference=preference,
                                       has_seen_taste_intro=True, **extra)
    for i, n in enumerate(["Wizkid", "Tems"]):
        TopArtist.objects.create(user=u, external_id=f"{username}{i}", name=n, rank=i + 1)
    return u


@mock.patch("apps.spotify_match.handlers.push_to_user")
@mock.patch("apps.messaging.handlers.get_channel_layer")
class BlockAndReportTests(EagerEventsMixin, TestCase):
    def setUp(self):
        super().setUp()
        from apps.spotify_match import services as match
        self.match = match
        self.ada = music_person("ada", "woman", "man")
        self.bayo = music_person("bayo", "man", "woman")
        match.recompute_scores_for_user(self.ada)

    def make_match(self):
        from apps.messaging.models import Conversation
        with self.run_events():
            self.match.record_swipe(self.ada, self.bayo.id, True)
            self.match.record_swipe(self.bayo, self.ada.id, True)
        a, b = sorted([self.ada, self.bayo], key=lambda u: str(u.id))
        return Conversation.objects.create(user_a=a, user_b=b)

    def test_block_hides_both_ways_and_ends_match_and_chat(self, layer, _):
        layer.return_value.group_send = mock.AsyncMock()
        from apps.messaging.models import Conversation
        self.make_match()
        self.client.force_login(self.ada)
        with self.run_events():
            response = self.client.post(f"/safety/{self.bayo.id}/block/")
        self.assertContains(response, "Bayo is blocked")
        self.assertTrue(Block.objects.filter(blocker=self.ada, blocked=self.bayo).exists())
        self.assertEqual(self.match.get_matches(self.ada), [])
        self.assertEqual(self.match.get_matches(self.bayo), [])
        self.assertFalse(Conversation.objects.exists())
        self.assertEqual(self.match.get_discovery_feed(self.ada), [])
        self.assertEqual(self.match.get_discovery_feed(self.bayo), [])  # the blocked person can't see them either
        with self.assertRaises(ValueError):
            self.match.record_swipe(self.bayo, self.ada.id, True)
        self.assertEqual(self.client.get(f"/user/{self.bayo.id}/").status_code, 302)

    def test_report_saves_and_blocks(self, layer, _):
        self.client.force_login(self.ada)
        response = self.client.post(f"/safety/{self.bayo.id}/report/", {"reason": "scam", "details": "asked for money"})
        self.assertContains(response, "Thanks for telling us")
        report = Report.objects.get()
        self.assertEqual((report.reporter, report.reported, report.reason, report.details), (self.ada, self.bayo, "scam", "asked for money"))
        self.assertTrue(Block.objects.filter(blocker=self.ada, blocked=self.bayo).exists())

    def test_report_needs_a_real_reason_and_post(self, layer, _):
        self.client.force_login(self.ada)
        self.assertEqual(self.client.post(f"/safety/{self.bayo.id}/report/", {"reason": "nope"}).status_code, 400)
        self.assertEqual(self.client.get(f"/safety/{self.bayo.id}/report/").status_code, 405)
        self.assertFalse(Report.objects.exists())

    def test_cannot_block_yourself(self, layer, _):
        self.client.force_login(self.ada)
        self.assertEqual(self.client.post(f"/safety/{self.ada.id}/block/").status_code, 400)

    def test_banned_account_disappears(self, layer, _):
        self.assertEqual(len(self.match.get_discovery_feed(self.ada)), 1)
        self.bayo.is_active = False
        self.bayo.save()
        self.assertEqual(self.match.get_discovery_feed(self.ada), [])

    def test_menu_is_on_discover_card(self, layer, _):
        self.client.force_login(self.ada)
        card = self.client.get("/dashboard/tab/discover/").content.decode()
        self.assertIn(f"openSafety('{self.bayo.id}')", card)
        self.assertIn("Looks under 18", card)


class DeleteAccountTests(TestCase):
    def setUp(self):
        self.user = music_person("del", "woman", "man")
        self.client.force_login(self.user)

    def test_wrong_password_keeps_account(self):
        response = self.client.post("/account/delete/", {"password": "wrong"})
        self.assertContains(response, "That password isn&#x27;t right", status_code=400)
        self.assertTrue(CustomUser.objects.filter(id=self.user.id).exists())

    @mock.patch("cloudinary.uploader.destroy")
    def test_deletes_everything_and_logs_out(self, destroy):
        from apps.spotify_match.models import TopArtist
        response = self.client.post("/account/delete/", {"password": "pass12345!"})
        self.assertRedirects(response, "/?deleted=1", fetch_redirect_response=False)
        self.assertFalse(CustomUser.objects.filter(id=self.user.id).exists())
        self.assertFalse(TopArtist.objects.filter(user_id=self.user.id).exists())
        self.assertContains(self.client.get("/?deleted=1"), "has been deleted")
        self.assertEqual(self.client.get("/dashboard/").status_code, 302)


class LegalPagesTests(TestCase):
    def test_pages_and_signup_link(self):
        self.assertContains(self.client.get("/terms/"), "Terms of Use")
        self.assertContains(self.client.get("/privacy/"), "Nigeria Data Protection Act")
        home = self.client.get("/").content.decode()
        self.assertIn('href="/terms/"', home)
        self.assertIn('href="/privacy/"', home)


@override_settings(EMAIL_ENABLED=False)
class EmailOffTests(TestCase):
    def test_no_forgot_link_or_banner_until_email_is_set_up(self):
        self.assertNotIn("Forgot password?", self.client.get("/").content.decode())
        self.assertContains(self.client.get("/password/reset/"), "being set up")
        user = music_person("off", "woman", "man")
        self.client.force_login(user)
        self.assertNotIn("verify-banner", self.client.get("/dashboard/").content.decode())


@override_settings(EMAIL_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailOnTests(EagerEventsMixin, TestCase):
    def test_password_reset_end_to_end(self):
        import re
        user = music_person("reset", "woman", "man")
        self.assertIn("Forgot password?", self.client.get("/?login=1").content.decode())
        response = self.client.post("/password/reset/", {"email": "RESET@example.com"})
        self.assertRedirects(response, "/password/reset/sent/", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["reset@example.com"])
        link = re.search(r"https?://[^/\s]+(/password/reset/[^\s]+/)", mail.outbox[0].body).group(1)
        page = self.client.get(link, follow=True)
        self.assertContains(page, "Choose a new password")
        done = self.client.post(page.redirect_chain[-1][0], {"new_password1": "brand-new-pass-77", "new_password2": "brand-new-pass-77"})
        self.assertRedirects(done, "/password/reset/complete/", fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.check_password("brand-new-pass-77"))

    def test_unknown_email_looks_the_same_and_sends_nothing(self):
        response = self.client.post("/password/reset/", {"email": "nobody@example.com"})
        self.assertRedirects(response, "/password/reset/sent/", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)

    def test_signup_sends_verification_and_link_verifies(self):
        import re
        self.client.post("/signup/", {"first_name": "Vee", "email": "vee@example.com", "password": "tune-lover-2026"})
        self.assertEqual(len(mail.outbox), 1)
        user = CustomUser.objects.get(email="vee@example.com")
        self.assertFalse(user.email_verified)
        self.assertIn("verify-banner", self.client.get("/messages/").content.decode())
        link = re.search(r"https?://[^/\s]+(/verify-email/[^\s]+/)", mail.outbox[0].body).group(1)
        self.assertContains(self.client.get(link), "Email confirmed")
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertNotIn("verify-banner", self.client.get("/messages/").content.decode())

    def test_bad_link_and_resend_limit(self):
        from django.core.cache import cache
        cache.clear()
        self.assertContains(self.client.get("/verify-email/not-a-real-token/"), "doesn't work", status_code=400)
        user = music_person("again", "woman", "man")
        self.client.force_login(user)
        self.assertContains(self.client.post("/verify-email/resend/"), "Check your inbox")
        self.assertContains(self.client.post("/verify-email/resend/"), "Wait a minute")
        self.assertEqual(len(mail.outbox), 1)


from .models import PushSubscription

FCM = "https://fcm.googleapis.com/fcm/send/abc123"
SUB = {"endpoint": FCM, "keys": {"p256dh": "BPk", "auth": "xyz"}}


@override_settings(PUSH_ENABLED=True, VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
class PushTests(TestCase):
    def setUp(self):
        self.user = music_person("pushy", "woman", "man")
        self.client.force_login(self.user)

    def post_json(self, url, data):
        import json
        return self.client.post(url, json.dumps(data), content_type="application/json")

    def test_subscribe_and_unsubscribe(self):
        self.assertEqual(self.post_json("/push/subscribe/", SUB).status_code, 200)
        self.assertEqual(self.post_json("/push/subscribe/", SUB).status_code, 200)  # same phone twice = one row
        self.assertEqual(PushSubscription.objects.filter(user=self.user).count(), 1)
        self.post_json("/push/unsubscribe/", {"endpoint": FCM})
        self.assertFalse(PushSubscription.objects.exists())

    def test_rejects_non_push_urls(self):
        bad = {"endpoint": "https://evil.example.com/collect", "keys": {"p256dh": "a", "auth": "b"}}
        self.assertEqual(self.post_json("/push/subscribe/", bad).status_code, 400)
        self.assertEqual(self.post_json("/push/subscribe/", {"endpoint": FCM}).status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    @mock.patch("apps.users.tasks.send_push_task.delay")
    def test_notify_only_when_subscribed(self, delay):
        from . import push
        push.notify(self.user.id, "Hi", "there")
        delay.assert_not_called()
        PushSubscription.objects.create(user=self.user, endpoint=FCM, p256dh="a", auth="b")
        push.notify(self.user.id, "Hi", "there", url="/x/")
        delay.assert_called_once_with(str(self.user.id), {"title": "Hi", "body": "there", "url": "/x/", "tag": "/x/"})

    @mock.patch("pywebpush.webpush")
    def test_deliver_removes_dead_subscriptions(self, webpush):
        from pywebpush import WebPushException
        from . import push
        PushSubscription.objects.create(user=self.user, endpoint=FCM, p256dh="a", auth="b")
        PushSubscription.objects.create(user=self.user, endpoint=FCM + "2", p256dh="a", auth="b")
        gone = mock.Mock(status_code=410)
        webpush.side_effect = [WebPushException("gone", response=gone), None]
        push.deliver(str(self.user.id), {"title": "t", "body": "b"})
        self.assertEqual(list(PushSubscription.objects.values_list("endpoint", flat=True)), [FCM + "2"])

    def test_app_files_and_controls(self):
        sw = self.client.get("/sw.js")
        self.assertEqual(sw["Content-Type"], "application/javascript")
        self.assertEqual(sw["Service-Worker-Allowed"], "/")
        self.assertIn("showNotification", sw.content.decode())
        manifest = self.client.get("/manifest.webmanifest").json()
        self.assertEqual((manifest["display"], manifest["start_url"]), ("standalone", "/dashboard/?source=app"))
        self.assertEqual({i["sizes"] for i in manifest["icons"]}, {"192x192", "512x512"})
        page = self.client.get("/dashboard/").content.decode()
        self.assertIn('rel="manifest"', page)
        self.assertIn('name="ml-vapid-key" content="pub"', page)
        self.assertIn("pwa-nudge", page)
        self.assertIn("Notifications &amp; app", self.client.get("/profile/tab/").content.decode())


@override_settings(PUSH_ENABLED=True, VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
@mock.patch("apps.users.push.notify")
@mock.patch("apps.spotify_match.handlers.push_to_user")
@mock.patch("apps.spotify_match.notifications.push_to_user")
@mock.patch("apps.messaging.handlers.get_channel_layer")
class PushOnEventsTests(EagerEventsMixin, TestCase):
    def test_match_and_message_buzz_the_phone(self, layer, _n, _h, notify):
        layer.return_value.group_send = mock.AsyncMock()
        from apps.spotify_match import services as match
        from apps.messaging import services as messaging
        ada, bayo = music_person("ada", "woman", "man"), music_person("bayo", "man", "woman")
        match.recompute_scores_for_user(ada)
        with self.captureOnCommitCallbacks(execute=True):
            match.record_swipe(ada, bayo.id, True)
            match.record_swipe(bayo, ada.id, True)
        titles = {(str(c.args[0]), c.args[1]) for c in notify.call_args_list}
        self.assertEqual(titles, {(str(ada.id), "It's a match! 🎵"), (str(bayo.id), "It's a match! 🎵")})
        notify.reset_mock()
        conversation = messaging.get_or_create_conversation(ada, bayo)
        with self.captureOnCommitCallbacks(execute=True):
            messaging.send_message(conversation, ada, "hey, Essence on repeat?")
        notify.assert_called_once()
        self.assertEqual((str(notify.call_args.args[0]), notify.call_args.args[1], notify.call_args.args[2]),
                         (str(bayo.id), "Ada", "hey, Essence on repeat?"))
        self.assertEqual(notify.call_args.kwargs["url"], f"/messages/?c={conversation.id}")


class NoBioTests(TestCase):
    """Profiles are music-only: no bio anywhere, even if one is still stored from before."""
    def test_bio_not_shown_or_editable(self):
        from apps.spotify_match import services as match
        ada, bayo = music_person("ada", "woman", "man"), music_person("bayo", "man", "woman")
        bayo.bio = "Old bio from before"
        bayo.save()
        match.recompute_scores_for_user(ada)
        self.client.force_login(ada)
        self.assertNotIn("Old bio from before", self.client.get("/dashboard/tab/discover/").content.decode())
        self.client.force_login(bayo)
        self.assertNotIn('name="bio"', self.client.get("/profile/tab/").content.decode())
        self.assertNotIn("Old bio from before", self.client.get("/dashboard/tab/sound/").content.decode())


class PushRetryTests(TestCase):
    @mock.patch("pywebpush.webpush")
    def test_network_failure_retries_only_failed_phones(self, webpush):
        import requests
        from .tasks import send_push_task, PUSH_RETRY_DELAYS
        user = music_person("retry", "woman", "man")
        ok = PushSubscription.objects.create(user=user, endpoint=FCM, p256dh="a", auth="b")
        flaky = PushSubscription.objects.create(user=user, endpoint=FCM + "2", p256dh="a", auth="b")
        calls = []
        def fake(subscription_info, **kw):
            calls.append(subscription_info["endpoint"])
            if subscription_info["endpoint"] == flaky.endpoint and calls.count(flaky.endpoint) < 3:
                raise requests.ReadTimeout("slow network")
        webpush.side_effect = fake
        result = send_push_task.apply(args=[str(user.id), {"title": "t", "body": "b"}])
        self.assertTrue(result.successful())
        self.assertEqual(calls.count(ok.endpoint), 1)      # the working phone isn't buzzed twice
        self.assertEqual(calls.count(flaky.endpoint), 3)   # failed twice, delivered on the 2nd retry
        self.assertTrue(PushSubscription.objects.filter(id=flaky.id).exists())


class LogoutAndInstallTests(TestCase):
    def test_logout_from_profile(self):
        user = music_person("bye", "woman", "man")
        self.client.force_login(user)
        self.assertContains(self.client.get("/profile/tab/"), 'action="/logout/"')
        self.assertEqual(self.client.get("/logout/").status_code, 405)  # only a real button press logs out
        response = self.client.post("/logout/")
        self.assertRedirects(response, "/?login=1", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/dashboard/").status_code, 302)

    def test_home_has_install_button(self):
        page = self.client.get("/").content.decode()
        self.assertIn('id="install-fab"', page)
        self.assertIn("Add to Home Screen", page)


class ProfileValidationTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="pv", password="x", gender="woman", preference="man",
                                                   birthdate=date(1995, 5, 4))
        self.client.force_login(self.user)

    def test_cannot_make_yourself_under_18(self):
        page = self.client.post("/profile/update/", {"birthdate": "2015-01-01"})
        self.assertContains(page, "18 or older")
        self.user.refresh_from_db()
        self.assertEqual(self.user.birthdate, date(1995, 5, 4))

    def test_bad_or_blank_values_keep_what_was_there(self):
        self.client.post("/profile/update/", {"birthdate": "not-a-date", "gender": "robot"})
        self.client.post("/profile/update/", {"birthdate": ""})
        self.user.refresh_from_db()
        self.assertEqual((self.user.birthdate, self.user.gender), (date(1995, 5, 4), "woman"))

    def test_valid_change_saves(self):
        page = self.client.post("/profile/update/", {"birthdate": "1990-02-03", "gender": "nonbinary"})
        self.assertContains(page, "Saved")
        self.user.refresh_from_db()
        self.assertEqual((self.user.birthdate, self.user.gender), (date(1990, 2, 3), "nonbinary"))

    def test_mangled_chat_link_does_not_crash(self):
        self.assertRedirects(self.client.get("/messages/?c=abc"), "/messages/", fetch_redirect_response=False)


class DeployFilesTests(TestCase):
    """The Docker image installs from requirements.txt — it must list every package in the Pipfile
    (pywebpush once went missing there, which would have broken phone notifications on Render)."""
    def test_requirements_cover_pipfile(self):
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        pipfile = (root / "Pipfile").read_text()
        packages = re.search(r"\[packages\](.*?)(\n\[|\Z)", pipfile, re.S).group(1)
        wanted = {re.split(r"\s*=", line)[0].strip().strip('"').lower().replace("_", "-")
                  for line in packages.splitlines() if "=" in line and not line.strip().startswith("#")}
        pinned = {re.split(r"[=<>;\[ ]", line)[0].strip().lower().replace("_", "-")
                  for line in (root / "requirements.txt").read_text().splitlines()
                  if line.strip() and not line.startswith(("#", "-"))}
        self.assertEqual(sorted(wanted - pinned), [], "Run: pipenv requirements > requirements.txt")

    def test_render_blueprint_is_valid_yaml_with_both_services(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[2] / "render.yaml").read_text()
        for needle in ["type: web", "runtime: docker", "key: RUN_WORKER_IN_WEB",
                       "key: FERNET_KEY", "key: VAPID_PRIVATE_KEY", "config.settings.prod"]:
            self.assertIn(needle, text)


class AnalyticsOffTests(TestCase):
    """No PostHog key (local dev / tests): no script, nothing queued."""
    def test_no_script_and_no_queue_without_key(self):
        self.client.post("/signup/", {"first_name": "Ada", "email": "ada@example.com", "password": "tune-lover-2026"})
        self.assertNotIn("ml_track", self.client.session)
        page = self.client.get("/welcome/about/")
        self.assertNotContains(page, "analytics.js")
        self.assertNotContains(page, "mlAnalytics")


@override_settings(POSTHOG_KEY="phc_test", POSTHOG_HOST="https://us.i.posthog.com")
@mock.patch("apps.spotify_match.handlers.push_to_user")
@mock.patch("apps.messaging.handlers.get_channel_layer")
class AnalyticsOnTests(EagerEventsMixin, TestCase):
    def setUp(self):
        super().setUp()
        from apps.spotify_match import services as match
        self.match = match
        self.ada = music_person("ada", "woman", "man")
        self.bayo = music_person("bayo", "man", "woman")
        match.recompute_scores_for_user(self.ada)

    def test_every_kind_of_page_loads_the_script(self, *_):
        for path in ["/", "/terms/", "/privacy/"]:
            self.assertContains(self.client.get(path), "js/analytics.js", msg_prefix=path)
        self.client.force_login(self.ada)
        for path in ["/dashboard/", "/matches/", "/messages/"]:
            page = self.client.get(path)
            self.assertContains(page, "js/analytics.js", msg_prefix=path)
            self.assertContains(page, f'user: "{self.ada.id}"', msg_prefix=path)  # random id only
            self.assertNotContains(page, 'user: "ada@example.com"')

    def test_signup_event_is_sent_once_from_the_next_page(self, *_):
        self.client.post("/signup/", {"first_name": "Ada", "email": "new@example.com", "password": "tune-lover-2026"})
        first = self.client.get("/welcome/about/")
        self.assertContains(first, '"event": "signed_up"')
        second = self.client.get("/welcome/about/")
        self.assertNotContains(second, "signed_up")

    def test_like_and_match_come_back_as_htmx_events(self, layer, _):
        import json
        layer.return_value.group_send = mock.AsyncMock()
        with self.run_events():
            self.match.record_swipe(self.bayo, self.ada.id, True)
        self.client.force_login(self.ada)
        with self.run_events():
            response = self.client.post("/dashboard/swipe/", {"to_user_id": str(self.bayo.id), "liked": "true"})
        events = [e["event"] for e in json.loads(response["HX-Trigger"])["ml:track"]["events"]]
        self.assertEqual(events, ["liked", "matched"])

    def test_message_event_never_carries_the_text(self, layer, _):
        import json
        from apps.messaging.models import Conversation
        layer.return_value.group_send = mock.AsyncMock()
        with self.run_events():
            self.match.record_swipe(self.ada, self.bayo.id, True)
            self.match.record_swipe(self.bayo, self.ada.id, True)
        a, b = sorted([self.ada, self.bayo], key=lambda u: str(u.id))
        convo = Conversation.objects.create(user_a=a, user_b=b)
        self.client.force_login(self.ada)
        with self.run_events():
            response = self.client.post(f"/messages/send/{convo.id}/", {"body": "my secret love note"})
        self.assertEqual(json.loads(response["HX-Trigger"])["ml:track"]["events"], [{"event": "message_sent", "props": {}}])
        self.assertNotIn("secret", response["HX-Trigger"])

    def test_existing_htmx_trigger_is_kept(self, *_):
        import json
        from django.http import HttpResponse
        from . import analytics
        response = HttpResponse()
        response["HX-Trigger"] = "notificationsChanged"
        analytics.track_response(response, "liked")
        triggers = json.loads(response["HX-Trigger"])
        self.assertIn("notificationsChanged", triggers)
        self.assertEqual(triggers["ml:track"]["events"][0]["event"], "liked")


class FriendlyErrorTests(TestCase):
    def setUp(self):
        self.user = music_person("err", "woman", "man")

    @override_settings(DEBUG=False)
    def test_unknown_page_shows_friendly_404(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "We can't find that page", status_code=404)
        self.assertContains(response, "Back to Music Luvers", status_code=404)

    def test_500_page_renders_without_request_context(self):
        from django.template import loader
        html = loader.get_template("500.html").render()
        self.assertIn("Something went wrong on our side", html)

    def test_expired_login_during_a_tap_becomes_a_full_page_change(self):
        response = self.client.get("/dashboard/tab/discover/", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["HX-Redirect"].startswith("/"))
        self.assertEqual(response.content, b"")

    def test_normal_redirects_are_untouched(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("HX-Redirect", response)

    def test_expired_form_shows_friendly_page_and_short_message_for_taps(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        page = strict.post("/profile/update/")
        self.assertContains(page, "This page expired", status_code=403)
        tap = strict.post("/profile/update/", HTTP_HX_REQUEST="true")
        self.assertEqual(tap.status_code, 403)
        self.assertEqual(tap.content.decode(), "This page expired. Refresh it and try again.")

    def test_profile_tab_rejects_non_photo_with_a_message(self):
        self.client.force_login(self.user)
        bad = SimpleUploadedFile("notes.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post("/profile/update/", {"photo": bad}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "a photo. Pick a JPG or PNG.")
        self.assertEqual(self.user.photos.count(), 0)

    def test_profile_tab_storage_failure_shows_a_message(self):
        self.client.force_login(self.user)
        with mock.patch("apps.users.services.add_profile_photo", side_effect=RuntimeError("cloudinary down")):
            response = self.client.post("/profile/update/", {
                "photo": SimpleUploadedFile("me.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "upload that photo. Try another one.")

    def test_every_app_page_loads_the_error_popup(self):
        self.client.force_login(self.user)
        for path in ["/dashboard/", "/matches/", "/messages/"]:
            self.assertContains(self.client.get(path), "js/errors.js", msg_prefix=path)


class PhotosOnlyTests(TestCase):
    """Only still photos: checked from the file's real contents, not its name or claimed type."""
    JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
    PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 64
    APNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 17 + b"\x00\x00\x00\x08acTL" + b"\x00" * 32
    WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
    WEBP_ANIMATED = b"RIFF\x24\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00\x02" + b"\x00" * 64
    GIF = b"GIF89a" + b"\x00" * 64
    MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 64
    HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
    WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 64
    PDF = b"%PDF-1.4" + b"\x00" * 64

    def form(self, data, name="me.jpg", content_type="image/jpeg"):
        from .forms import PhotoForm
        return PhotoForm({}, {"photo": SimpleUploadedFile(name, data, content_type=content_type)})

    def test_still_photos_are_accepted(self):
        for label, data in [("jpg", self.JPG), ("png", self.PNG), ("webp", self.WEBP)]:
            self.assertTrue(self.form(data).is_valid(), label)

    def test_videos_are_refused_even_when_renamed_to_jpg(self):
        for label, data in [("mp4", self.MP4), ("mov", self.MOV), ("webm", self.WEBM)]:
            form = self.form(data, name="holiday.jpg", content_type="image/jpeg")
            self.assertFalse(form.is_valid(), label)
            self.assertIn("Videos can't be used", form.errors["photo"][0], label)

    def test_video_type_is_refused_straight_away(self):
        form = self.form(self.JPG, name="clip.mp4", content_type="video/mp4")
        self.assertIn("Videos can't be used", form.errors["photo"][0])

    def test_moving_pictures_are_refused(self):
        self.assertIn("Moving GIFs", self.form(self.GIF, content_type="image/gif").errors["photo"][0])
        self.assertIn("Videos can't be used", self.form(self.WEBP_ANIMATED).errors["photo"][0])
        self.assertIn("Videos can't be used", self.form(self.APNG).errors["photo"][0])

    def test_heic_gets_a_helpful_message(self):
        self.assertIn("HEIC", self.form(self.HEIC, name="IMG_1.HEIC", content_type="image/heic").errors["photo"][0])

    def test_other_files_are_refused(self):
        self.assertIn("isn't a photo", self.form(self.PDF, content_type="image/jpeg").errors["photo"][0])

    def test_signup_photo_upload_refuses_a_video(self):
        user = CustomUser.objects.create_user(username="v@example.com", email="v@example.com", password="x")
        self.client.force_login(user)
        with mock.patch("apps.users.services.add_profile_photo") as add:
            response = self.client.post("/welcome/about/photo/", {
                "photo": SimpleUploadedFile("clip.jpg", self.MP4, content_type="image/jpeg")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Videos can't be used", response.json()["error"])
        add.assert_not_called()

    def test_pickers_only_offer_photos(self):
        user = music_person("pick", "woman", "man")
        self.client.force_login(user)
        for path in ["/welcome/about/", "/profile/tab/"]:
            page = self.client.get(path)
            self.assertContains(page, 'accept="image/jpeg,image/png,image/webp"', msg_prefix=path)
