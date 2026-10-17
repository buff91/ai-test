"""Optional for socket games: rewrite the game's state into the text the model sees.

Delete this file to send the game's own description (or the raw state JSON).
"""

import json


def describe(state):
    return json.dumps(state, ensure_ascii=False)
