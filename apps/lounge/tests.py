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
    def test_opens_friday_5pm_and_closes_at_515_without_a_full_playlist(self):
        self.assertTrue(schedule.is_open(FRIDAY_5PM))
        self.assertTrue(schedule.is_open(FRIDAY_5PM + timedelta(minutes=14)))
        self.assertFalse(schedule.is_open(FRIDAY_5PM - timedelta(minutes=1)))
        self.assertFalse(schedule.is_open(FRIDAY_5PM + timedelta(minutes=15)))   # nobody filled it: closed
        self.assertFalse(schedule.is_open(FRIDAY_5PM + timedelta(days=1)))   # Saturday 5pm

    def test_next_opening(self):
        self.assertEqual(schedule.next_opening(datetime(2026, 11, 18, 9, 0)), FRIDAY_5PM)          # Wednesday
        self.assertEqual(schedule.next_opening(FRIDAY_5PM + timedelta(hours=3)), FRIDAY_5PM + timedelta(days=7))

    @override_settings(LOUNGE_ALWAYS_OPEN=True)
    def test_test_switch_opens_it_any_day(self):
        self.assertTrue(schedule.is_open(datetime(2026, 11, 17, 10, 0)))


@mock.patch("apps.lounge.services.broadcast")
@mock.patch("apps.spotify_match.artist_search._get", side_effect=fake_track)
@override_settings(YOUTUBE_API_KEY="")  # never call the real YouTube from tests
class PoolTests(TestCase):
    """Requests fill a pool of 5; the moment it's full, those 5 play for everyone in a random order."""
    def setUp(self):
        self.people = [person(f"p{i}") for i in range(12)]

    def fill(self, at, people, tracks=None):
        for i, p in enumerate(people):
            services.request_song(p, (tracks or [f"t{n}" for n in range(len(people))])[i], at=at)

    def test_one_request_per_person_and_it_can_change(self, *_):
        services.request_song(self.people[0], "t1", at=FRIDAY_5PM)
        services.request_song(self.people[0], "t2", at=FRIDAY_5PM + timedelta(minutes=1))
        self.assertEqual(SongRequest.objects.filter(round__is_warmup=False).get().track_id, "t2")

    def test_closed_lounge_refuses_requests(self, *_):
        with self.assertRaisesMessage(ValueError, "closed"):
            services.request_song(self.people[0], "t1", at=FRIDAY_5PM - timedelta(hours=1))

    def test_four_songs_wait_the_fifth_starts_the_playlist(self, *_):
        at = FRIDAY_5PM + timedelta(minutes=2)
        self.fill(at, self.people[:4])
        self.assertFalse(Round.objects.filter(is_warmup=False, started_at__isnull=False).exists())
        services.request_song(self.people[4], "t4", at=at)
        pool = Round.objects.get(is_warmup=False, number=1)
        self.assertEqual(pool.started_at, at)
        self.assertEqual(sorted(s.position for s in services.playlist(pool)), [0, 1, 2, 3, 4])

    def test_same_song_from_two_people_counts_once(self, *_):
        at = FRIDAY_5PM
        self.fill(at, self.people[:5], tracks=["same", "same", "a", "b", "c"])
        self.assertFalse(Round.objects.filter(is_warmup=False, started_at__isnull=False).exists())   # 4 songs
        services.request_song(self.people[5], "d", at=at)
        pool = Round.objects.get(is_warmup=False, number=1)
        same = next(s for s in services.playlist(pool) if s.track_id == "same")
        self.assertEqual(set(same.requesters), set(self.people[:2]))

    def test_order_is_random(self, *_):
        orders = set()
        for _ in range(6):
            Round.objects.all().delete()
            self.fill(FRIDAY_5PM, self.people[:5])
            pool = Round.objects.get(is_warmup=False, number=1)
            orders.add(tuple(s.track_id for s in services.playlist(pool)))
        self.assertGreater(len(orders), 1)

    def test_a_full_pool_cuts_in_on_filler_music(self, *_):
        filler = services.build_filler(FRIDAY, FRIDAY_5PM)
        SongRequest.objects.create(round=filler, position=0, track_id="f", track_name="Filler", duration_ms=200_000)
        self.assertTrue(services.playing_round(FRIDAY_5PM + timedelta(seconds=30))[0].is_warmup)
        self.fill(FRIDAY_5PM + timedelta(seconds=40), self.people[:5])
        playing, _ = services.playing_round(FRIDAY_5PM + timedelta(seconds=41))
        self.assertFalse(playing.is_warmup)

    def test_now_playing_moves_with_the_clock_and_late_joiners_start_mid_song(self, *_):
        self.fill(FRIDAY_5PM, self.people[:5])
        first = services.stage(self.people[7], at=FRIDAY_5PM + timedelta(seconds=72))
        self.assertEqual(first["elapsed"], 72.0)
        self.assertEqual(first["seconds_left"], 108)
        self.assertEqual(len(first["up_next"]), 4)
        later = services.stage(self.people[7], at=FRIDAY_5PM + timedelta(minutes=3, seconds=5))
        self.assertNotEqual(later["now_playing"], first["now_playing"])
        self.assertEqual(later["played"][0].track_id, first["now_playing"].track_id)   # tappable afterwards

    @override_settings(YOUTUBE_API_KEY="k")
    def test_a_song_youtube_cant_play_still_plays_through_spotify(self, *_):
        found = lambda songs: {r.id: ("", 0) if r.track_id == "t0" else (f"v{r.track_id}", 180_000) for r in songs}
        with mock.patch("apps.lounge.youtube.find_videos", side_effect=found):
            self.fill(FRIDAY_5PM, self.people[:5])
        songs = services.playlist(Round.objects.get(is_warmup=False, number=1))
        self.assertEqual(len(songs), 5)
        self.assertEqual(sum(1 for s in songs if not s.youtube_id), 1)

    def test_requests_close_once_the_playlist_starts(self, *_):
        self.fill(FRIDAY_5PM, self.people[:5])
        with self.assertRaisesMessage(ValueError, "already playing"):
            services.request_song(self.people[6], "t9", at=FRIDAY_5PM + timedelta(minutes=1))

    def test_full_at_514_plays_all_5_songs_then_the_lounge_closes(self, *_):
        start = FRIDAY_5PM + timedelta(minutes=14)
        self.fill(start, self.people[:5])                                     # 5 x 3 min
        self.assertTrue(schedule.is_open(start + timedelta(minutes=14)))      # past 5:15, songs still on
        self.assertFalse(schedule.is_open(start + timedelta(minutes=15)))     # last song over: closed

    def test_same_song_under_two_spotify_listings_counts_once(self, *_):
        def listing(url, params=None):   # "single" and "album" ids, same song
            track = fake_track(url)
            track["name"] = "Essence" if url.endswith(("single", "album")) else track["name"]
            return track
        with mock.patch("apps.spotify_match.artist_search._get", side_effect=listing):
            services.request_song(self.people[0], "single", at=FRIDAY_5PM)
            services.request_song(self.people[1], "album", at=FRIDAY_5PM)
        self.assertEqual(services.pool_count(services.open_round(FRIDAY_5PM)), 1)

    def test_long_songs_are_capped_at_5_minutes(self, *_):
        self.assertEqual(services.play_ms(SongRequest(duration_ms=9 * 60_000)), 300_000)


@override_settings(YOUTUBE_API_KEY="")  # never call the real YouTube from tests
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
@override_settings(YOUTUBE_API_KEY="")  # never call the real YouTube from tests
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

    def test_card_has_block_or_report_right_there(self, *_):
        card = self.client.get(f"/lounge/person/{self.ben.id}/")
        self.assertContains(card, "Block or report Ben")
        self.assertContains(card, f'id="safety-{self.ben.id}"')
        self.assertContains(card, 'data-next="/lounge/"')

    def test_blocked_people_cant_be_tapped_in_the_lounge(self, *_):
        from apps.users.models import Block
        Block.objects.create(blocker=self.ada, blocked=self.ben)
        self.assertEqual(self.client.get(f"/lounge/person/{self.ben.id}/").status_code, 404)
        self.assertIn(self.ben.id, services.stage(self.ada)["blocked"])

    def test_top_bar_shows_live(self, *_):
        self.assertContains(self.client.get("/matches/"), "m-lounge is-live")


@override_settings(YOUTUBE_API_KEY="")  # never call the real YouTube from tests
class ClosedLoungeTests(TestCase):
    def test_closed_page_says_when_it_opens(self):
        self.client.force_login(person("ada"))
        with mock.patch("apps.lounge.schedule.now", return_value=datetime(2026, 11, 18, 9, 0)):
            page = self.client.get("/lounge/")
        self.assertContains(page, "Friday Lounge, from 5pm")
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


def yt_response(payload):
    r = mock.Mock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


@override_settings(YOUTUBE_API_KEY="k")
class YouTubeTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def fake_get(self, url, params=None, timeout=None):
        if "search" in url:
            return yt_response({"items": [{"id": {"videoId": "vid123"}}]})
        return yt_response({"items": [{"id": "vid123", "snippet": {"channelTitle": "Wizkid - Topic"},
                                       "contentDetails": {"duration": "PT3M25S"}, "status": {"embeddable": True}}]})

    def test_finds_the_video_and_its_real_length(self):
        from . import youtube
        with mock.patch("apps.lounge.youtube.requests.get", side_effect=self.fake_get) as get:
            self.assertEqual(youtube.find_video("sp1", "Essence", "Wizkid"), ("vid123", 205_000))
            self.assertEqual(youtube.find_video("sp1", "Essence", "Wizkid"), ("vid123", 205_000))
        self.assertEqual(get.call_count, 2)   # second time remembered: no extra quota used

    def test_picks_the_video_whose_length_matches_the_song(self):
        from . import youtube
        def many(url, params=None, timeout=None):
            if "search" in url:
                return yt_response({"items": [{"id": {"videoId": v}} for v in ("loop", "skit", "track")]})
            return yt_response({"items": [
                {"id": "loop", "snippet": {"channelTitle": "Fan"}, "contentDetails": {"duration": "PT19M18S"}, "status": {"embeddable": True}},
                {"id": "skit", "snippet": {"channelTitle": "AyraStarrVEVO"}, "contentDetails": {"duration": "PT4M40S"}, "status": {"embeddable": True}},
                {"id": "track", "snippet": {"channelTitle": "AyraStarrVEVO", "title": "Ayra Starr - Rush (Visualizer)"}, "contentDetails": {"duration": "PT3M5S"}, "status": {"embeddable": True}},
            ]})
        with mock.patch("apps.lounge.youtube.requests.get", side_effect=many):
            self.assertEqual(youtube.find_video("rush", "Rush", "Ayra Starr", duration_ms=185_000), ("track", 185_000))

    def test_official_audio_beats_the_music_video(self):
        from . import youtube
        def both(url, params=None, timeout=None):
            if "search" in url:
                self.assertTrue(params["q"].endswith(" official audio"))
                return yt_response({"items": [{"id": {"videoId": v}} for v in ("mv", "art")]})
            return yt_response({"items": [
                {"id": "mv", "snippet": {"channelTitle": "WizkidVEVO", "title": "Wizkid - Essence (Official Video)"},
                 "contentDetails": {"duration": "PT4M6S"}, "status": {"embeddable": True}},
                {"id": "art", "snippet": {"channelTitle": "WizkidVEVO", "title": "Wizkid - Essence (Official Audio)"},
                 "contentDetails": {"duration": "PT4M8S"}, "status": {"embeddable": True}},
            ]})
        with mock.patch("apps.lounge.youtube.requests.get", side_effect=both):
            self.assertEqual(youtube.find_video("ess", "Essence", "Wizkid", duration_ms=248_000)[0], "art")

    def test_auto_generated_topic_tracks_are_a_last_resort(self):
        from . import youtube
        def topic(url, params=None, timeout=None):
            if "search" in url:
                return yt_response({"items": [{"id": {"videoId": v}} for v in ("auto", "mv")]})
            return yt_response({"items": [
                {"id": "auto", "snippet": {"channelTitle": "SZA - Topic", "title": "Snooze"},
                 "contentDetails": {"duration": "PT3M21S"}, "status": {"embeddable": True}},
                {"id": "mv", "snippet": {"channelTitle": "SZAVEVO", "title": "SZA - Snooze (Official Video)"},
                 "contentDetails": {"duration": "PT3M25S"}, "status": {"embeddable": True}},
            ]})
        with mock.patch("apps.lounge.youtube.requests.get", side_effect=topic):
            self.assertEqual(youtube.find_video("snz", "Snooze", "SZA", duration_ms=201_000)[0], "mv")

    def test_live_performances_are_never_picked(self):
        from . import youtube
        def live(url, params=None, timeout=None):
            if "search" in url:
                return yt_response({"items": [{"id": {"videoId": v}} for v in ("stage", "song")]})
            return yt_response({"items": [
                {"id": "stage", "snippet": {"channelTitle": "Burna Boy", "title": "Burna Boy - Ye (Live Performance)"},
                 "contentDetails": {"duration": "PT3M50S"}, "status": {"embeddable": True}},
                {"id": "song", "snippet": {"channelTitle": "Music Hub", "title": "Burna Boy - Ye"},
                 "contentDetails": {"duration": "PT3M56S"}, "status": {"embeddable": True}},
            ]})
        with mock.patch("apps.lounge.youtube.requests.get", side_effect=live):
            self.assertEqual(youtube.find_video("ye", "Ye", "Burna Boy", duration_ms=231_000)[0], "song")

    def test_video_that_cant_be_embedded_is_skipped(self):
        from . import youtube
        def blocked(url, params=None, timeout=None):
            if "search" in url:
                return yt_response({"items": [{"id": {"videoId": "x"}}]})
            return yt_response({"items": [{"id": "x", "contentDetails": {"duration": "PT3M"}, "status": {"embeddable": False}}]})
        with mock.patch("apps.lounge.youtube.requests.get", side_effect=blocked):
            self.assertEqual(youtube.find_video("sp2", "Song", "Artist"), ("", 0))

    def test_youtube_down_doesnt_break_the_draw(self):
        import requests as http
        from . import youtube
        with mock.patch("apps.lounge.youtube.requests.get", side_effect=http.exceptions.ConnectionError):
            self.assertEqual(youtube.find_video("sp3", "Song", "Artist"), ("", 0))

    @mock.patch("apps.lounge.services.broadcast")
    @mock.patch("apps.spotify_match.artist_search._get", side_effect=fake_track)
    def test_drawn_songs_get_their_video_and_late_joiners_start_mid_song(self, *_):
        people = [person(f"y{i}") for i in range(6)]
        with mock.patch("apps.lounge.youtube.requests.get", side_effect=self.fake_get):
            for i, p in enumerate(people[:5]):
                services.request_song(p, f"t{i}", at=FRIDAY_5PM)          # 5th request starts the playlist
        songs = SongRequest.objects.filter(round__is_warmup=False)
        self.assertEqual({(s.youtube_id, s.duration_ms) for s in songs}, {("vid123", 205_000)})
        later = services.stage(people[5], at=FRIDAY_5PM + timedelta(minutes=1, seconds=12))
        self.assertEqual(later["elapsed"], 72.0)          # 1:12 into the song for someone joining now
        self.assertEqual(later["seconds_left"], 133)


class YouTubeKeyNotLoggedTests(TestCase):
    @override_settings(YOUTUBE_API_KEY="secret-key-123")
    def test_a_failed_lookup_never_writes_the_key_to_the_logs(self):
        import requests
        from apps.lounge import youtube
        err = requests.exceptions.HTTPError("429 Too Many Requests for url: https://x/search?q=a&key=secret-key-123")
        with mock.patch("apps.lounge.youtube.requests.get", side_effect=err), \
                self.assertLogs("apps.lounge.youtube", level="WARNING") as logs:
            self.assertEqual(youtube.find_video("t1", "Song", "Artist"), ("", 0))
        self.assertNotIn("secret-key-123", "\n".join(logs.output))


@override_settings(LOUNGE_ALWAYS_OPEN=True)
@override_settings(YOUTUBE_API_KEY="")  # never call the real YouTube from tests
class PlayerOnPageTests(TestCase):
    def test_page_has_the_shared_player(self):
        self.client.force_login(person("ada"))
        page = self.client.get("/lounge/")
        self.assertContains(page, "Join the music")
        self.assertContains(page, "youtube.com/iframe_api")


@override_settings(YOUTUBE_API_KEY="")
@mock.patch("apps.lounge.services.broadcast")
@mock.patch("apps.spotify_match.artist_search._get", side_effect=fake_track)
class FillerTests(TestCase):
    """Whenever no pool is playing: random songs from people's profiles, with no names on them."""
    def answer(self, user, prompt, track_id, name):
        from apps.spotify_match.models import ProfilePrompt
        ProfilePrompt.objects.create(user=user, prompt=prompt, track_id=track_id, track_name=name, artist_name="X")

    def setUp(self):
        from django.utils import timezone
        self.people = [person(f"w{i}") for i in range(6)]
        CustomUser.objects.update(last_login=timezone.now())
        for i, p in enumerate(self.people):
            self.answer(p, "anthem", f"a{i}", f"Anthem {i}")
            self.answer(p, "dance", f"d{i}", f"Dance {i}")

    def test_music_plays_from_5pm_anonymous_profile_songs(self, *_):
        from .models import TrackVideo
        for i in range(6):
            TrackVideo.objects.create(track_id=f"a{i}", youtube_id=f"yt{i}", duration_ms=150_000)
        page = services.stage(self.people[0], at=FRIDAY_5PM + timedelta(seconds=30))
        self.assertTrue(page["warmup"])
        self.assertEqual(page["now_playing"].requesters, [])                 # no name on filler music
        self.assertTrue(page["now_playing"].youtube_id.startswith("yt"))     # saved matches first: free
        filler = Round.objects.get(is_warmup=True)
        self.assertEqual(filler.requests.count(), 4)                          # 4 x 150 s fills 10 min
        self.assertFalse(filler.requests.exclude(user=None).exists())

    def test_prepared_at_430_and_finds_a_dozen_songs_ahead(self, *_):
        with mock.patch("apps.lounge.youtube.find_videos", return_value={}) as find:
            services.prepare_warmup(FRIDAY)
        self.assertEqual(len(find.call_args_list[0].args[0]), 6)             # every profile song (one each)
        self.assertEqual(services.playing_round(FRIDAY_5PM - timedelta(minutes=20)), (None, []))
        self.assertTrue(services.playing_round(FRIDAY_5PM + timedelta(seconds=5))[0].is_warmup)

    def test_music_keeps_going_between_pools_without_repeats(self, *_):
        first = services.stage(self.people[0], at=FRIDAY_5PM)
        filler1 = {s.track_id for s in Round.objects.get(is_warmup=True).requests.all()}
        later = services.stage(self.people[0], at=FRIDAY_5PM + timedelta(minutes=14))   # first batch over
        self.assertTrue(later["warmup"])
        filler2 = {s.track_id for s in Round.objects.filter(is_warmup=True).order_by("-number")[0].requests.all()}
        self.assertFalse(filler1 & filler2)

    def test_filler_only_plays_songs_found_on_youtube(self, *_):
        from .models import TrackVideo
        TrackVideo.objects.create(track_id="a0", youtube_id="yt0", duration_ms=200_000)
        TrackVideo.objects.create(track_id="d3", youtube_id="yt3", duration_ms=200_000)
        # YouTube's daily limit is used up: nothing new can be found
        with mock.patch("apps.lounge.youtube.find_video", return_value=("", 0)):
            songs = services._filler_songs(FRIDAY, set())
        self.assertEqual(sorted(s["youtube_id"] for s in songs), ["yt0", "yt3"])   # only the saved ones

    def test_filler_falls_back_to_songs_played_before(self, *_):
        from apps.spotify_match.models import ProfilePrompt
        ProfilePrompt.objects.all().delete()
        old = Round.objects.create(session_date=FRIDAY - timedelta(days=14), number=1, opened_at=FRIDAY_5PM, started_at=FRIDAY_5PM)
        SongRequest.objects.create(round=old, position=0, track_id="old", track_name="Old Song", artist_name="Y",
                                   youtube_id="ytold", duration_ms=180_000)
        songs = services._filler_songs(FRIDAY, set())
        self.assertEqual([s["youtube_id"] for s in songs], ["ytold"])

    def test_same_song_on_two_profiles_plays_once(self, *_):
        from apps.spotify_match.models import ProfilePrompt
        ProfilePrompt.objects.all().delete()
        for p in self.people[:3]:
            self.answer(p, "dance", f"x-{p.id}", "Kill Bill")
        self.assertEqual(len(services._profile_songs(FRIDAY, set())), 1)

    def test_people_inactive_for_a_month_are_left_out(self, *_):
        CustomUser.objects.update(last_login=FRIDAY_5PM - timedelta(days=60), date_joined=FRIDAY_5PM - timedelta(days=90))
        self.assertEqual(services._profile_songs(FRIDAY, set()), [])

    def test_first_friday_ever_uses_the_starter_songs(self, *_):
        from apps.spotify_match.models import ProfilePrompt
        ProfilePrompt.objects.all().delete()
        hits = [{"track_id": f"st{i}", "track_name": n, "artist_name": a, "image_url": None}
                for i, (n, a) in enumerate(services.STARTER_SONGS)]
        with mock.patch("apps.spotify_match.artist_search.search_tracks", side_effect=[[h] for h in hits]):
            page = services.stage(self.people[0], at=FRIDAY_5PM)
        self.assertTrue(page["warmup"])
        self.assertEqual(page["now_playing"].requesters, [])


class RulesTests(TestCase):
    def test_closed_lounge_explains_the_rules(self):
        self.client.force_login(person("ada"))
        with mock.patch("apps.lounge.schedule.now", return_value=datetime(2026, 11, 18, 9, 0)):
            page = self.client.get("/lounge/").content.decode()
        for rule in ["Fridays from 5pm", "Request one song", "5 songs make the playlist", "15 minutes to fill it",
                     "5:15pm", "No song plays twice", "Lounge closes", "block or report"]:
            self.assertIn(rule, page)


class TrackVideoMemoryTests(TestCase):
    @override_settings(YOUTUBE_API_KEY="")
    def test_saved_matches_are_used_even_with_no_youtube_limit_left(self):
        from . import youtube
        from .models import TrackVideo
        TrackVideo.objects.create(track_id="sp9", youtube_id="vid9", duration_ms=200_000)
        with mock.patch("apps.lounge.youtube.requests.get") as get:
            self.assertEqual(youtube.find_video("sp9", "Song", "Artist"), ("vid9", 200_000))
        get.assert_not_called()


@override_settings(LOUNGE_ALWAYS_OPEN=True)
@override_settings(YOUTUBE_API_KEY="")
class SpotifyBusyLoungeTests(TestCase):
    """Spotify busy: Lounge search still finds songs the Lounge knows, and they can be requested."""
    def test_search_and_request_known_songs_while_spotify_is_busy(self):
        from apps.spotify_match import artist_search
        ada = person("ada")
        pool = services.open_round()
        SongRequest.objects.create(round=pool, user=person("bola"), track_id="4ssence", track_name="Essence",
                                   artist_name="Wizkid", duration_ms=240_000)
        busy = mock.patch("apps.spotify_match.artist_search._get", side_effect=artist_search.SpotifyBusy("wait"))
        self.client.force_login(ada)
        with busy:
            page = self.client.get("/lounge/search/?q=essen")
            self.assertContains(page, "Essence")
            services.request_song(ada, "4ssence")
        self.assertEqual(SongRequest.objects.get(user=ada).duration_ms, 240_000)
