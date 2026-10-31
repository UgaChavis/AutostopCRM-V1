class ForeignFenceHeld(RuntimeError):
    """The fence atomically rejected acquisition because another owner holds it."""
