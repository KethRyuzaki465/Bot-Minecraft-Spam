import math
import random
import threading
import time

MELEE_REACH = 3.0
MELEE_APPROACH_DISTANCE = 2.7
MELEE_BACKSTEP_DISTANCE = 1.8
SWORD_COOLDOWN = 0.65
SPRINT_RELEASE = 0.15
SWORD_MATERIALS = ('wooden', 'golden', 'stone', 'copper', 'iron', 'diamond', 'netherite')
HEALING_APPLES = ('enchanted_golden_apple', 'golden_apple')
HEALING_HEALTH = 14
HEALING_RETREAT_DISTANCE = 24.0
EXPERT_REACTION_SECONDS = 0.05
EXPERT_STRAFE_SLOPE = math.tan(math.radians(22))
EXPERT_REACH_FLIPS = 6


def distance_between(first, second):
    return math.dist(
        (float(first.x), float(first.y), float(first.z)),
        (float(second.x), float(second.y), float(second.z)),
    )


class Combat:
    def __init__(self, name, ignore_players, bot_names, control_lock, difficulty='standard'):
        if difficulty not in ('standard', 'expert'):
            raise ValueError("bot_difficulty must be 'standard' or 'expert'")
        self.difficulty = difficulty
        self.name = name
        self.ignore_players = ignore_players
        self.bot_names = bot_names
        self.last_attack = -math.inf
        self.last_weapon_swap = -math.inf
        self.hand_lock = threading.Lock()
        self.control_lock = control_lock
        self.healing = False
        self.healing_sprint = False
        self.swing_reach = self.roll_expert_reach() if difficulty == 'expert' else MELEE_REACH
        self.expert_ready = None
        self.target_sample = None
        self.strafe_direction = -1
        self.strafe_change_at = -math.inf

    @staticmethod
    def roll_expert_reach():
        misses = EXPERT_REACH_FLIPS - random.getrandbits(EXPERT_REACH_FLIPS).bit_count()
        return MELEE_REACH - 0.1 * misses

    def reset_expert_read(self):
        self.expert_ready = None
        self.target_sample = None

    def consume_healing_apple(self, client, items, navigation=None, *, combat_mode=False):
        if not 0 < client.health < HEALING_HEALTH:
            return False
        apples = {item.name: item for item in items if item.name in HEALING_APPLES}
        apple = next((apples[name] for name in HEALING_APPLES if name in apples), None)
        if apple is None:
            return False

        sprint_jump = False
        if combat_mode and navigation is not None:
            _, distance = self.find_target(client)
            sprint_jump = distance < HEALING_RETREAT_DISTANCE

        with self.hand_lock:
            try:
                with self.control_lock:
                    self.healing = True
                    self.healing_sprint = sprint_jump
                    self.reset_expert_read()
                    if navigation is not None:
                        navigation.stop()
                    client.clearControlStates()
                client.equip(apple, 'hand')
                if sprint_jump:
                    navigation.run_retreat_hops()
                    with self.control_lock:
                        self.healing_sprint = False
                        navigation.stop()
                try:
                    client.consume()
                except Exception:
                    if client.usingHeldItem:
                        client.deactivateItem()
                    raise
            finally:
                self.last_weapon_swap = time.monotonic()
                with self.control_lock:
                    self.healing = False
                    self.healing_sprint = False
                    if navigation is not None:
                        navigation.stop()
                    client.clearControlStates()
        return True

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
            if name in self.ignore_players or name == self.name or name in self.bot_names:
                continue
            player = players[name]
            entity = getattr(player, 'entity', None)
            if entity is None or getattr(player, 'gamemode', None) == 3:
                continue
            distance = distance_between(position, entity.position)
            if distance < best:
                target, best = entity, distance
        return target, best

    def tick(self, client, target, distance, now, navigation=None):
        if target is None or client.health <= 0:
            self.reset_expert_read()
            if navigation is not None:
                navigation.stop()
            client.clearControlStates()
            return

        if self.healing:
            if navigation is None:
                self.retreat_while_healing(client, target, distance)
            elif distance < HEALING_RETREAT_DISTANCE:
                navigation.retreat(target, HEALING_RETREAT_DISTANCE, sprint_jump=self.healing_sprint)
            else:
                navigation.stop()
            return

        if navigation is not None and distance > MELEE_REACH:
            self.expert_ready = None
            navigation.follow(target)
            return
        # Keep bridge receivers alive until their method calls return.
        target_position = target.position
        aim = target_position.offset(0, target.height * 0.85, 0)
        if navigation is not None:
            if self.swing_path_blocked(client, aim):
                self.expert_ready = None
                navigation.follow(target)
                return
            navigation.stop()
        # VexBot releases forward and sprint briefly after a melee swing.
        releasing = now - self.last_attack < SPRINT_RELEASE
        in_exchange = distance <= MELEE_REACH
        expert = self.difficulty == 'expert'
        if expert and in_exchange:
            forward, back, strafe, left, releasing, movement_aim = self.expert_movement(
                client, target, aim, distance, now, releasing
            )
        else:
            forward = distance > MELEE_APPROACH_DISTANCE and (not releasing or not in_exchange)
            back = distance < MELEE_BACKSTEP_DISTANCE and not releasing
            strafe = in_exchange and not releasing
            left = int(now / 0.75) % 2 == 0
            movement_aim = aim

        client.lookAt(movement_aim, True)
        client.setControlState('forward', forward)
        client.setControlState('back', back)
        client.setControlState('left', strafe and left)
        client.setControlState('right', strafe and not left)
        client.setControlState('sprint', forward and not releasing)

        if not in_exchange or not self.hand_lock.acquire(blocking=False):
            self.expert_ready = None
            return
        try:
            held_name = getattr(client.heldItem, 'name', '')
            # shortcut: vanilla timing at 20 TPS, use server attack speed and tick rate if available.
            if held_name.endswith('_sword'):
                cooldown = 0.625 * 0.95 if expert else SWORD_COOLDOWN
            else:
                cooldown = 1.25 if held_name.endswith('_axe') else 1.0
            if distance > self.swing_reach or now - max(self.last_attack, self.last_weapon_swap) < cooldown:
                self.expert_ready = None
                return
            if navigation is None and self.swing_path_blocked(client, aim):
                self.expert_ready = None
                return
            if expert:
                if self.expert_ready is None or self.expert_ready[0] != target.id:
                    self.expert_ready = (target.id, now + EXPERT_REACTION_SECONDS)
                    return
                if now < self.expert_ready[1]:
                    return
                if movement_aim is not aim:
                    client.lookAt(aim, True)
                self.expert_ready = None
                self.swing_reach = self.roll_expert_reach()
                self.strafe_direction *= -1
                self.strafe_change_at = now + random.randint(8, 18) * EXPERT_REACTION_SECONDS
            client.attack(target)
            self.last_attack = now
        finally:
            self.hand_lock.release()

    def expert_movement(self, client, target, aim, distance, now, releasing):
        position = target.position
        x, z = position.x, position.z
        origin = client.entity.position
        dx, dz = x - origin.x, z - origin.z
        gap = math.hypot(dx, dz)
        previous = self.target_sample
        self.target_sample = (target.id, x, z, now)
        fleeing = advancing = False
        if previous is not None and previous[0] == target.id and gap > 0.01:
            _, old_x, old_z, sampled_at = previous
            elapsed = now - sampled_at
            if 0 < elapsed <= 0.25:
                # Convert client position samples to VexBot's blocks-per-tick units.
                vx = (x - old_x) * 0.05 / elapsed
                vz = (z - old_z) * 0.05 / elapsed
                speed = math.hypot(vx, vz)
                outward = (vx * dx + vz * dz) / gap
                fleeing = speed >= 0.12 and outward > 0.5 * speed
                advancing = outward < -0.05

        releasing = releasing and not fleeing
        forward = not releasing and (fleeing or distance > min(MELEE_APPROACH_DISTANCE, self.swing_reach - 0.15))
        back = (not releasing and distance < MELEE_BACKSTEP_DISTANCE and not forward) or (releasing and advancing)
        if now >= self.strafe_change_at:
            self.strafe_direction *= -1
            self.strafe_change_at = now + random.randint(8, 18) * EXPERT_REACTION_SECONDS
        left = self.strafe_direction > 0
        if forward and not fleeing:
            slope = self.strafe_direction * EXPERT_STRAFE_SLOPE
            aim = aim.offset(-dz * slope, 0, dx * slope)
        return forward, back, not releasing and not forward, left, releasing, aim

    @staticmethod
    def swing_path_blocked(client, aim):
        entity = client.entity
        position = entity.position
        eye = position.offset(0, entity.eyeHeight, 0)
        direction = aim.minus(eye)
        reach = direction.norm()
        world = client.world
        return reach > MELEE_REACH or (
            reach > 0 and world.raycast(eye, direction.scaled(1 / reach), reach) is not None
        )

    def retreat_while_healing(self, client, target, distance):
        if distance >= HEALING_RETREAT_DISTANCE:
            client.setControlState('forward', False)
            return

        position = client.entity.position
        away_x = position.x - target.position.x
        away_z = position.z - target.position.z
        if not (away_x or away_z):
            client.setControlState('forward', False)
            return

        # shortcut: straight-line retreat, add obstacle and ledge checks for uneven arenas.
        client.lookAt(position.offset(away_x, client.entity.eyeHeight, away_z), True)
        client.setControlState('forward', True)
