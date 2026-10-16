"""turn.card: one deckbuilding fight with a known draw order and visible enemy intents."""

from dataclasses import dataclass

from sims.base import Sim


# Cards drawn each turn as (attack, block, power), and the enemy's repeating intents.
HANDS = ((2, 2, 1), (3, 1, 0), (1, 3, 1), (2, 2, 0))
INTENTS = (("attack", 9), ("attack", 14), ("buff", 3), ("attack", 7))
MAX_HP = 40
ATTACK, BLOCK, POWER, POTION_BLOCK = 6, 5, 2, 12


@dataclass
class CardSim(Sim):
    ACTIONS = ("play_attack", "play_block", "play_power", "use_potion", "end_turn")
    HOLD = "end_turn"
    HORIZON = 10
    MAX_VITAL = MAX_HP
    SCENARIOS = {
        "opening": {},
        "lethal_intent": {"hp": 12, "hand": (1, 2, 0), "intent_index": 1},
        "kill_window": {"enemy_hp": 10, "hand": (2, 0, 0)},
        "buff_turn": {"hand": (1, 1, 1), "intent_index": 2, "enemy_hp": 40},
        "no_potion_low": {"hp": 10, "potions": 0, "enemy_hp": 20, "hand": (1, 2, 1)},
    }

    scenario: str = "opening"
    hp: int = MAX_HP
    block: int = 0
    energy: int = 3
    strength: int = 0
    hand: tuple = HANDS[0]
    deck_index: int = 0
    potions: int = 1
    enemy_hp: int = 50
    enemy_strength: int = 0
    intent_index: int = 0
    turn: int = 1
    moves: int = 0
    outcome: str = "playing"

    def intent(self):
        kind, amount = INTENTS[self.intent_index % len(INTENTS)]
        return kind, amount + self.enemy_strength if kind == "attack" else amount

    def snapshot(self):
        kind, amount = self.intent()
        return {"scenario": self.scenario, "turn": self.turn, "hp": self.hp, "block": self.block, "energy": self.energy,
                "strength": self.strength, "hand": dict(zip(("attack", "block", "power"), self.hand)),
                "potions": self.potions, "enemy_hp": self.enemy_hp, "enemy_intent": kind, "enemy_amount": amount,
                "outcome": self.outcome}

    def describe(self):
        kind, amount = self.intent()
        attacks, blocks, powers = self.hand
        intent = ("The enemy intends to attack for %d damage when the turn ends." % amount if kind == "attack"
                  else "The enemy intends to raise its attack damage by %d permanently; it will not attack this turn." % amount)
        return ("Turn %d of a deckbuilding card battle. Player: %d of %d HP, %d block, %d of 3 energy, strength %d. "
                "Hand: %d attack cards (1 energy, deal %d damage), %d block cards (1 energy, gain %d block), "
                "%d power cards (1 energy, +%d strength for the rest of the fight). "
                "Potions: %d (each gives %d block, no energy). Enemy: %d HP. %s "
                "Block absorbs damage and resets when the next turn starts. Ending the turn lets the enemy act; "
                "unplayed cards are discarded and a new hand is drawn. Choose the single best next action."
                % (self.turn, self.hp, MAX_HP, self.block, self.energy, self.strength,
                   attacks, ATTACK + self.strength, blocks, BLOCK, powers, POWER,
                   self.potions, POTION_BLOCK, self.enemy_hp, intent))

    def _play(self, slot):
        if self.energy < 1 or self.hand[slot] < 1:
            return False
        self.energy -= 1
        self.hand = tuple(n - 1 if i == slot else n for i, n in enumerate(self.hand))
        return True

    def step(self, action):
        if self.outcome != "playing":
            return ["게임이 끝났습니다."]
        if action not in self.ACTIONS:
            raise ValueError("Unknown action: %s" % action)
        self.moves += 1
        if self.moves > 100:
            self.outcome = "draw"
            return ["행동 수 제한에 도달했습니다."]
        if action == "play_attack":
            if not self._play(0):
                return ["공격 카드를 낼 수 없습니다."]
            self.enemy_hp = max(0, self.enemy_hp - ATTACK - self.strength)
            if self.enemy_hp == 0:
                self.outcome = "won"
                return ["적을 쓰러뜨렸습니다!"]
            return ["%d 피해를 주었습니다." % (ATTACK + self.strength)]
        if action == "play_block":
            if not self._play(1):
                return ["방어 카드를 낼 수 없습니다."]
            self.block += BLOCK
            return ["방어도 %d을 얻었습니다." % BLOCK]
        if action == "play_power":
            if not self._play(2):
                return ["파워 카드를 낼 수 없습니다."]
            self.strength += POWER
            return ["힘이 %d 올랐습니다." % POWER]
        if action == "use_potion":
            if self.potions < 1:
                return ["물약이 없습니다."]
            self.potions -= 1
            self.block += POTION_BLOCK
            return ["물약으로 방어도 %d을 얻었습니다." % POTION_BLOCK]
        events = []
        kind, amount = self.intent()
        if kind == "attack":
            damage = max(0, amount - self.block)
            self.hp = max(0, self.hp - damage)
            events.append("적이 %d 피해를 주었습니다 (방어 %d)." % (damage, min(amount, self.block)))
        else:
            self.enemy_strength += amount
            events.append("적의 공격력이 %d 올랐습니다." % amount)
        self.block, self.energy = 0, 3
        self.deck_index += 1
        self.hand = HANDS[self.deck_index % len(HANDS)]
        self.intent_index += 1
        self.turn += 1
        if self.hp == 0:
            self.outcome = "lost"
        elif self.turn > 15:
            self.outcome = "draw"
        return events

    def legal(self):
        playable = [a for a, n in zip(self.ACTIONS, self.hand) if n > 0 and self.energy > 0]
        return tuple(playable + (["use_potion"] if self.potions else []) + ["end_turn"])

    def vital(self):
        return self.hp

    def danger_truth(self):
        """The question is about the coming enemy turn: 0 no HP lost, 1 HP lost, 2 the player dies."""
        after = self.after(self.HOLD)
        return 2 if after.outcome == "lost" else 1 if after.hp < self.hp else 0

    def heuristic(self):
        kind, amount = self.intent()
        useful_block = min(self.block, amount) if kind == "attack" else 0
        return self.hp * 2 - self.enemy_hp * 1.5 + self.strength * 3 + self.potions * 8 + useful_block * 2
