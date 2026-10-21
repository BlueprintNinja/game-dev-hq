from hq_sdk.plugin import NavigationItem, PluginIntegration, PluginManifest

lazy from .services import capabilities
lazy from .views import dashboard_cards


def integration():
    return PluginIntegration(capabilities=capabilities, dashboard=dashboard_cards)


plugin = PluginManifest(
    id="gamedev.tasks", name="Game Dev", version="0.2.0", distribution="gamedev-hq-tasks",
    source_repository="BlueprintNinja/game-dev-hq", source_workflow=".github/workflows/gamedev.yml",
    api_version=4, integration_provider="gamedev.hq_plugin.plugin:integration",
    django_apps=("gamedev.hq_plugin",), url_prefix="game-dev/", urlconf="gamedev.hq_plugin.urls",
    navigation=(NavigationItem("Game Dev", "gamedev:index", "gamedev", order=150),),
    operator_capabilities=("gamedev.read", "gamedev.write"),
)
