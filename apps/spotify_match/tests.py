from django.test import TestCase
from apps.users.models import CustomUser
from unittest import mock
from django.test import override_settings
from apps.events import catalog
from apps.events.testing import EagerEventsMixin
from apps.messaging.models import Conversation
from .models import TopArtist, TopTrack, MatchScore, Swipe, Match, Notification, ProfilePrompt, SOURCE_MANUAL, SOURCE_LASTFM
from . import lastfm, services, taste_catalog


def make_user(username, **extra):
    """A user who finished "About you" (location, gender, birthday), unless a test says otherwise."""
    from datetime import date
    extra.setdefault("latitude", 6.52)   # Lagos
    extra.setdefault("longitude", 3.38)
    extra.setdefault("gender", "woman")
    extra.setdefault("birthdate", date(1998, 5, 1))
    return CustomUser.objects.create_user(username=username, password="pass1234", **extra)


def make_artists(user, names, genres=None):
    genres = genres or []
    for i, name in enumerate(names):
        TopArtist.objects.create(
            user=user, external_id=f"{user.username}-artist-{i}",
            name=name, genres=genres, rank=i + 1,
        )


def make_tracks(user, names):
    for i, name in enumerate(names):
        TopTrack.objects.create(
            user=user, external_id=f"{user.username}-track-{i}",
            name=name, artist_name="Someone", rank=i + 1,
        )


class DisplayScoreTests(TestCase):
    def test_no_overlap_baseline(self):
        self.assertEqual(services._display_score(set(), set(), set()), 40)

    def test_artist_cap(self):
        shared_artists = {f"a{i}" for i in range(10)}
        score = services._display_score(shared_artists, set(), set())
        self.assertEqual(score, 40 + 40)

    def test_track_cap(self):
        shared_tracks = {f"t{i}" for i in range(10)}
        score = services._display_score(set(), set(), shared_tracks)
        self.assertEqual(score, 40 + 15)

    def test_genre_cap(self):
        shared_genres = {f"g{i}" for i in range(10)}
        score = services._display_score(set(), shared_genres, set())
        self.assertEqual(score, 40 + 10)

    def test_combined_overflow_hits_hard_cap(self):
        shared_artists = {f"a{i}" for i in range(10)}
        shared_genres = {f"g{i}" for i in range(10)}
        shared_tracks = {f"t{i}" for i in range(10)}
        score = services._display_score(shared_artists, shared_genres, shared_tracks)
        self.assertEqual(score, 97)


class RecomputeAllScoresTests(TestCase):
    def test_only_pairs_with_taste_data_get_scored(self):
        a = make_user("alice")
        b = make_user("bob")
        make_user("carol")  # no taste data at all

        make_artists(a, ["Radiohead", "Bjork"], genres=["art rock"])
        make_artists(b, ["Radiohead", "Daft Punk"], genres=["art rock"])
        make_tracks(a, ["Karma Police"])
        make_tracks(b, ["Karma Police"])

        services.recompute_all_scores()

        self.assertEqual(MatchScore.objects.count(), 1)
        score = MatchScore.objects.first()
        self.assertEqual(set(score.shared_artists), {"Radiohead"})
        self.assertEqual(set(score.shared_genres), {"art rock"})
        self.assertEqual(set(score.shared_tracks), {"Karma Police"})

    def test_idempotent(self):
        a = make_user("alice")
        b = make_user("bob")
        make_artists(a, ["Radiohead"])
        make_artists(b, ["Radiohead"])

        services.recompute_all_scores()
        services.recompute_all_scores()

        self.assertEqual(MatchScore.objects.count(), 1)


class DiscoveryFeedTests(TestCase):
    def _matched_pair(self, pref_a="everyone", gender_a="", pref_b="everyone", gender_b=""):
        a = make_user("alice", preference=pref_a, gender=gender_a)
        b = make_user("bob", preference=pref_b, gender=gender_b)
        shared = ["Radiohead", "Bjork", "Portishead", "Massive Attack", "Air"]
        make_artists(a, shared)
        make_artists(b, shared)
        services.recompute_all_scores()
        return a, b

    def test_visible_when_preferences_are_open(self):
        a, b = self._matched_pair()
        feed = services.get_discovery_feed(a)
        self.assertEqual([f["user"] for f in feed], [b])

    def test_excluded_when_preference_does_not_match_gender(self):
        a, b = self._matched_pair(pref_a="woman", gender_b="man")
        feed = services.get_discovery_feed(a)
        self.assertEqual(feed, [])

    def test_included_when_preference_matches_gender(self):
        a, b = self._matched_pair(pref_a="woman", gender_b="woman")
        feed = services.get_discovery_feed(a)
        self.assertEqual([f["user"] for f in feed], [b])

    def test_already_swiped_excluded(self):
        a, b = self._matched_pair()
        Swipe.objects.create(from_user=a, to_user=b, liked=False)
        feed = services.get_discovery_feed(a)
        self.assertEqual(feed, [])

    def test_min_score_threshold(self):
        a, b = self._matched_pair()
        feed = services.get_discovery_feed(a, min_score=100)
        self.assertEqual(feed, [])


class DiscoverTabViewTests(TestCase):
    def test_discover_tab_returns_ok_and_nonblank(self):
        user = make_user("alice")
        self.client.force_login(user)
        response = self.client.get("/dashboard/tab/discover/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.strip())


class RecordSwipeTests(EagerEventsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.a = make_user("alice")
        self.b = make_user("bob")

    def test_cannot_swipe_on_self(self):
        with self.assertRaises(ValueError):
            services.record_swipe(self.a, self.a.id, True)
        self.assertEqual(Match.objects.count(), 0)

    def test_unknown_or_malformed_user_id(self):
        with self.assertRaises(ValueError):
            services.record_swipe(self.a, "not-a-uuid", True)

    def test_mutual_like_publishes_match_created_once(self):
        services.record_swipe(self.a, self.b.id, True)
        result = services.record_swipe(self.b, self.a.id, True)
        services.record_swipe(self.b, self.a.id, True)
        self.assertTrue(result["matched"])
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(len(self.published(catalog.MATCH_CREATED)), 1)

    @mock.patch("apps.spotify_match.handlers.push_to_user")
    def test_match_created_notifies_both_users_live(self, push):
        services.record_swipe(self.a, self.b.id, True)
        with self.run_events():
            services.record_swipe(self.b, self.a.id, True)

        self.assertEqual(Notification.objects.filter(kind="match_created").count(), 2)
        kinds = [call.args[1] for call in push.call_args_list]
        self.assertEqual(kinds, ["match_created", "match_created"])
        pushed_to = {str(call.args[0]) for call in push.call_args_list}
        self.assertEqual(pushed_to, {str(self.a.id), str(self.b.id)})

    @mock.patch("apps.spotify_match.handlers.push_to_user")
    def test_unmatch_blocks_rematch_and_deletes_conversation(self, push):
        with self.run_events():
            services.record_swipe(self.a, self.b.id, True)
            services.record_swipe(self.b, self.a.id, True)
        user_a, user_b = sorted([self.a, self.b], key=lambda u: str(u.id))
        Conversation.objects.create(user_a=user_a, user_b=user_b)

        with self.run_events():
            services.unmatch(self.b, self.a)

        self.assertEqual(len(self.published(catalog.MATCH_REMOVED)), 1)
        self.assertFalse(Conversation.objects.exists())
        result = services.record_swipe(self.a, self.b.id, True)
        self.assertFalse(result["matched"])
        self.assertEqual(Match.objects.count(), 0)


class CrossSourceMatchingTests(TestCase):
    def test_artist_names_match_case_insensitively(self):
        a = make_user("alice")
        b = make_user("bob")
        make_artists(a, ["Radiohead"])
        TopArtist.objects.create(user=b, source=SOURCE_LASTFM, external_id="x", name="radiohead ", rank=1)
        services.recompute_all_scores()
        self.assertEqual(MatchScore.objects.get().shared_artists, ["Radiohead"])


def catalog_artist(i):
    return {"external_id": f"sp{i}", "name": f"Artist {i}", "genres": ["afrobeats"], "image_url": None}


class BuildTasteTests(EagerEventsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("alice")
        self.client.force_login(self.user)

    def test_dashboard_sends_new_users_to_build_taste(self):
        response = self.client.get("/dashboard/")
        self.assertRedirects(response, "/taste/build/", fetch_redirect_response=False)

    @mock.patch("apps.spotify_match.artist_search.get_artist", side_effect=lambda artist_id: catalog_artist(artist_id[-1]))
    def test_add_artist_looks_it_up_server_side(self, get_artist):
        response = self.client.post("/taste/add/", {"artist_id": "abc1"})
        self.assertEqual(response.status_code, 200)
        get_artist.assert_called_once_with("abc1")
        artist = services.manual_artists(self.user).get()
        self.assertEqual((artist.name, artist.source), ("Artist 1", SOURCE_MANUAL))

    def test_add_rejects_malformed_ids(self):
        response = self.client.post("/taste/add/", {"artist_id": "../../me"})
        self.assertEqual(response.status_code, 400)

    def test_finish_needs_enough_artists(self):
        services.add_manual_artist(self.user, catalog_artist(1))
        response = self.client.post("/taste/finish/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.published(catalog.TASTE_PROFILE_UPDATED), [])

    @mock.patch("apps.spotify_match.handlers.push_to_user")
    def test_finish_triggers_scoring_and_live_update(self, push):
        other = make_user("bob")
        make_artists(other, ["Artist 1", "Artist 2", "Artist 3"])
        for i in (1, 2, 3):
            services.add_manual_artist(self.user, catalog_artist(i))

        with self.run_events():
            response = self.client.post("/taste/finish/")

        self.assertRedirects(response, "/taste/songs/", fetch_redirect_response=False)
        score = MatchScore.objects.get()
        self.assertEqual(len(score.shared_artists), 3)
        # both people get a live Discover refresh: the one who changed and the one who can now see them
        pushed = {(str(c.args[0]), c.args[1]) for c in push.call_args_list}
        self.assertEqual(pushed, {(str(self.user.id), "discover_updated"), (str(other.id), "discover_updated")})
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.taste_synced_at)


def lastfm_response(payload):
    response = mock.Mock()
    response.content = b"x"
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


@override_settings(LASTFM_API_KEY="test-key")
class LastfmTests(EagerEventsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("alice")
        self.client.force_login(self.user)

    def test_connect_rejects_bad_usernames(self):
        response = self.client.post("/taste/lastfm/", {"lastfm_username": "<script>"})
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.lastfm_username, "")

    @mock.patch("apps.spotify_match.lastfm.user_exists", return_value=False)
    def test_connect_rejects_unknown_users(self, _):
        response = self.client.post("/taste/lastfm/", {"lastfm_username": "nobody_here"})
        self.assertEqual(response.status_code, 400)

    @mock.patch("apps.spotify_match.handlers.push_to_user")
    @mock.patch("apps.spotify_match.lastfm.sync_lastfm_taste")
    @mock.patch("apps.spotify_match.lastfm.user_exists", return_value=True)
    def test_connect_publishes_and_syncs_in_background(self, _, sync, push):
        with self.run_events():
            response = self.client.post("/taste/lastfm/", {"lastfm_username": "alice_fm"})

        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.lastfm_username, "alice_fm")
        sync.assert_called_once()
        self.assertEqual(len(self.published(catalog.TASTE_SOURCE_CONNECTED)), 1)
        self.assertEqual(len(self.published(catalog.TASTE_PROFILE_UPDATED)), 1)

    def test_sync_parses_lastfm_and_keeps_other_sources(self):
        self.user.lastfm_username = "alice_fm"
        self.user.save()
        services.add_manual_artist(self.user, catalog_artist(1))

        def fake_get(url, params, timeout):
            method = params["method"]
            if method == "user.gettopartists":
                return lastfm_response({"topartists": {"artist": [
                    {"name": "Burna Boy", "mbid": "m1"},
                    {"name": "Tems", "mbid": ""},
                ]}})
            if method == "user.gettoptracks":
                return lastfm_response({"toptracks": {"track": {"name": "Free Mind", "mbid": "", "artist": {"name": "Tems"}}}})
            return lastfm_response({"toptags": {"tag": [{"name": "Afrobeats"}, {"name": "Nigerian"}]}})

        with mock.patch("apps.spotify_match.lastfm.requests.get", side_effect=fake_get):
            lastfm.sync_lastfm_taste(self.user)

        lastfm_artists = TopArtist.objects.filter(user=self.user, source=SOURCE_LASTFM).order_by("rank")
        self.assertEqual([a.name for a in lastfm_artists], ["Burna Boy", "Tems"])
        self.assertEqual(lastfm_artists[0].genres, ["afrobeats", "nigerian"])
        self.assertEqual(TopTrack.objects.get(user=self.user).name, "Free Mind")
        self.assertEqual(services.manual_artists(self.user).count(), 1)


class SwipeViewTests(TestCase):
    def test_bad_user_id_is_400(self):
        user = make_user("alice")
        self.client.force_login(user)
        response = self.client.post("/dashboard/swipe/", {"to_user_id": "nope", "liked": "true"})
        self.assertEqual(response.status_code, 400)


def catalog_track(track_id):
    return {"track_id": track_id, "track_name": "Essence", "artist_name": "Wizkid", "image_url": None}


class GenreStepTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.client.force_login(self.user)

    def test_new_users_start_on_genres(self):
        response = self.client.get("/taste/build/")
        self.assertEqual(response.context["step"], "genres")

    def test_saves_known_genres_only_and_caps_them(self):
        picks = ["afrobeats", "made-up", "rnb", "pop", "gospel", "rock", "amapiano", "afrobeats"]
        response = self.client.post("/taste/genres/", {"genres": picks})
        self.assertRedirects(response, "/taste/build/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.favorite_genres, ["afrobeats", "rnb", "pop", "gospel", "rock"])
        self.assertEqual(self.client.get("/taste/build/").context["step"], "artists")

    def test_needs_at_least_one_genre(self):
        response = self.client.post("/taste/genres/", {"genres": ["made-up"]})
        self.assertEqual(response.status_code, 400)


class ArtistPickingTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.client.force_login(self.user)

    @mock.patch("apps.spotify_match.artist_search.get_artist",
                return_value={"external_id": "sp1", "name": "Burna Boy", "genres": [], "image_url": None})
    def test_genres_come_from_seed_list_when_spotify_has_none(self, _):
        self.client.post("/taste/add/", {"artist_id": "sp1"})
        artist = services.manual_artists(self.user).get()
        self.assertIn("afrobeats", artist.genres)
        self.assertIn("dancehall", artist.genres)

    @mock.patch("apps.spotify_match.artist_search.get_artist", side_effect=lambda artist_id: catalog_artist(artist_id[-1]))
    def test_tapping_a_picked_tile_removes_it(self, _):
        self.client.post("/taste/add/", {"artist_id": "sp1", "toggle": "1"})
        self.assertEqual(services.manual_artists(self.user).count(), 1)
        self.client.post("/taste/add/", {"artist_id": "sp1", "toggle": "1"})
        self.assertEqual(services.manual_artists(self.user).count(), 0)

    @mock.patch("apps.spotify_match.artist_search.get_artist", side_effect=lambda artist_id: catalog_artist(artist_id[-1]))
    def test_search_add_without_toggle_never_removes(self, _):
        self.client.post("/taste/add/", {"artist_id": "sp1"})
        self.client.post("/taste/add/", {"artist_id": "sp1"})
        self.assertEqual(services.manual_artists(self.user).count(), 1)


class PromptTests(EagerEventsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("alice")
        self.client.force_login(self.user)

    @mock.patch("apps.spotify_match.artist_search.get_track", side_effect=catalog_track)
    def test_song_is_looked_up_server_side(self, get_track):
        response = self.client.post("/taste/songs/set/", {"prompt": "anthem", "track_id": "abc123"})
        self.assertEqual(response.status_code, 200)
        get_track.assert_called_once_with("abc123")
        prompt = ProfilePrompt.objects.get(user=self.user)
        self.assertEqual((prompt.prompt, prompt.track_name), ("anthem", "Essence"))

    @mock.patch("apps.spotify_match.artist_search.get_track", side_effect=catalog_track)
    def test_setting_again_replaces_the_answer(self, _):
        self.client.post("/taste/songs/set/", {"prompt": "anthem", "track_id": "a1"})
        self.client.post("/taste/songs/set/", {"prompt": "anthem", "track_id": "a2"})
        self.assertEqual(ProfilePrompt.objects.get(user=self.user).track_id, "a2")

    def test_rejects_unknown_prompt_and_bad_ids(self):
        self.assertEqual(self.client.post("/taste/songs/set/", {"prompt": "nope", "track_id": "a1"}).status_code, 400)
        self.assertEqual(self.client.post("/taste/songs/set/", {"prompt": "anthem", "track_id": "../x"}).status_code, 400)
        self.assertEqual(self.client.get("/taste/songs/search/", {"prompt": "nope", "q": "x"}).status_code, 400)

    def test_clear(self):
        ProfilePrompt.objects.create(user=self.user, prompt="anthem", **catalog_track("a1"))
        self.client.post("/taste/songs/clear/anthem/")
        self.assertFalse(ProfilePrompt.objects.exists())

    def test_done_rescores_when_there_are_songs(self):
        make_artists(self.user, ["Wizkid"])
        ProfilePrompt.objects.create(user=self.user, prompt="anthem", **catalog_track("a1"))
        response = self.client.post("/taste/songs/done/")
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        self.assertEqual(len(self.published(catalog.TASTE_PROFILE_UPDATED)), 1)

    def test_skip_does_not_publish(self):
        self.client.post("/taste/songs/done/")
        self.assertEqual(self.published(catalog.TASTE_PROFILE_UPDATED), [])


class SignupMatchingTests(TestCase):
    def test_shared_prompt_song_and_genres_count(self):
        a = make_user("alice", favorite_genres=["afrobeats", "rnb"])
        b = make_user("bob", favorite_genres=["afrobeats"])
        make_artists(a, ["Wizkid"])
        make_artists(b, ["Tems"])
        ProfilePrompt.objects.create(user=a, prompt="anthem", **catalog_track("a1"))
        ProfilePrompt.objects.create(user=b, prompt="dance", **catalog_track("a1"))

        services.recompute_all_scores()

        score = MatchScore.objects.get()
        self.assertEqual(score.shared_tracks, ["Essence"])
        self.assertEqual(score.shared_genres, ["afrobeats"])


SEED = [
    {"external_id": "sp1", "name": "Burna Boy", "genres": [], "image_url": None},
    {"external_id": "sp2", "name": "Wizkid", "genres": [], "image_url": None},
]


def _resolve_seed(name):
    return next((dict(a) for a in SEED if a["name"] == name), None)


@mock.patch("apps.spotify_match.taste_catalog._in_background")  # no real lookups after the test
@mock.patch("apps.spotify_match.taste_catalog.resolve_artist", side_effect=_resolve_seed)
@mock.patch("apps.spotify_match.taste_catalog.artist_pool", return_value=["Burna Boy", "Wizkid"])
class ArtistDeckTests(EagerEventsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("alice", favorite_genres=["afrobeats"])
        self.client.force_login(self.user)

    def test_pass_moves_to_another_artist(self, *_):
        first = self.client.get("/dashboard/artists/").context["artist"]["external_id"]
        response = self.client.post("/dashboard/artists/swipe/", {"artist_id": first, "liked": "false"})
        self.assertEqual(response.context["artist"]["external_id"], ({"sp1", "sp2"} - {first}).pop())
        self.assertFalse(services.manual_artists(self.user).exists())
        last = self.client.post("/dashboard/artists/swipe/", {"artist_id": response.context["artist"]["external_id"], "liked": "false"})
        self.assertIsNone(last.context["artist"])  # passed on everyone: "you've been through all of them"

    @mock.patch("apps.spotify_match.artist_search.get_artist", side_effect=lambda artist_id: dict(SEED[0]))
    def test_like_adds_artist_and_skips_it(self, *_):
        response = self.client.post("/dashboard/artists/swipe/", {"artist_id": "sp1", "liked": "true"})
        self.assertEqual(services.manual_artists(self.user).get().name, "Burna Boy")
        self.assertEqual(response.context["artist"]["external_id"], "sp2")

    @mock.patch("apps.spotify_match.artist_search.get_artist", side_effect=lambda artist_id: catalog_artist(artist_id[-1]))
    def test_like_rescores_once_taste_is_big_enough(self, *_):
        make_artists(self.user, [])
        for i in (3, 4):
            services.add_manual_artist(self.user, catalog_artist(i))
        self.client.post("/dashboard/artists/swipe/", {"artist_id": "sp1", "liked": "true"})
        self.assertEqual(len(self.published(catalog.TASTE_PROFILE_UPDATED)), 1)

    def test_rejects_bad_ids(self, *_):
        self.assertEqual(self.client.post("/dashboard/artists/swipe/", {"artist_id": "<x>", "liked": "true"}).status_code, 400)


class RandomArtistDeckTests(TestCase):
    """The deck shows random artists from the person's genres, not the same starter list in order."""
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.names = [f"Act {i}" for i in range(40)]

    def deck(self, **kwargs):
        resolve = lambda name: {"external_id": "id-" + name.replace(" ", ""), "name": name, "image_url": None}
        with mock.patch("apps.spotify_match.taste_catalog.artist_pool", return_value=self.names), \
             mock.patch("apps.spotify_match.taste_catalog.resolve_artist", side_effect=resolve):
            return taste_catalog.random_deck_artist(["afrobeats"], **kwargs)

    def test_picks_vary(self):
        picks = {self.deck()["name"] for _ in range(20)}
        self.assertGreater(len(picks), 8)

    def test_skips_artists_you_have_or_passed(self):
        skip = self.names[:-1]
        for _ in range(5):
            self.assertEqual(self.deck(skip_names=skip)["name"], "Act 39")
        self.assertIsNone(self.deck(skip_names=self.names))
        self.assertEqual(self.deck(skip_ids={"id-Act" + str(i) for i in range(39)}, tries=200)["name"], "Act 39")

    def test_labels_the_genre(self):
        self.assertEqual(self.deck()["genre_label"], "Afrobeats")

    @override_settings(LASTFM_API_KEY="k")
    def test_pool_adds_lastfm_artists_for_the_genre(self):
        page = {"topartists": {"artist": [{"name": "Victony"}, {"name": "Burna Boy"}, {"name": "Qing Madi"}]}}
        with mock.patch("apps.spotify_match.lastfm._call", return_value=page) as call, \
             mock.patch("apps.spotify_match.taste_catalog._in_background", side_effect=lambda fn, *a: fn(*a)):
            first = taste_catalog.artist_pool("afrobeats")   # builds (here: right away instead of in a thread)
            pool = taste_catalog.artist_pool("afrobeats")    # then served from the cache
        self.assertEqual(first[0], "Burna Boy")
        self.assertIn("Victony", pool)
        self.assertIn("Davido", pool)                               # starter list kept
        self.assertEqual([n.casefold() for n in pool].count("burna boy"), 1)  # no duplicates
        self.assertEqual(call.call_args.kwargs["tag"], "afrobeats")

    @override_settings(LASTFM_API_KEY="")
    def test_pool_without_lastfm_is_the_starter_list(self):
        self.assertEqual(taste_catalog.artist_pool("amapiano")[0], "Kabza De Small")

    @override_settings(LASTFM_API_KEY="k")
    def test_nobody_waits_for_lastfm(self):
        with mock.patch("apps.spotify_match.taste_catalog._in_background") as bg:
            pool = taste_catalog.artist_pool("rnb")
        self.assertEqual(pool[0], "SZA")       # starter list straight away
        bg.assert_called_once()                # full list built behind the scenes


class TasteCatalogTests(TestCase):
    def test_clean_genre_keys(self):
        self.assertEqual(taste_catalog.clean_genre_keys(["pop", "pop", "zzz", "rnb"]), ["pop", "rnb"])

    @override_settings(LASTFM_API_KEY="")
    def test_seed_genres_without_lastfm(self):
        self.assertEqual(taste_catalog.genres_for_artist("rema"), ["afrobeats"])
        self.assertEqual(taste_catalog.genres_for_artist("Unknown Person"), [])


class FormUrlRefreshTests(TestCase):
    def test_get_on_form_urls_redirects_instead_of_405(self):
        self.client.force_login(make_user("alice"))
        for url, target in [("/taste/genres/", "/taste/build/"), ("/taste/finish/", "/taste/build/"),
                            ("/taste/lastfm/", "/taste/build/"), ("/taste/songs/done/", "/taste/songs/")]:
            self.assertRedirects(self.client.get(url), target, fetch_redirect_response=False)


@mock.patch("apps.spotify_match.notifications.push_to_user")
@mock.patch("apps.spotify_match.handlers.push_to_user")
@mock.patch("apps.messaging.handlers.get_channel_layer")
class NotificationTests(EagerEventsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.a = make_user("ada", first_name="Ada")
        self.b = make_user("bayo", first_name="Bayo")

    def match(self):
        with self.run_events():
            services.record_swipe(self.a, self.b.id, True)
            services.record_swipe(self.b, self.a.id, True)
        from apps.messaging import services as messaging
        return messaging.get_or_create_conversation(self.a, self.b)

    def send(self, conversation, sender, body):
        from apps.messaging import services as messaging
        with self.run_events():
            messaging.send_message(conversation, sender, body)

    def setup_layer(self, layer):
        layer.return_value.group_send = mock.AsyncMock()

    def test_match_shows_in_panel_and_opening_marks_read(self, layer, *_):
        self.setup_layer(layer)
        self.match()
        self.client.force_login(self.a)

        badge = self.client.get("/notifications/badge/")
        self.assertContains(badge, 'class="notif-badge"')
        self.assertContains(badge, ">1<")

        panel = self.client.get("/notifications/")
        self.assertContains(panel, "You matched with Bayo")
        self.assertContains(panel, f"/messages/open/{self.b.id}/")
        self.assertEqual(panel["HX-Trigger"], "notificationsChanged")

        badge = self.client.get("/notifications/badge/")
        self.assertNotContains(badge, "notif-badge")

    def test_messages_from_one_chat_collapse_into_one_notification(self, layer, _, notif_push):
        self.setup_layer(layer)
        conversation = self.match()
        self.send(conversation, self.a, "hey you like Wizkid too?")
        self.send(conversation, self.a, "what's your favourite song")

        notes = Notification.objects.filter(user=self.b, kind="new_message")
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.first().payload["count"], 2)
        self.assertEqual(notes.first().payload["preview"], "what's your favourite song")
        self.assertFalse(Notification.objects.filter(user=self.a, kind="new_message").exists())
        self.assertEqual({str(c.args[0]) for c in notif_push.call_args_list}, {str(self.b.id)})

        self.client.force_login(self.b)
        panel = self.client.get("/notifications/")
        self.assertContains(panel, "Ada sent you 2 messages")
        self.assertContains(panel, f"/messages/?c={conversation.id}")

    def test_reading_the_chat_clears_its_message_notification(self, layer, *_):
        self.setup_layer(layer)
        conversation = self.match()
        self.send(conversation, self.a, "hi")
        from apps.messaging import services as messaging
        messaging.mark_read(conversation, self.b)
        self.assertFalse(Notification.objects.filter(user=self.b, kind="new_message", read_at__isnull=True).exists())
        # the match notification is untouched by reading a chat
        self.assertTrue(Notification.objects.filter(user=self.b, kind="match_created", read_at__isnull=True).exists())

    def test_new_message_after_reading_starts_a_fresh_notification(self, layer, *_):
        self.setup_layer(layer)
        conversation = self.match()
        self.send(conversation, self.a, "hi")
        from apps.messaging import services as messaging
        messaging.mark_read(conversation, self.b)
        self.send(conversation, self.a, "you there?")
        unread = Notification.objects.filter(user=self.b, kind="new_message", read_at__isnull=True)
        self.assertEqual(unread.count(), 1)
        self.assertEqual(unread.first().payload["count"], 1)

    def test_panel_needs_login(self, *_):
        self.assertEqual(self.client.get("/notifications/").status_code, 302)
        self.assertEqual(self.client.get("/notifications/badge/").status_code, 302)

    def test_empty_panel(self, *_):
        self.client.force_login(self.a)
        self.assertContains(self.client.get("/notifications/"), "Nothing yet")

    def test_messages_page_has_the_bell(self, layer, *_):
        self.setup_layer(layer)
        self.match()
        self.client.force_login(self.a)
        page = self.client.get("/messages/")
        self.assertContains(page, 'id="notif-bell"')
        self.assertContains(page, 'id="notif-panel"')

    def test_reading_open_chat_endpoint_clears_notification(self, layer, *_):
        self.setup_layer(layer)
        conversation = self.match()
        self.send(conversation, self.a, "hi")
        stranger = make_user("zed")
        self.client.force_login(stranger)
        self.assertEqual(self.client.post(f"/messages/read/{conversation.id}/").status_code, 403)
        self.client.force_login(self.b)
        self.assertEqual(self.client.get(f"/messages/read/{conversation.id}/").status_code, 405)
        self.assertEqual(self.client.post(f"/messages/read/{conversation.id}/").status_code, 204)
        self.assertFalse(Notification.objects.filter(user=self.b, kind="new_message", read_at__isnull=True).exists())


class DiscoverCutoffTests(TestCase):
    def test_one_shared_artist_with_similar_genres_shows_in_discover(self):
        a = make_user("ama", gender="woman", preference="man")
        b = make_user("ben", gender="man", preference="woman")
        make_artists(a, ["Wizkid", "Tems"], genres=["afrobeats", "rnb"])
        make_artists(b, ["Wizkid", "Asake"], genres=["afrobeats", "rnb"])
        services.recompute_scores_for_user(a)
        score = MatchScore.objects.get().display_score
        self.assertLess(score, 70)  # would have been hidden under the old cut-off
        self.assertEqual([x["user"] for x in services.get_discovery_feed(a)], [b])

    def test_no_shared_artists_or_songs_stays_hidden(self):
        a = make_user("ama", gender="woman", preference="man")
        b = make_user("ben", gender="man", preference="woman")
        make_artists(a, ["Tems"])
        make_artists(b, ["Asake"])
        services.recompute_scores_for_user(a)
        self.assertEqual(services.get_discovery_feed(a), [])


class LiveDiscoverTests(TestCase):
    def test_audience_is_people_who_can_see_you_and_have_not_swiped(self):
        me = make_user("me", gender="woman", preference="man")
        fan = make_user("fan", gender="man", preference="woman")
        swiped = make_user("swiped", gender="man", preference="woman")
        wrong_pref = make_user("wp", gender="man", preference="man")
        for u in (me, fan, swiped, wrong_pref):
            make_artists(u, ["Wizkid", "Tems"])
        services.recompute_scores_for_user(me)
        Swipe.objects.create(from_user=swiped, to_user=me, liked=False)
        self.assertEqual(services.discover_audience(me), [str(fan.id)])


class MoreSongQuestionsTests(TestCase):
    def setUp(self):
        self.user = make_user("ada", first_name="Ada")
        make_artists(self.user, ["Wizkid"])
        self.client.force_login(self.user)

    def test_signup_shows_three_questions_my_sound_shows_all(self):
        signup = self.client.get("/taste/songs/").content.decode()
        self.assertIn("A song that makes me dance", signup)
        self.assertNotIn("warm hug", signup)
        sound = self.client.get("/dashboard/tab/sound/").content.decode()
        for label in ["A song that makes me dance", "An underrated song people should know", "The anthem of my life",
                      "A rising artist the world should know", "A 10/10 album",
                      "A song that must be played at my wedding"]:
            self.assertIn(label.replace("'", "&#x27;"), sound)
        self.assertNotIn("warm hug", sound)
        self.assertNotIn("My road trip song", sound)  # locked until the 3 My Sound questions are answered
        self.assertIn("3 more questions to unlock", sound)
        self.assertIn("0 of 6 answered", sound)

    @mock.patch("apps.spotify_match.artist_search.get_track", return_value=catalog_track("k1"))
    def test_answer_extra_question_from_my_sound(self, _):
        response = self.client.post("/taste/songs/set/", {"prompt": "wedding", "track_id": "k1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ProfilePrompt.objects.get(user=self.user).prompt, "wedding")
        self.assertIn("1 of 6 answered", self.client.get("/dashboard/tab/sound/").content.decode())

    def test_warm_hug_can_no_longer_be_answered(self):
        response = self.client.post("/taste/songs/set/", {"prompt": "warm_hug", "track_id": "k1"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ProfilePrompt.objects.exists())


class DashboardTabParamTests(TestCase):
    def test_tab_param_opens_that_tab_and_phone_nav_is_present(self):
        user = make_user("ada", has_seen_taste_intro=True)
        make_artists(user, ["Wizkid"])
        self.client.force_login(user)
        page = self.client.get("/dashboard/?tab=profile").content.decode()
        self.assertIn('hx-get="/profile/tab/"', page)
        self.assertIn('class="m-tabbar"', page)
        self.assertIn('class="m-tab active" data-tab="profile"', page)
        self.assertIn('class="m-tab active" data-tab="discover"', self.client.get("/dashboard/?tab=bogus").content.decode())


class EveryPageRendersTests(TestCase):
    """Loads each page a signed-in person can reach, so a broken template fails the suite."""
    def test_pages_render(self):
        me = make_user("ada", first_name="Ada", gender="woman", preference="man", has_seen_taste_intro=True,
                       favorite_genres=["afrobeats"], location="Lagos")
        other = make_user("bayo", first_name="Bayo", gender="man", preference="woman")
        make_artists(me, ["Wizkid", "Tems", "Rema"])
        make_artists(other, ["Wizkid", "Tems"])
        services.recompute_scores_for_user(me)
        a, b = sorted([me, other], key=lambda u: str(u.id))
        Match.objects.create(user_a=a, user_b=b)
        conversation = Conversation.objects.create(user_a=a, user_b=b)
        self.client.force_login(me)
        pages = ["/dashboard/", "/dashboard/?tab=sound", "/dashboard/?tab=profile", "/dashboard/tab/discover/",
                 "/dashboard/tab/sound/", "/profile/tab/", "/matches/", f"/user/{other.id}/", "/messages/",
                 f"/messages/?c={conversation.id}", "/welcome/about/", "/taste/build/?step=genres", "/taste/songs/",
                 "/notifications/", "/notifications/badge/"]
        with mock.patch("apps.spotify_match.taste_catalog.suggestions_for_genres", return_value=[]):
            pages.append("/taste/build/")
            for path in pages:
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get("/").status_code, 200)


LAGOS, IKEJA, IBADAN, BENIN = (6.45, 3.39), (6.60, 3.35), (7.38, 3.94), (6.34, 5.63)


class DistanceTests(TestCase):
    """Like Tinder: you only see people within your distance AND theirs; cards show rounded distance."""
    def person(self, username, where, gender, preference, max_km=50):
        u = make_user(username, first_name=username.title(), gender=gender, preference=preference,
                      latitude=where[0] if where else None, longitude=where[1] if where else None,
                      max_distance_km=max_km)
        make_artists(u, ["Wizkid", "Tems"])
        return u

    def setUp(self):
        self.ada = self.person("ada", LAGOS, "woman", "man")

    def feed_names(self, user, **kw):
        services.recompute_scores_for_user(user)
        return sorted(c["user"].username for c in services.get_discovery_feed(user, **kw))

    def test_distance_maths(self):
        from . import distance
        a, b = CustomUser(latitude=LAGOS[0], longitude=LAGOS[1]), CustomUser(latitude=BENIN[0], longitude=BENIN[1])
        self.assertAlmostEqual(distance.km_between(a, b), 248, delta=5)
        self.assertEqual(distance.label(1.2), "Less than 2 km away")
        self.assertEqual(distance.label(7.6), "8 km away")
        self.assertEqual(distance.label(17.6), "20 km away")
        self.assertEqual(distance.label(None), "")

    def test_nearby_shown_far_hidden(self):
        self.person("ikeja", IKEJA, "man", "woman")
        self.person("benin", BENIN, "man", "woman")
        self.assertEqual(self.feed_names(self.ada), ["ikeja"])

    def test_their_limit_counts_too(self):
        self.ada.max_distance_km = 0  # Ada: anywhere
        self.ada.save()
        self.person("ibadan", IBADAN, "man", "woman", max_km=50)       # ~115 km, wants within 50
        self.person("ibadan2", IBADAN, "man", "woman", max_km=0)       # ~115 km, anywhere
        self.assertEqual(self.feed_names(self.ada), ["ibadan2"])

    def test_anywhere_and_people_without_a_location_are_never_shown(self):
        self.person("benin", BENIN, "man", "woman", max_km=0)
        self.person("nowhere", None, "man", "woman")
        self.assertEqual(self.feed_names(self.ada), [])
        self.ada.max_distance_km = 0
        self.ada.save()
        self.assertEqual(self.feed_names(self.ada), ["benin"])
        nowhere = CustomUser.objects.get(username="nowhere")
        self.assertEqual(services.discover_audience(nowhere), [])  # and they appear in nobody's Discover

    def test_show_people_further_away(self):
        self.person("benin", BENIN, "man", "woman", max_km=0)
        self.assertEqual(self.feed_names(self.ada), [])
        self.assertEqual(services.count_further_away(self.ada), 1)
        self.assertEqual(self.feed_names(self.ada, expand=True), ["benin"])

    def test_card_shows_distance_and_expand_button(self):
        self.person("ikeja", IKEJA, "man", "woman")
        services.recompute_scores_for_user(self.ada)
        self.client.force_login(self.ada)
        card = self.client.get("/dashboard/tab/discover/").content.decode()
        self.assertIn("km away", card)

        Swipe.objects.create(from_user=self.ada, to_user=CustomUser.objects.get(username="ikeja"), liked=False)
        self.person("benin", BENIN, "man", "woman", max_km=0)
        services.recompute_scores_for_user(self.ada)
        empty = self.client.get("/dashboard/tab/discover/").content.decode()
        self.assertIn("Show people further away", empty)
        expanded = self.client.get("/dashboard/tab/discover/?expand=1").content.decode()
        self.assertIn("Benin", expanded)
        self.assertIn("250 km away", expanded)

    def test_live_update_audience_respects_distance(self):
        near = self.person("ikeja", IKEJA, "man", "woman")
        self.person("benin", BENIN, "man", "woman")
        services.recompute_scores_for_user(self.ada)
        self.assertEqual(services.discover_audience(self.ada), [str(near.id)])



def answer(user, prompt, name, artist="", item_id=None):
    return ProfilePrompt.objects.create(user=user, prompt=prompt, track_id=item_id or name.replace(" ", "")[:20],
                                        track_name=name, artist_name=artist)


class ArtistAndAlbumQuestionTests(TestCase):
    def setUp(self):
        self.user = make_user("ada", first_name="Ada")
        make_artists(self.user, ["Wizkid"])
        self.client.force_login(self.user)

    @mock.patch("apps.spotify_match.artist_search.get_artist",
                return_value={"external_id": "ar1", "name": "Shallipopi", "genres": [], "image_url": "http://img/a.jpg"})
    def test_rising_artist_is_an_artist_pick(self, _):
        self.client.post("/taste/songs/set/", {"prompt": "rising_artist", "track_id": "ar1"})
        pick = ProfilePrompt.objects.get(user=self.user)
        self.assertEqual((pick.prompt, pick.kind, pick.track_name), ("rising_artist", "artist", "Shallipopi"))
        card = self.client.get("/dashboard/tab/sound/").content.decode()
        self.assertIn("Search an album", card)  # the album question searches albums

    @mock.patch("apps.spotify_match.artist_search._get", return_value={"albums": {"items": [
        {"id": "al1", "name": "Made in Lagos", "artists": [{"name": "Wizkid"}], "images": [{"url": "http://img/m.jpg"}],
         "release_date": "2020-10-30"}]}})
    def test_album_search_returns_albums(self, _get):
        page = self.client.get("/taste/songs/search/", {"prompt": "album", "q": "made in lagos"}).content.decode()
        self.assertIn("Made in Lagos", page)
        self.assertIn("Album · Wizkid", page)
        self.assertEqual(_get.call_args.args[1]["type"], "album")


class AnswersMatchTests(TestCase):
    """Profile questions count extra towards the match score, and say why on the card."""
    def setUp(self):
        self.a = make_user("ama", gender="woman", preference="man")
        self.b = make_user("ben", gender="man", preference="woman")
        make_artists(self.a, ["Tems"])
        make_artists(self.b, ["Asake"])

    def score(self):
        services.recompute_scores_for_user(self.a)
        return MatchScore.objects.get()

    def test_no_answers_no_bonus(self):
        self.assertEqual(self.score().display_score, 40)

    def test_same_song_any_question(self):
        answer(self.a, "road_trip", "Essence", "Wizkid")
        answer(self.b, "wedding", "essence ", "WizKid")
        s = self.score()
        self.assertIn("You both picked “Essence”", s.shared_answers)
        self.assertEqual(s.display_score, 40 + 5 + 8)  # shared song (+5) and same answer (+8)

    def test_same_rising_artist_and_album(self):
        answer(self.a, "rising_artist", "Shallipopi")
        answer(self.b, "rising_artist", "Shallipopi")
        answer(self.a, "album", "Made in Lagos", "Wizkid")
        answer(self.b, "album", "Made In Lagos", "Wizkid")
        s = self.score()
        self.assertEqual(s.display_score, 40 + 10 + 10)
        self.assertIn("Same 10/10 album: Made in Lagos", s.shared_answers)
        self.assertIn("You both rate Shallipopi as a rising artist", s.shared_answers)
        self.assertEqual(s.shared_tracks, [])  # artist/album names aren't counted as songs

    def test_rising_artist_the_other_listens_to(self):
        answer(self.a, "rising_artist", "Asake")  # Ben has Asake in his artists
        s = self.score()
        self.assertEqual(s.display_score, 45)
        self.assertIn("Asake is a rising-artist pick you're both into", s.shared_answers)

    def test_answer_by_an_artist_they_listen_to_is_capped(self):
        for i, name in enumerate(["Rema", "Burna Boy", "Davido"]):  # Ben already has Asake
            TopArtist.objects.create(user=self.b, external_id=f"extra-{i}", name=name, rank=10 + i)
        for i, artist in enumerate(["Asake", "Rema", "Burna Boy", "Davido"]):
            answer(self.a, ["dance", "underrated", "anthem", "this_year"][i], f"Song {i}", artist)
        s = self.score()
        self.assertEqual(s.display_score, 40 + 9)  # 4 x 3 points, capped at 9

    def test_answers_bonus_is_capped(self):
        for prompt in ["dance", "underrated", "anthem", "this_year"]:
            answer(self.a, prompt, f"Track {prompt}", "X")
            answer(self.b, prompt, f"Track {prompt}", "X")
        s = self.score()
        self.assertEqual(s.display_score, 40 + 15 + 25)  # songs +15 (max) and answers +25 (max)

    def test_card_shows_what_you_have_in_common(self):
        answer(self.a, "album", "Made in Lagos", "Wizkid")
        answer(self.b, "album", "Made in Lagos", "Wizkid")
        self.score()
        self.client.force_login(self.a)
        card = self.client.get("/dashboard/tab/discover/").content.decode()
        self.assertIn("You two have in common", card)
        self.assertIn("Same 10/10 album: Made in Lagos", card)




class PageSpeedTests(TestCase):
    """Pages must not do one query per match/chat (each query is ~0.3s from Nigeria to the database)."""
    def setUp(self):
        from apps.messaging.models import Message
        self.Message = Message
        self.me = make_user("ada", first_name="Ada", gender="woman", preference="man", has_seen_taste_intro=True)
        make_artists(self.me, ["Wizkid"])
        self.n = 0

    def add_matches(self, count):
        for _ in range(count):
            self.n += 1
            other = make_user(f"guy{self.n}", first_name=f"Guy{self.n}", gender="man", preference="woman")
            a, b = sorted([self.me, other], key=lambda u: str(u.id))
            Match.objects.create(user_a=a, user_b=b)
            conv = Conversation.objects.create(user_a=a, user_b=b)
            self.Message.objects.create(conversation=conv, sender=other, body="hey")

    def queries_for(self, path):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.force_login(self.me)
        self.client.get(path)  # warm the session cache
        with CaptureQueriesContext(connection) as q:
            self.assertEqual(self.client.get(path).status_code, 200)
        return len(q.captured_queries)

    def test_query_count_does_not_grow_with_matches(self):
        self.add_matches(2)
        few = {p: self.queries_for(p) for p in ["/messages/", "/matches/", "/dashboard/"]}
        self.add_matches(6)
        many = {p: self.queries_for(p) for p in ["/messages/", "/matches/", "/dashboard/"]}
        self.assertEqual(few, many)

    def test_no_broken_asset_links(self):
        self.add_matches(1)
        self.client.force_login(self.me)
        for path in ["/dashboard/", "/messages/", "/matches/", "/welcome/about/"]:
            self.assertNotIn('\\"', self.client.get(path).content.decode(), path)
        self.client.logout()
        self.assertNotIn('\\"', self.client.get("/").content.decode())


class QuestionOrderTests(TestCase):
    def test_my_sound_order_with_bonus_questions_last(self):
        user = make_user("ada", first_name="Ada")
        make_artists(user, ["Wizkid"])
        for key in ProfilePrompt.MORE_PROMPTS:
            answer(user, key, f"Pick {key}", "Someone")
        self.client.force_login(user)
        page = self.client.get("/dashboard/tab/sound/").content.decode()
        order = ["A song that makes me dance", "An underrated song people should know", "The anthem of my life",
                 "A rising artist the world should know", "A 10/10 album", "A song that must be played at my wedding",
                 "An artist I&#x27;d travel to see live", "My road trip song", "My favorite song this year"]
        positions = [page.index(label) for label in order]
        self.assertEqual(positions, sorted(positions))


def fake_answer(kind, item_id):
    return {"track_id": item_id, "track_name": f"Pick {item_id}", "artist_name": "Someone", "image_url": None}


@mock.patch("apps.spotify_match.artist_search.get_answer", side_effect=fake_answer)
class BonusQuestionsTests(TestCase):
    """warm hug is gone; answering the 3 My Sound questions unlocks 3 more, right away."""
    def setUp(self):
        self.user = make_user("ada", first_name="Ada")
        make_artists(self.user, ["Wizkid"])
        self.client.force_login(self.user)

    def pick(self, prompt, item="abc123"):
        return self.client.post("/taste/songs/set/", {"prompt": prompt, "track_id": item}).content.decode()

    def test_teaser_counts_up_then_the_last_answer_unlocks_three_more(self, _):
        first = self.pick("rising_artist", "ar1")
        self.assertIn('id="bonus-questions"', first)
        self.assertIn("1 of 3 done", first)
        second = self.pick("album", "al1")
        self.assertIn("2 of 3 done", second)
        third = self.pick("wedding", "tr1")
        self.assertIn('hx-swap-oob="true"', third)
        self.assertIn("3 more questions unlocked", third)
        for label in ["An artist I&#x27;d travel to see live", "My road trip song", "My favorite song this year"]:
            self.assertIn(label, third)
        page = self.client.get("/dashboard/tab/sound/").content.decode()
        self.assertIn("My road trip song", page)
        self.assertIn("0 of 9 answered".replace("0", "3"), page)

    def test_signup_questions_dont_touch_the_bonus_area(self, _):
        self.assertNotIn("bonus-questions", self.pick("dance"))

    def test_changing_an_answer_after_unlocking_keeps_bonus_questions(self, _):
        for key in ProfilePrompt.MORE_PROMPTS:
            self.pick(key)
        cleared = self.client.post("/taste/songs/clear/wedding/").content.decode()
        self.assertNotIn("bonus-questions", cleared)  # nothing vanishes while picking a new song

    def test_bonus_answers_keep_them_unlocked(self, _):
        self.pick("live_artist", "ar9")
        self.assertTrue(services.bonus_unlocked(self.user))
        self.assertIn("An artist I&#x27;d travel to see live", self.client.get("/dashboard/tab/sound/").content.decode())


class BonusQuestionMatchingTests(TestCase):
    def setUp(self):
        self.a = make_user("ada", first_name="Ada", gender="woman", preference="man")
        self.b = make_user("ben", first_name="Ben", gender="man", preference="woman")
        make_artists(self.a, ["Tems"])
        make_artists(self.b, ["Asake"])

    def score(self):
        services.recompute_scores_for_user(self.a)
        return MatchScore.objects.get()

    def test_same_live_artist_counts(self):
        answer(self.a, "live_artist", "Burna Boy")
        answer(self.b, "live_artist", "burna boy")
        s = self.score()
        self.assertIn("You'd both travel to see Burna Boy live", s.shared_answers)
        self.assertEqual(s.display_score, 50)

    def test_same_song_this_year_counts(self):
        answer(self.a, "this_year", "African Queen", "2Baba")
        answer(self.b, "road_trip", "African Queen", "2Baba")
        self.assertIn("You both picked “African Queen”", self.score().shared_answers)

    def test_old_warm_hug_answers_no_longer_count_or_show(self):
        answer(self.a, "warm_hug", "Essence", "Wizkid")
        answer(self.b, "warm_hug", "Essence", "Wizkid")
        s = self.score()
        self.assertEqual(s.shared_answers, [])
        self.assertEqual(s.display_score, 40)
        self.assertEqual(services.answered_prompts(self.a), [])


class AnswerCardsTests(TestCase):
    def setUp(self):
        self.me = make_user("ada", first_name="Ada")
        make_artists(self.me, ["Wizkid"])
        self.client.force_login(self.me)

    def test_profile_cards_lead_with_the_anthem_and_link_to_spotify(self):
        answer(self.me, "dance", "Last Last", "Burna Boy", item_id="3NKmsBCHTzxrX9gEPgjwyb")
        answer(self.me, "anthem", "Free Mind", "Tems", item_id="0SZsfRXh0Wyy1ZnsJ6QaF4")
        answer(self.me, "live_artist", "Asake", item_id="3a1tBryiczPAZpgoZN9Rzg")
        prompts = services.answered_prompts(self.me)
        self.assertEqual([p.prompt for p in prompts], ["anthem", "dance", "live_artist"])
        self.assertEqual(prompts[0].spotify_url, "https://open.spotify.com/track/0SZsfRXh0Wyy1ZnsJ6QaF4")
        self.assertEqual(prompts[2].spotify_url, "https://open.spotify.com/artist/3a1tBryiczPAZpgoZN9Rzg")

    def test_own_profile_tab_does_not_repeat_your_answers(self):
        answer(self.me, "anthem", "Free Mind", "Tems", item_id="0SZsfRXh0Wyy1ZnsJ6QaF4")
        page = self.client.get("/profile/tab/").content.decode()
        self.assertNotIn("Free Mind", page)  # they live in My Sound

    def test_other_peoples_answers_have_a_play_button(self):
        other = make_user("ben", first_name="Ben")
        answer(other, "anthem", "Free Mind", "Tems", item_id="0SZsfRXh0Wyy1ZnsJ6QaF4")
        a, b = sorted([self.me, other], key=lambda u: str(u.id))
        Match.objects.create(user_a=a, user_b=b)  # profile pages open for matches
        page = self.client.get(f"/user/{other.id}/").content.decode()
        self.assertIn("Free Mind", page)
        self.assertIn('class="answer-play" href="https://open.spotify.com/track/0SZsfRXh0Wyy1ZnsJ6QaF4"', page)


class FastScoringTests(EagerEventsMixin, TestCase):
    """Scores are built in bulk (a few queries, not ~12 per person) and only for people who could meet."""
    def setUp(self):
        super().setUp()
        self.ada = make_user("ada", gender="woman", preference="man")
        make_artists(self.ada, ["Tems", "Wizkid", "Asake"])

    def test_queries_dont_grow_with_the_number_of_users(self):
        for i in range(25):
            u = make_user(f"m{i}", gender="man", preference="woman")
            make_artists(u, ["Wizkid", f"Other {i}"])
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            services.recompute_scores_for_user(self.ada)
        self.assertLessEqual(len(queries), 8)
        self.assertEqual(MatchScore.objects.count(), 25)

    def test_people_who_can_never_meet_get_no_score(self):
        other_woman = make_user("eve", gender="woman", preference="man")  # both want men
        make_artists(other_woman, ["Tems"])
        services.recompute_scores_for_user(self.ada)
        self.assertFalse(MatchScore.objects.exists())

    def test_changing_who_you_want_to_see_rescores(self):
        from apps.users import services as user_services
        eve = make_user("eve", gender="woman", preference="man")
        make_artists(eve, ["Tems"])
        services.recompute_scores_for_user(self.ada)
        self.assertFalse(MatchScore.objects.exists())
        self.ada.refresh_from_db()
        with self.run_events():
            user_services.update_profile(eve, {"preference": "everyone"})
            user_services.update_profile(self.ada, {"preference": "everyone"})
        self.assertEqual(MatchScore.objects.get().shared_artists, ["Tems"])

    def test_bulk_results_match_one_pair_at_a_time(self):
        ben = make_user("ben", gender="man", preference="woman")
        make_artists(ben, ["Wizkid", "Asake"])
        answer(self.ada, "anthem", "Essence", "Wizkid")
        answer(ben, "wedding", "Essence", "Wizkid")
        services.recompute_scores_for_user(self.ada)
        bulk = MatchScore.objects.values("display_score", "shared_artists", "shared_tracks", "shared_answers").get()
        MatchScore.objects.all().delete()
        services._upsert_score(self.ada, ben)
        single = MatchScore.objects.values("display_score", "shared_artists", "shared_tracks", "shared_answers").get()
        self.assertEqual(bulk, single)


@mock.patch("apps.spotify_match.taste_catalog._in_background")  # no real lookups after the test
@mock.patch("apps.spotify_match.taste_catalog.resolve_artist", side_effect=_resolve_seed)
@mock.patch("apps.spotify_match.taste_catalog.artist_pool", return_value=["Burna Boy", "Wizkid"])
class DeckRemembersPassedNamesTests(TestCase):
    def test_passed_names_are_skipped_without_looking_them_up_again(self, _, resolve, __):
        self.client.force_login(make_user("alice", favorite_genres=["afrobeats"]))
        self.client.post("/dashboard/artists/swipe/", {"artist_id": "sp1", "artist_name": "Burna Boy", "liked": "false"})
        resolve.reset_mock()
        response = self.client.get("/dashboard/artists/")
        self.assertEqual(response.context["artist"]["name"], "Wizkid")
        self.assertNotIn(mock.call("Burna Boy"), resolve.call_args_list)


class DeckReadyInAdvanceTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_next_cards_are_picked_and_fetched_in_the_background(self):
        with mock.patch("apps.spotify_match.taste_catalog.artist_pool", return_value=["A1", "A2", "A3", "A4", "A5"]), \
             mock.patch("apps.spotify_match.taste_catalog._in_background") as bg:
            picks = taste_catalog.plan_next_cards(["afrobeats"], skip_names=["A1"])
        self.assertEqual(len(picks), 3)
        self.assertNotIn("A1", [name for name, _ in picks])
        bg.assert_called_once()

    def test_a_ready_card_is_shown_first(self):
        resolve = lambda name: {"external_id": "id" + name, "name": name, "image_url": None}
        with mock.patch("apps.spotify_match.taste_catalog.resolve_artist", side_effect=resolve):
            artist = taste_catalog.random_deck_artist(["afrobeats"], queue=[("Ready One", "afrobeats")])
        self.assertEqual(artist["name"], "Ready One")


class MatchesShowLatestMessageTests(TestCase):
    """The Matches list shows each match's latest message (bold when it's new) with the newest chat on top."""
    def test_latest_message_and_order(self):
        from apps.messaging.models import Message
        me, ada, bola = make_user("me"), make_user("ada"), make_user("bola")
        for other in (ada, bola):
            a, b = sorted([me, other], key=lambda u: str(u.id))
            Match.objects.create(user_a=a, user_b=b)
        a, b = sorted([me, ada], key=lambda u: str(u.id))
        chat = Conversation.objects.create(user_a=a, user_b=b)
        Message.objects.create(conversation=chat, sender=me, body="old one")
        Message.objects.create(conversation=chat, sender=ada, body="hey you")
        matches = services.get_matches(me)
        self.assertEqual(matches[0], ada)                      # the chat with news is on top
        self.assertEqual(matches[0].last_message.body, "hey you")
        self.assertTrue(matches[0].last_unread)
        self.assertIsNone(matches[1].last_message)             # bola: matched, no messages yet
        self.client.force_login(me)
        page = self.client.get("/matches/")
        self.assertContains(page, "hey you")
        self.assertContains(page, "is-unread")


class SpotifyRateLimitTests(TestCase):
    """When Spotify says "too many requests", the app waits instead of asking again (which makes the block
    longer), and the same search isn't sent twice."""
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        cache.set("spotify_app_token", "tok")

    def _resp(self, status, body=None, headers=None):
        r = mock.Mock(status_code=status, headers=headers or {})
        r.json.return_value = body or {"tracks": {"items": []}}
        r.raise_for_status.return_value = None
        return r

    def test_a_429_pauses_requests_for_the_time_spotify_asks(self):
        from apps.spotify_match import artist_search
        with mock.patch("apps.spotify_match.artist_search.requests.get",
                        return_value=self._resp(429, headers={"Retry-After": "600"})) as get:
            with self.assertRaises(artist_search.SpotifyBusy):
                artist_search.search_tracks("essence")
            with self.assertRaises(artist_search.SpotifyBusy):
                artist_search.search_tracks("calm down")
        self.assertEqual(get.call_count, 1)               # the second search never reached Spotify

    def test_same_search_is_asked_once(self):
        from apps.spotify_match import artist_search
        with mock.patch("apps.spotify_match.artist_search.requests.get", return_value=self._resp(200)) as get:
            artist_search.search_tracks("Ayra Starr")
            artist_search.search_tracks("  ayra   starr ")
        self.assertEqual(get.call_count, 1)
