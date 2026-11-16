from django.urls import path

from . import views

app_name = "lounge"

urlpatterns = [
    path("", views.lounge_page, name="lounge"),
    path("stage/", views.stage_partial, name="stage"),
    path("search/", views.search, name="search"),
    path("request/", views.request_song, name="request"),
    path("person/<uuid:user_id>/", views.person, name="person"),
    path("person/<uuid:user_id>/like/", views.like, name="like"),
]
