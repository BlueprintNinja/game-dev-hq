from django.urls import path

from . import views

app_name = "gamedev"
urlpatterns = [path("", views.index, name="index"), path("move/", views.move, name="move")]
