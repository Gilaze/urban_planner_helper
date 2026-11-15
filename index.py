from backend.api import create_app

# The deployed demo always serves the bundled real release.
app = create_app(mode="live")
