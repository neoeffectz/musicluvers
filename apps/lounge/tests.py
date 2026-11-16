from datetime import date, datetime, timedelta
from unittest import mock

from django.test import TestCase, override_settings

from apps.spotify_match.models import Match, Swipe, TopArtist
from apps.users.models import CustomUser

from . import schedule, services
from .models import Round, SongRequest

FRIDAY_5PM = datetime(2026, 11, 20, 17, 0)   # a Friday
FRIDAY = FRIDAY_5PM.date()


def person(username, gender="woman", preference="man"):
    u = CustomUser.objects.create_user(username=username, password="x", first_name=username.title(), gender=gender,
                                       preference=preference, birthdate=date(1998, 5, 1), latitude=6.52, longitude=3.38)
    TopArtist.objects.create(user=u, external_id=f"{username}-a", name="Wizkid", rank=1)
    return u


def fake_track(url, params=None):
    track_id = url.rsplit("/", 1)[-1]
    return {"id": track_id, "name": f"Song {track_id}", "artists": [{"name": "Artist"}],
            "album": {"images": [{"url": "http://img/x.jpg"}]}, "duration_ms": 180_000}


class ScheduleTests(TestCase):
    def test_open_fridays_5_to_6_lagos_time(self):
        self.assertTrue(schedule.is_open(FRIDAY_5PM))
        self.assertTrue(schedule.is_open(FRIDAY_5PM + timedelta(minutes=59)))
        self.assertFalse(schedule.is_open(FRIDAY_5PM - timedelta(minutes=1)))
        self.assertFalse(schedule.is_open(FRIDAY_5PM + timedelta(hours=1)))
        self.assertFalse(schedule.is_open(FRIDAY_5PM + timedelta(days=1)))   # Saturday 5pm

    def test_next_opening(self):
        self.assertEqual(schedule.next_opening(datetime(2026, 11, 18, 9, 0)), FRIDAY_5PM)          # Wednesday
        self.assertEqual(schedule.next_opening(FRIDAY_5PM + timedelta(hours=3)), FRIDAY_5PM + timedelta(days=7))

    @override_settings(LOUNGE_ALWAYS_OPEN=True)
    def test_test_switch_opens_it_any_day(self):
        self.assertTrue(schedule.is_open(datetime(2026, 11, 17, 10, 0)))


@mock.patch("apps.lounge.services.broadcast")
@mock.patch("apps.spotify_match.artist_search._get", side_effect=fake_track)
class RoundsTests(TestCase):
    def setUp(self):
        self.people = [person(f"p{i}") for i in range(14)]

    def request_all(self, at, people=None):
        for i, p in enumerate(people or self.people):
            services.request_song(p, f"t{i}", at=at)

    def test_one_request_per_person_per_round_and_it_can_change(self, *_):
        services.request_song(self.people[0], "t1", at=FRIDAY_5PM)
        services.request_song(self.people[0], "t2", at=FRIDAY_5PM + timedelta(minutes=1))
        self.assertEqual(SongRequest.objects.get().track_id, "t2")
        self.assertEqual(SongRequest.objects.get().duration_ms, 180_000)

    def test_closed_lounge_refuses_requests(self, *_):
        with self.assertRaisesMessage(ValueError, "closed"):
            services.request_song(self.people[0], "t1", at=FRIDAY_5PM - timedelta(hours=1))

    def test_after_10_minutes_10_random_requests_become_the_playlist(self, *_):
        self.request_all(FRIDAY_5PM + timedelta(minutes=2))
        self.assertIsNone(services.advance(FRIDAY_5PM + timedelta(minutes=9)))      # still collecting
        drawn = services.advance(FRIDAY_5PM + timedelta(minutes=10))
        picks = list(drawn.requests.filter(position__isnull=False).order_by("position"))
        self.assertEqual(len(picks), 10)
        self.assertEqual(sorted(p.position for p in picks), list(range(10)))
        self.assertEqual(drawn.requests.filter(position__isnull=True).count(), 4)   # 4 not picked
        self.assertTrue(Round.objects.filter(session_date=FRIDAY, number=2, started_at__isnull=True).exists())

    def test_draw_is_random(self, *_):
        orders = set()
        for n in range(6):
            Round.objects.all().delete()
            self.request_all(FRIDAY_5PM)
            drawn = services.advance(FRIDAY_5PM + timedelta(minutes=10))
            orders.add(tuple(drawn.requests.filter(position__isnull=False).order_by("position").values_list("track_id", flat=True)))
        self.assertGreater(len(orders), 1)

    def test_now_playing_moves_with_the_clock_and_shows_who_requested(self, *_):
        self.request_all(FRIDAY_5PM, self.people[:3])
        start = FRIDAY_5PM + timedelta(minutes=10)
        services.advance(start)
        first = services.stage(self.people[5], at=start + timedelta(seconds=30))
        self.assertEqual(len(first["up_next"]), 2)
        self.assertEqual(first["seconds_left"], 150)
        second = services.stage(self.people[5], at=start + timedelta(minutes=3, seconds=5))
        self.assertEqual(second["played"], [first["now_playing"]])
        self.assertNotEqual(second["now_playing"], first["now_playing"])
        self.assertIn(second["now_playing"].user, self.people[:3])

    def test_next_round_is_drawn_when_the_playlist_ends(self, *_):
        self.request_all(FRIDAY_5PM, self.people[:2])
        start = FRIDAY_5PM + timedelta(minutes=10)
        services.advance(start)                                                  # 2 songs = 6 minutes
        services.request_song(self.people[9], "t99", at=start + timedelta(minutes=1))
        self.assertIsNone(services.advance(start + timedelta(minutes=5)))         # round 1 still playing
        round2 = services.advance(start + timedelta(minutes=12))                 # ended, and round 2 had 10 min
        self.assertEqual(round2.number, 2)
        self.assertEqual(services.stage(self.people[0], at=start + timedelta(minutes=12))["now_playing"].track_id, "t99")

    def test_long_songs_are_capped_at_5_minutes(self, *_):
        req = SongRequest(duration_ms=9 * 60_000)
        self.assertEqual(services.play_ms(req), 300_000)


class PresenceTests(TestCase):
    def test_people_here_counts_recent_check_ins(self):
        a, b = person("a"), person("b")
        services.check_in(a, at=FRIDAY_5PM)
        services.check_in(b, at=FRIDAY_5PM - timedelta(minutes=5))   # left a while ago
        self.assertEqual(services.people_here(at=FRIDAY_5PM + timedelta(seconds=30)), 1)
        services.check_out(a)
        self.assertEqual(services.people_here(at=FRIDAY_5PM + timedelta(seconds=30)), 0)


@override_settings(LOUNGE_ALWAYS_OPEN=True)
@mock.patch("apps.lounge.services.broadcast")
@mock.patch("apps.spotify_match.artist_search._get", side_effect=fake_track)
class LoungePageTests(TestCase):
    def setUp(self):
        self.ada = person("ada", "woman", "man")
        self.ben = person("ben", "man", "woman")
        self.eve = person("eve", "woman", "man")     # wants men too: Ada and Eve can't match
        self.client.force_login(self.ada)

    def test_page_shows_people_and_request_box(self, *_):
        page = self.client.get("/lounge/")
        self.assertContains(page, "The Lounge")
        self.assertContains(page, "here now")
        self.assertContains(page, "Search a song to request")

    def test_page_hands_out_the_security_cookie(self, *_):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.ada)
        strict.get("/lounge/")
        token = strict.cookies["csrftoken"].value
        response = strict.post("/lounge/request/", {"track_id": "abc123"}, HTTP_X_CSRFTOKEN=token, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)

    def test_request_from_the_page(self, *_):
        response = self.client.post("/lounge/request/", {"track_id": "abc123"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Song abc123")
        self.assertEqual(SongRequest.objects.get().user, self.ada)
        self.assertEqual(self.client.post("/lounge/request/", {"track_id": "<bad>"}).status_code, 400)

    def test_tap_requester_and_like_them(self, *_):
        card = self.client.get(f"/lounge/person/{self.ben.id}/")
        self.assertContains(card, "Ben")
        self.assertContains(card, "Like")
        self.client.post(f"/lounge/person/{self.ben.id}/like/")
        self.assertTrue(Swipe.objects.filter(from_user=self.ada, to_user=self.ben, liked=True).exists())
        self.client.force_login(self.ben)
        result = self.client.post(f"/lounge/person/{self.ada.id}/like/")
        self.assertContains(result, "a match! Say hi")
        self.assertTrue(Match.objects.exists())

    def test_no_like_button_when_preferences_dont_fit(self, *_):
        card = self.client.get(f"/lounge/person/{self.eve.id}/")
        self.assertNotContains(card, "fa-heart\"></i> Like")
        self.assertEqual(self.client.post(f"/lounge/person/{self.eve.id}/like/").status_code, 400)

    def test_top_bar_shows_live(self, *_):
        self.assertContains(self.client.get("/matches/"), "m-lounge is-live")


class ClosedLoungeTests(TestCase):
    def test_closed_page_says_when_it_opens(self):
        self.client.force_login(person("ada"))
        with mock.patch("apps.lounge.schedule.now", return_value=datetime(2026, 11, 18, 9, 0)):
            page = self.client.get("/lounge/")
        self.assertContains(page, "Friday Lounge, 5pm to 6pm")
        self.assertContains(page, "Friday")
        self.assertNotContains(page, "Search a song to request")


class FridayNotificationTests(TestCase):
    @override_settings(PUSH_ENABLED=True)
    def test_announcement_goes_to_people_with_notifications_on(self):
        from apps.users.models import PushSubscription
        from .tasks import announce_lounge_open
        ada = person("ada")
        PushSubscription.objects.create(user=ada, endpoint="https://push.example/1", p256dh="k", auth="a")
        with mock.patch("apps.users.tasks.send_push_task.delay") as send:
            announce_lounge_open()
        self.assertEqual(send.call_args.args[0], str(ada.id))
        self.assertEqual(send.call_args.args[1]["url"], "/lounge/")
