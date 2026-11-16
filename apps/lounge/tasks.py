from celery import shared_task


@shared_task
def announce_lounge_open():
    """Friday 5pm: a phone notification to everyone who turned notifications on."""
    from apps.users import push
    from apps.users.models import PushSubscription
    for user_id in PushSubscription.objects.values_list("user_id", flat=True).distinct():
        push.notify(user_id, "The Lounge is open 🎧", "Request a song and see who picked the others. Till 7pm.",
                    url="/lounge/", tag="lounge-open")
