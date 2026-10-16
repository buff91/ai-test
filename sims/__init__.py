"""Simulation games served over the socket connector, each with its own truth (see sims/base.py)."""

from sims.arena import ArenaSim
from sims.cards import CardSim
from sims.party import PartySim
from sims.racer import RacerSim
from sims.shooter import ShooterSim
from sims.swarm import SwarmSim


SIMS = {"arena": ArenaSim, "cards": CardSim, "party": PartySim,
        "shooter": ShooterSim, "swarm": SwarmSim, "racer": RacerSim}
