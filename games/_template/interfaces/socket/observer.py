"""Optional for socket games: rewrite the game's state into the text the model sees.

Delete this file to send the game's own description (or the raw state JSON).
"""


def describe(state):
    return "The player's health is %s%%." % state.get("hp", "unknown")
