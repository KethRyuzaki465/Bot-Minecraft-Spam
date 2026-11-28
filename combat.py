import math
import threading
import time

MELEE_REACH = 3.0
MELEE_APPROACH_DISTANCE = 2.7
MELEE_BACKSTEP_DISTANCE = 1.8
SWORD_COOLDOWN = 0.65
SPRINT_RELEASE = 0.15
SWORD_MATERIALS = ('wooden', 'golden', 'stone', 'copper', 'iron', 'diamond', 'netherite')


def distance_between(first, second):
    return math.dist(
        (float(first.x), float(first.y), float(first.z)),
        (float(second.x), float(second.y), float(second.z)),
    )


class Combat:
    def __init__(self, name, ignore_players, name_prefix):
        self.name = name
        self.ignore_players = ignore_players
        self.name_prefix = name_prefix
        self.last_attack = -math.inf
        self.last_weapon_swap = -math.inf
        self.hand_lock = threading.Lock()

    def equip_weapon(self, client, weapons, prefer_sword):
        weapon = weapons[-1]
        if prefer_sword:
            swords = [item for item in weapons if item.name.endswith('_sword')]
            if swords:
                ranking = {material: rank for rank, material in enumerate(SWORD_MATERIALS)}
                weapon = max(swords, key=lambda item: ranking.get(item.name.removesuffix('_sword'), -1))

        with self.hand_lock:
            if weapon.name != getattr(client.heldItem, 'name', None):
                client.equip(weapon, 'hand')
                self.last_weapon_swap = time.monotonic()

    def find_target(self, client):
        target = None
        best = math.inf
        position = client.entity.position
        players = client.players
        for name in players:
            if name in self.ignore_players or name == self.name or name.startswith(self.name_prefix):
                continue
            player = players[name]
            entity = getattr(player, 'entity', None)
            if entity is None or getattr(player, 'gamemode', None) == 3:
                continue
            distance = distance_between(position, entity.position)
            if distance < best:
                target, best = entity, distance
        return target, best

    def tick(self, client, target, distance, now):
        if target is None or client.health <= 0:
            client.clearControlStates()
            return

        aim = target.position.offset(0, target.height * 0.85, 0)
        client.lookAt(aim, True)

        # VexBot releases forward and sprint briefly after a melee swing.
        releasing = now - self.last_attack < SPRINT_RELEASE
        in_exchange = distance <= MELEE_REACH
        forward = distance > MELEE_APPROACH_DISTANCE and (not releasing or not in_exchange)
        strafe = in_exchange and not releasing
        left = int(now / 0.75) % 2 == 0
        client.setControlState('forward', forward)
        client.setControlState('back', distance < MELEE_BACKSTEP_DISTANCE and not releasing)
        client.setControlState('left', strafe and left)
        client.setControlState('right', strafe and not left)
        client.setControlState('sprint', forward and not releasing)

        if not in_exchange or not self.hand_lock.acquire(blocking=False):
            return
        try:
            held_name = getattr(client.heldItem, 'name', '')
            # shortcut: vanilla timing at 20 TPS, use server attack speed and tick rate if available.
            if held_name.endswith('_sword'):
                cooldown = SWORD_COOLDOWN
            else:
                cooldown = 1.25 if held_name.endswith('_axe') else 1.0
            if now - max(self.last_attack, self.last_weapon_swap) < cooldown:
                return
            entity = client.entity
            eye = entity.position.offset(0, entity.eyeHeight, 0)
            direction = aim.minus(eye)
            reach = direction.norm()
            if reach > MELEE_REACH:
                return
            if reach > 0 and client.world.raycast(eye, direction.scaled(1 / reach), reach):
                return
            client.attack(target)
            self.last_attack = now
        finally:
            self.hand_lock.release()
