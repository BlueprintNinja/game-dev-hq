from django.urls import path

from . import views

app_name = "gamedev"
urlpatterns = [path("", views.index, name="index"), path("move/", views.move, name="move")]
urlpatterns += [
    path("queue/", views.queue, name="queue"),
    path("runs/<int:run_id>/", views.run_detail, name="run"),
    path("runs/<int:run_id>/decide/", views.decide, name="decide"),
    path("runs/<int:run_id>/retry/", views.submit_action, {"action": "retry"}, name="retry"),
    path("runs/<int:run_id>/apply/", views.submit_action, {"action": "apply"}, name="apply"),
    path("runs/<int:run_id>/rollback/", views.submit_action, {"action": "rollback"}, name="rollback"),
]
