from collections import Counter

from itertools import combinations
from apps.users.models import CustomUser
from apps.events import bus, catalog
from apps.users import safety

from . import distance
from .models import TopArtist, MatchScore, TopTrack, Swipe, Match, ProfilePrompt, SOURCE_MANUAL
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from datetime import datetime

def get_greeting():
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def get_sidebar_context(user, matches=None):
    """Pass `matches` when the page already loaded them, so they aren't fetched twice."""
    stats = taste_stats(user)
    return {
        "greeting": get_greeting(),
        "top_genre_pct": stats["top_genre_pct"],
        "top_artists": stats["top_artists"],
        "matches": get_matches(user) if matches is None else matches,
    }

def replace_source_artists(user, source, artists):
    """Swap out one source's artists, leaving the other sources untouched."""
    with transaction.atomic():
        TopArtist.objects.filter(user=user, source=source).delete()
        TopArtist.objects.bulk_create([
            TopArtist(user=user, source=source, rank=i + 1, **artist)
            for i, artist in enumerate(artists)
        ])


def replace_source_tracks(user, source, tracks):
    with transaction.atomic():
        TopTrack.objects.filter(user=user, source=source).delete()
        TopTrack.objects.bulk_create([
            TopTrack(user=user, source=source, rank=i + 1, **track)
            for i, track in enumerate(tracks)
        ])


def taste_stats(user):
    artists = list(user.top_artists.all())
    genre_counts = Counter()
    for a in artists:
        genre_counts.update(a.genres)

    total = sum(genre_counts.values())

    if total > 0:
        breakdown = [
            {"genre": genre, "pct": round(count / total * 100)}
            for genre, count in genre_counts.most_common(5)
        ]
        top_genre = breakdown[0]["genre"]
        top_genre_pct = breakdown[0]["pct"]
    else:
        
        breakdown = []
        top_genre = artists[0].name if artists else None
        top_genre_pct = 0

    return {
        "top_genre": top_genre,
        "top_genre_pct": top_genre_pct,
        "genre_breakdown": breakdown,
        "has_genre_data": total > 0,
        "top_artists": artists[:6],
        "top_tracks": list(user.top_tracks.all())[:5],
    }


# MATCHING SERVICE



def _normalized(names):
    """Key names case-insensitively so "Radiohead" from Last.fm matches
    "radiohead" typed by hand. Values keep a display spelling."""
    return {name.strip().casefold(): name.strip() for name in names if name and name.strip()}


def _artist_set(user):
    return _normalized(TopArtist.objects.filter(user=user).values_list("name", flat=True))


def _genre_set(user):
    genres = list(user.favorite_genres)
    for artist_genres in TopArtist.objects.filter(user=user).values_list("genres", flat=True):
        genres.extend(artist_genres)
    return _normalized(genres)


def _track_set(user):
    names = list(TopTrack.objects.filter(user=user).values_list("name", flat=True))
    song_questions = [key for key, _ in ProfilePrompt.PROMPT_CHOICES if key not in ProfilePrompt.PROMPT_KINDS]
    names += ProfilePrompt.objects.filter(user=user, prompt__in=song_questions).values_list("track_name", flat=True)
    return _normalized(names)


def _shared(mine, theirs):
    """Shared names, shown in the better-capitalised spelling ("Radiohead" over "radiohead")
    so the result doesn't depend on which of the two users happens to be first."""
    def display(key):
        return max(mine[key], theirs[key], key=lambda name: (sum(c.isupper() for c in name), name))
    return sorted(display(key) for key in mine.keys() & theirs.keys())


def _display_score(shared_artists, shared_genres, shared_tracks, answer_points=0):
    score = 40
    score += min(len(shared_artists) * 7, 40)
    score += min(len(shared_tracks) * 5, 15)
    score += min(len(shared_genres) * 2, 10)
    score += min(answer_points, ANSWER_POINTS_CAP)
    return min(round(score), 97)


# Profile questions count extra: picking the same song/album/rising artist says a lot about two people.
ANSWER_POINTS_CAP = 25
SAME_SONG_POINTS = 8
SAME_RISING_ARTIST_POINTS = 10
SAME_ALBUM_POINTS = 10
RISING_ARTIST_THEY_LISTEN_TO_POINTS = 5
ANSWER_BY_ARTIST_THEY_LISTEN_TO_POINTS = 3
ANSWER_BY_ARTIST_CAP = 9


def _fold(text):
    return " ".join((text or "").split()).casefold()


def _active_prompts(user):
    """Answers to questions we still ask (answers to a retired question, e.g. "warm hug", are ignored).
    Filters in Python so a prefetched `prompts` list costs no extra query."""
    current = dict(ProfilePrompt.PROMPT_CHOICES)
    return [p for p in user.prompts.all() if p.prompt in current]


def _answers(user):
    """The user's question answers grouped by kind, keyed so the same pick matches across people."""
    return _answers_from(_active_prompts(user))


def _answers_from(prompts):
    out = {"song": {}, "artist": {}, "live": {}, "album": {}, "by": {}}
    for p in prompts:
        main_artist = (p.artist_name or "").split(",")[0].strip()
        if p.prompt == "live_artist":
            out["live"][_fold(p.track_name)] = p.track_name
            out["by"][_fold(p.track_name)] = p.track_name  # counts if the other person listens to them
        elif p.kind == "artist":
            out["artist"][_fold(p.track_name)] = p.track_name
        elif p.kind == "album":
            out["album"][(_fold(p.track_name), _fold(main_artist))] = p.track_name
            if main_artist:
                out["by"][_fold(main_artist)] = main_artist
        else:
            out["song"][(_fold(p.track_name), _fold(main_artist))] = p.track_name
            if main_artist:
                out["by"][_fold(main_artist)] = main_artist
    return out


def _answer_matches(a, b, a_artists, b_artists, shared_artists):
    """(points, reasons) from the two people's question answers."""
    return _answer_matches_from(_answers(a), _answers(b), a_artists, b_artists, shared_artists)


def _answer_matches_from(mine, theirs, a_artists, b_artists, shared_artists):
    points, reasons = 0, []
    for key in sorted(mine["song"].keys() & theirs["song"].keys()):
        points += SAME_SONG_POINTS
        reasons.append(f"You both picked “{mine['song'][key]}”")
    for key in sorted(mine["album"].keys() & theirs["album"].keys()):
        points += SAME_ALBUM_POINTS
        reasons.append(f"Same 10/10 album: {mine['album'][key]}")
    for key in sorted(mine["live"].keys() & theirs["live"].keys()):
        points += SAME_RISING_ARTIST_POINTS
        reasons.append(f"You'd both travel to see {mine['live'][key]} live")
    same_rising = mine["artist"].keys() & theirs["artist"].keys()
    for key in sorted(same_rising):
        points += SAME_RISING_ARTIST_POINTS
        reasons.append(f"You both rate {mine['artist'][key]} as a rising artist")
    for answers, other_artists in ((mine, b_artists), (theirs, a_artists)):
        for key in sorted(answers["artist"].keys() - same_rising):
            if key in other_artists:
                points += RISING_ARTIST_THEY_LISTEN_TO_POINTS
                reasons.append(f"{answers['artist'][key]} is a rising-artist pick you're both into")
    already = {_fold(name) for name in shared_artists}
    by_points, by_seen = 0, set()
    for answers, other_artists in ((mine, b_artists), (theirs, a_artists)):
        for key, name in sorted(answers["by"].items()):
            if key in other_artists and key not in already and key not in by_seen:
                by_seen.add(key)
                by_points += ANSWER_BY_ARTIST_THEY_LISTEN_TO_POINTS
                reasons.append(f"Both into {name}")
    points += min(by_points, ANSWER_BY_ARTIST_CAP)
    return min(points, ANSWER_POINTS_CAP), reasons


def _visible_to_each_other(viewer, other):
    viewer_ok = viewer.preference == "everyone" or other.gender == viewer.preference
    other_ok = other.preference == "everyone" or viewer.gender == other.preference
    return viewer_ok and other_ok


def _upsert_score(a, b):
    """Score one pair (kept for small jobs and tests; bulk paths use _score_users)."""
    _save_scores([(a, b)], _tastes([a, b]))


def _tastes(users):
    """Everything scoring needs about these people, loaded in 4 queries in total (not ~6 per pair)."""
    from collections import defaultdict
    ids = [u.id for u in users]
    artists, genres, tracks, prompts = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for uid, name, artist_genres in TopArtist.objects.filter(user_id__in=ids).values_list("user_id", "name", "genres"):
        artists[uid].append(name)
        genres[uid].extend(artist_genres or [])
    for uid, name in TopTrack.objects.filter(user_id__in=ids).values_list("user_id", "name"):
        tracks[uid].append(name)
    current = dict(ProfilePrompt.PROMPT_CHOICES)
    for p in ProfilePrompt.objects.filter(user_id__in=ids, prompt__in=list(current)):
        prompts[p.user_id].append(p)
    out = {}
    for u in users:
        song_answers = [p.track_name for p in prompts[u.id] if p.prompt not in ProfilePrompt.PROMPT_KINDS]
        out[u.id] = {
            "artists": _normalized(artists[u.id]),
            "genres": _normalized(list(u.favorite_genres or []) + genres[u.id]),
            "tracks": _normalized(tracks[u.id] + song_answers),
            "answers": _answers_from(prompts[u.id]),
        }
    return out


def _score_fields(ta, tb):
    shared_artists = _shared(ta["artists"], tb["artists"])
    shared_genres = _shared(ta["genres"], tb["genres"])
    shared_tracks = _shared(ta["tracks"], tb["tracks"])
    answer_points, shared_answers = _answer_matches_from(ta["answers"], tb["answers"],
                                                         ta["artists"], tb["artists"], shared_artists)
    display = _display_score(shared_artists, shared_genres, shared_tracks, answer_points)
    return {
        "raw_score": display / 100,
        "display_score": display,
        "shared_artists": shared_artists,
        "shared_genres": shared_genres,
        "shared_tracks": shared_tracks,
        "shared_answers": shared_answers,
    }


def _save_scores(pairs, tastes):
    """Write scores for many pairs at once: one read of the existing rows, then batched updates/inserts."""
    if not pairs:
        return
    user_ids = {u.id for pair in pairs for u in pair}
    existing = {(s.user_a_id, s.user_b_id): s for s in MatchScore.objects.filter(
        user_a_id__in=user_ids, user_b_id__in=user_ids)}
    now = timezone.now()
    to_update, to_create = [], []
    for first, second in pairs:
        # Scored in the given order (the person being re-scored first, as before); stored as a sorted pair.
        fields = _score_fields(tastes[first.id], tastes[second.id])
        a, b = sorted((first, second), key=lambda u: str(u.id))
        row = existing.get((a.id, b.id))
        if row is None:
            to_create.append(MatchScore(user_a=a, user_b=b, **fields))
        elif any(getattr(row, name) != value for name, value in fields.items()):  # skip unchanged scores
            for name, value in fields.items():
                setattr(row, name, value)
            row.computed_at = now
            to_update.append(row)
    MatchScore.objects.bulk_update(to_update, list(_SCORE_FIELDS) + ["computed_at"], batch_size=500)
    MatchScore.objects.bulk_create(to_create, batch_size=500, ignore_conflicts=True)


_SCORE_FIELDS = ("raw_score", "display_score", "shared_artists", "shared_genres", "shared_tracks", "shared_answers")


def _scorable_users():
    return CustomUser.objects.filter(top_artists__isnull=False, is_active=True).distinct()


def recompute_all_scores():
    """Full rebuild across every user who could meet (admin/backfill only, not on a request path)."""
    users = list(_scorable_users())
    tastes = _tastes(users)
    _save_scores([(a, b) for a, b in combinations(users, 2) if _visible_to_each_other(a, b)], tastes)


def recompute_scores_for_user(user):
    """Re-score this person against everyone they could meet. Pairs whose gender preferences rule each
    other out are skipped (they never see each other); changing gender/preference re-scores."""
    others = [o for o in _scorable_users().exclude(id=user.id) if _visible_to_each_other(user, o)]
    tastes = _tastes([user] + others)
    _save_scores([(user, o) for o in others], tastes)


def unmatch(user, other_user):
    user_a, user_b = sorted([user, other_user], key=lambda u: str(u.id))
    with transaction.atomic():
        deleted, _ = Match.objects.filter(user_a=user_a, user_b=user_b).delete()
        Swipe.objects.filter(from_user=user, to_user=other_user).update(liked=False)
        if deleted:
            bus.publish(
                catalog.MATCH_REMOVED,
                user_a_id=str(user_a.id), user_b_id=str(user_b.id), removed_by_id=str(user.id),
            )


def mark_taste_synced(user):
    user.taste_synced_at = timezone.now()
    user.save(update_fields=["taste_synced_at"])


def request_taste_resync(user):
    """Ask every connected source to pull fresh data (handled asynchronously)."""
    sources = []
    if user.lastfm_username:
        sources.append("lastfm")
    with transaction.atomic():
        for source in sources:
            bus.publish(catalog.TASTE_SOURCE_CONNECTED, user_id=str(user.id), source=source)
    return sources


# MANUAL TASTE BUILDING

MIN_MANUAL_ARTISTS = 3
MAX_MANUAL_ARTISTS = 20


def manual_artists(user):
    return TopArtist.objects.filter(user=user, source=SOURCE_MANUAL)


def add_manual_artist(user, artist):
    """`artist` comes from the catalog lookup, never straight from the browser."""
    existing = manual_artists(user)
    if existing.filter(external_id=artist["external_id"]).exists():
        return
    if existing.count() >= MAX_MANUAL_ARTISTS:
        raise ValueError(f"You can pick up to {MAX_MANUAL_ARTISTS} artists.")
    TopArtist.objects.create(user=user, source=SOURCE_MANUAL, rank=existing.count() + 1, **artist)


def remove_manual_artist(user, artist_id):
    manual_artists(user).filter(id=artist_id).delete()
    for rank, artist in enumerate(manual_artists(user).order_by("rank"), start=1):
        if artist.rank != rank:
            artist.rank = rank
            artist.save(update_fields=["rank"])


def finish_manual_taste(user):
    if manual_artists(user).count() < MIN_MANUAL_ARTISTS:
        raise ValueError(f"Pick at least {MIN_MANUAL_ARTISTS} artists.")
    publish_taste_change(user)


def set_favorite_genres(user, genres):
    user.favorite_genres = genres
    user.save(update_fields=["favorite_genres"])


def publish_taste_change(user):
    with transaction.atomic():
        mark_taste_synced(user)
        bus.publish(catalog.TASTE_PROFILE_UPDATED, user_id=str(user.id), source=SOURCE_MANUAL)


def rescore_if_ready(user):
    """Used by the artist swipe deck: re-score after each liked artist once the user has a taste."""
    if manual_artists(user).count() >= MIN_MANUAL_ARTISTS:
        publish_taste_change(user)


# PROMPTS

def prompt_labels():
    return dict(ProfilePrompt.PROMPT_CHOICES)


def set_prompt(user, prompt, track):
    """`track` comes from the catalog lookup, never straight from the browser."""
    if prompt not in prompt_labels():
        raise ValueError("That question isn't available. Refresh the page.")
    ProfilePrompt.objects.update_or_create(user=user, prompt=prompt, defaults=track)


def clear_prompt(user, prompt):
    ProfilePrompt.objects.filter(user=user, prompt=prompt).delete()


def prompts_with_answers(user, keys=None):
    """Every question (or just `keys`) with the user's answer or None, in question order."""
    answers = {p.prompt: p for p in user.prompts.all()}
    return [{"key": key, "label": label, "answer": answers.get(key), "kind": prompt_kind(key)}
            for key, label in ProfilePrompt.PROMPT_CHOICES if keys is None or key in keys]


def prompt_kind(key):
    """"song", "artist" or "album" — what this question is answered with."""
    return ProfilePrompt.PROMPT_KINDS.get(key, "song")


_PROMPT_ORDER = {key: i for i, (key, _) in enumerate(ProfilePrompt.PROMPT_CHOICES)}


_DISPLAY_ORDER = {key: i for i, key in enumerate(ProfilePrompt.DISPLAY_ORDER)}


def answered_prompts(user):
    """The user's answers for profile cards, anthem first (Tinder-style profile song)."""
    return sorted(_active_prompts(user), key=lambda p: _DISPLAY_ORDER.get(p.prompt, _PROMPT_ORDER.get(p.prompt, 99)))


def bonus_unlocked(user, answers=None):
    """The 3 bonus questions appear once the 3 My Sound questions are answered
    (and stay, if someone already answered a bonus one and later clears an earlier answer)."""
    answered = {p.prompt for p in _active_prompts(user)} if answers is None else answers
    return set(ProfilePrompt.MORE_PROMPTS) <= answered or bool(answered & set(ProfilePrompt.BONUS_PROMPTS))


def my_sound_prompts(user):
    """(questions to show in My Sound, unlocked?, how many of the 3 unlock questions are answered)."""
    prompts = prompts_with_answers(user)
    answered = {p["key"] for p in prompts if p["answer"]}
    unlocked = bonus_unlocked(user, answered)
    shown = [p for p in prompts if unlocked or p["key"] not in ProfilePrompt.BONUS_PROMPTS]
    return shown, unlocked, len(answered & set(ProfilePrompt.MORE_PROMPTS))


# SWIPE SERVICE


# Lowest score that shows up in Discover. 40 is the base; 50 means at least one shared
# artist (or a couple of shared songs/genres), so people with close-but-different taste still meet.
MIN_DISCOVER_SCORE = 50


def discover_audience(user, min_score=MIN_DISCOVER_SCORE):
    """Ids of other people whose Discover feed can now show this user (used for live updates)."""
    scores = MatchScore.objects.filter(
        models.Q(user_a=user) | models.Q(user_b=user), display_score__gte=min_score,
    ).select_related("user_a", "user_b")
    already_swiped = set(Swipe.objects.filter(to_user=user).values_list("from_user_id", flat=True))
    already_swiped |= safety.blocked_ids(user)
    audience = []
    for score in scores:
        other = score.user_b if score.user_a_id == user.id else score.user_a
        if other.id in already_swiped or not other.is_active or not _visible_to_each_other(other, user):
            continue
        if not user.has_position:
            break  # this person has no location yet, so nobody's Discover shows them
        km = distance.km_between(user, other)
        if distance.within_range(km, user.max_distance_km) and distance.within_range(km, other.max_distance_km):
            audience.append(str(other.id))
    return audience


def _discover_candidates(user, min_score):
    """(score, other, km) for everyone this user could be shown, best score first. Already-swiped people,
    gender preferences and the *other* person's distance limit are applied here; this user's own limit is not."""
    already_swiped = set(Swipe.objects.filter(from_user=user).values_list("to_user_id", flat=True))
    already_swiped |= safety.blocked_ids(user)
    scores = MatchScore.objects.filter(
        models.Q(user_a=user) | models.Q(user_b=user),
        display_score__gte=min_score,
    ).select_related("user_a", "user_b").order_by("-display_score")
    for score in scores:
        other = score.user_b if score.user_a_id == user.id else score.user_a
        if other.id in already_swiped or not other.is_active or not _visible_to_each_other(user, other):
            continue
        if not other.has_position:
            continue  # no location = we can't tell how far away they are, so they aren't shown
        km = distance.km_between(user, other)
        if not distance.within_range(km, other.max_distance_km):
            continue  # they don't want people this far away
        yield score, other, km


def get_discovery_feed(user, min_score=MIN_DISCOVER_SCORE, limit=None, expand=False):
    """`expand=True` is "show people further away": ignore this user's own distance limit."""
    feed = []
    for score, other, km in _discover_candidates(user, min_score):
        if limit is not None and len(feed) >= limit:
            break
        if not expand and not distance.within_range(km, user.max_distance_km):
            continue
        feed.append({
            "user": other,
            "score": score.display_score,
            "distance": distance.label(km),
            "shared_answers": score.shared_answers,
            "shared_artists": score.shared_artists,
            "shared_tracks": score.shared_tracks,
            "top_artists": list(other.top_artists.all()[:5]),
            "top_tracks": list(other.top_tracks.all()[:5]),
            "prompts": answered_prompts(other),
        })
    return feed


def count_further_away(user, min_score=MIN_DISCOVER_SCORE):
    """How many good matches are hidden only because they're outside this user's distance."""
    return sum(1 for _, _, km in _discover_candidates(user, min_score)
               if not distance.within_range(km, user.max_distance_km))


# SWIPE SERVICE


def record_swipe(from_user, to_user_id, liked: bool):
    try:
        to_user = CustomUser.objects.get(id=to_user_id)
    except (CustomUser.DoesNotExist, ValidationError, ValueError):
        raise ValueError("That profile isn't available anymore.")
    if to_user.id == from_user.id:
        raise ValueError("You can't swipe on yourself.")
    if safety.is_blocked_pair(from_user, to_user):
        raise ValueError("That profile isn't available anymore.")

    matched = False
    with transaction.atomic():
        Swipe.objects.update_or_create(
            from_user=from_user, to_user=to_user,
            defaults={"liked": liked},
        )

        if liked:
            mutual = Swipe.objects.filter(
                from_user=to_user, to_user=from_user, liked=True
            ).exists()
            if mutual:
                user_a, user_b = sorted([from_user, to_user], key=lambda u: str(u.id))
                _, created = Match.objects.get_or_create(user_a=user_a, user_b=user_b)
                matched = True
                if created:
                    bus.publish(catalog.MATCH_CREATED, user_a_id=str(user_a.id), user_b_id=str(user_b.id))

    return {"matched": matched, "matched_user": to_user if matched else None}


def get_matches(user):
    matches = Match.objects.filter(
        models.Q(user_a=user) | models.Q(user_b=user)
    ).select_related("user_a", "user_b").prefetch_related("user_a__photos", "user_b__photos").order_by("-created_at")

    hidden = safety.blocked_ids(user)
    result = []
    for m in matches:
        other = m.user_b if m.user_a_id == user.id else m.user_a
        if other.id not in hidden and other.is_active:
            other.matched_at = m.created_at
            result.append(other)
    _attach_last_messages(user, result)
    # Latest conversation first (a new match with no messages yet counts from when you matched).
    result.sort(key=lambda o: o.last_message.created_at if o.last_message else o.matched_at, reverse=True)
    return result


def _attach_last_messages(user, others):
    """Each match's latest message (match.last_message, .last_unread), in 2 queries for the whole list."""
    from apps.messaging.models import Conversation, Message
    for o in others:
        o.last_message, o.last_unread = None, False
    if not others:
        return
    by_other = {}
    for c in Conversation.objects.filter(models.Q(user_a=user) | models.Q(user_b=user)).values("id", "user_a_id", "user_b_id"):
        by_other[c["user_b_id"] if c["user_a_id"] == user.id else c["user_a_id"]] = c["id"]
    latest = {m.conversation_id: m for m in Message.objects.filter(conversation_id__in=by_other.values())
              .order_by("conversation_id", "-created_at").distinct("conversation_id")}
    for o in others:
        message = latest.get(by_other.get(o.id))
        if message:
            o.last_message = message
            o.last_unread = message.sender_id != user.id and message.read_at is None


def get_user_profile_view(viewer, other_user):
    if safety.is_blocked_pair(viewer, other_user):
        return None
    is_matched = Match.objects.filter(
        models.Q(user_a=viewer, user_b=other_user) | models.Q(user_a=other_user, user_b=viewer)
    ).exists()
    if not is_matched:
        return None

    user_a, user_b = sorted([viewer, other_user], key=lambda u: str(u.id))
    score = MatchScore.objects.filter(user_a=user_a, user_b=user_b).first()

    return {
        "user": other_user,
        "score": score.display_score if score else None,
        "shared_artists": score.shared_artists if score else [],
        "shared_tracks": score.shared_tracks if score else [],
        "distance": distance.label(distance.km_between(viewer, other_user)),
        "shared_answers": score.shared_answers if score else [],
        "top_artists": list(other_user.top_artists.all()[:6]),
        "top_tracks": list(other_user.top_tracks.all()[:5]),
        "prompts": answered_prompts(other_user),
    }