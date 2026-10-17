"""Read only this teaching game's visible HUD pixels."""
from gamelab.screens import observe as observe_hud

def observe(frame):
    return observe_hud(frame, 'party')
