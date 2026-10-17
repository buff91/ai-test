"""The three teaching games and their socket-service registry."""
from sims.party import PartySim
from sims.racer import RacerSim
from sims.moba import MobaSim

SIMS = {"party": PartySim, "racer": RacerSim, "moba": MobaSim}
