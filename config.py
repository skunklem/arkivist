"""
Global configuration for StoryArkivist.
"""

# Toggle development mode features
DEV_MODE = True  # set to False to hide dev menu / use persistent DB
DEV_MODE = False

# Database path selection
if DEV_MODE:
    DB_PATH = ":memory:"  # In-memory DB for quick testing
else:
    DB_PATH = "story_arkivist.db"

