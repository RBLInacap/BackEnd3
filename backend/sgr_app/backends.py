from django.contrib.auth.backends import ModelBackend


class DemoModelBackend(ModelBackend):
    """Compatibility alias that uses only Django's standard password authentication."""
