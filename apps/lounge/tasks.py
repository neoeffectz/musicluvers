from celery import shared_task


@shared_task
def announce_lounge_open():
    """Friday 5pm: a phone notification to everyone who turned notifications on."""
    from apps.users import push
    from apps.users.models import PushSubscription
    for user_id in PushSubscription.objects.values_list("user_id", flat=True).distinct():
        push.notify(user_id, "The Lounge is open", "Request a song before 5:15pm. 5 songs make tonight's playlist.",
                    url="/lounge/", tag="lounge-open")


@shared_task
def prepare_lounge_warmup():
    """Friday 4:30pm: choose the warm-up songs and find them on YouTube before 5pm."""
    from . import services
    services.prepare_warmup()
