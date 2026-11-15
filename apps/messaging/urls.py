from django.urls import path
from . import views

app_name = "messaging"

urlpatterns = [
    path("", views.messages_page, name="messages"),
    path("open/<uuid:other_user_id>/", views.open_conversation, name="open"),
    path("send/<uuid:conversation_id>/", views.send_message_view, name="send"),
    path("thread/<uuid:conversation_id>/", views.thread_partial, name="thread"),
    path("read/<uuid:conversation_id>/", views.mark_read_view, name="read"),
    path("list/", views.conversations_list_partial, name="list"),
    path("messages/<uuid:conversation_id>/older/", views.load_older_messages, name="load_older"),
    path("messages/<uuid:message_id>/like/", views.toggle_like_view, name="toggle_like"),
]