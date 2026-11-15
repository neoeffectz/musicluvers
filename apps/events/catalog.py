"""Every domain event in SoundMatch, in one place.

Producers publish these names with `bus.publish(...)`; consumers subscribe
with `@bus.subscribe(...)`. Neither side imports the other.
"""

# payload: user_id, source ("spotify" | "lastfm")
TASTE_SOURCE_CONNECTED = "taste.source_connected"

# payload: user_id, source ("manual" | "spotify" | "lastfm")
TASTE_PROFILE_UPDATED = "taste.profile_updated"

# payload: user_id
MATCH_SCORES_UPDATED = "match.scores_updated"

# payload: user_a_id, user_b_id
MATCH_CREATED = "match.created"

# payload: user_a_id, user_b_id, removed_by_id
MATCH_REMOVED = "match.removed"

# payload: message_id, conversation_id
MESSAGE_SENT = "message.sent"
