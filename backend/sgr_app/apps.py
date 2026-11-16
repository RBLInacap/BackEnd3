from django.apps import AppConfig


class SgrAppConfig(AppConfig):
    name = 'sgr_app'

    def ready(self):
        from sgr_app import signals  # noqa: F401  (registra los receptores)
