import json
import logging
import sys
import threading
import time
from pathlib import Path

from javascript import On, require, terminate

from combat import Combat, HEALING_APPLES, distance_between
from navigation import Navigation

MODE_LOOT = 'loot'
MODE_COMBAT = 'combat'
BOT_NAMES = ['Sonion0', 'bot_saygex', 'sondulovuong']


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).with_name('botconfig.json')
with CONFIG_PATH.open(encoding='utf-8') as config_file:
    CONFIG = json.load(config_file)

host = CONFIG['host']
port = CONFIG['port']
bots = []
IGNORE_PLAYERS = CONFIG['ignore_players']
FOOD_ITEMS = set(CONFIG['food_items'])
WEAPON_KEYWORDS = tuple(CONFIG['weapon_keywords'])
LOOT_KEYWORDS = tuple(CONFIG['loot_keywords'] + CONFIG['weapon_keywords'])
CONTAINER_BLOCKS = CONFIG['container_blocks']
SEARCH_DISTANCE = CONFIG['search_distance']
INTERACTION_DISTANCE = CONFIG['interaction_distance']


class Bot:
    def __init__(self, name, host, port, *, difficulty=None):
        self.name = name
        self.host = host
        self.port = port
        self.bot = None
        self.navigation = None
        self.control_lock = threading.Lock()
        if difficulty is None:
            difficulty = CONFIG.get('bot_difficulty', 'standard')
        self.combat = Combat(self.name, IGNORE_PLAYERS, BOT_NAMES, self.control_lock, difficulty)
        self.mode = MODE_LOOT
        self.running = False
        self.checked_containers = set()

    def set_mode(self, mode):
        with self.control_lock:
            if mode in (MODE_LOOT, MODE_COMBAT) and mode != self.mode:
                self.mode = mode
                self.combat.reset_expert_read()
                if self.bot is not None:
                    if self.navigation is not None:
                        self.navigation.stop()
                    self.bot.clearControlStates()
                logger.info('[%s] Mode: %s', self.name, mode)
            return self.mode

    def start(self):
        mineflayer = require('./node_modules/mineflayer/index.js')
        pathfinder = require('./node_modules/mineflayer-pathfinder/index.js')
        retreat_hop = require('./retreat_hop.js')
        self.running = True
        logger.info('[%s] Connecting to %s:%s with %s difficulty', self.name, self.host, self.port, self.combat.difficulty)
        self.bot = mineflayer.createBot({
            'host': self.host,
            'port': self.port,
            'username': self.name,
            'version': '1.21.11',
            'auth': 'offline',
            'hideErrors': False,
        })
        self.bot.loadPlugin(pathfinder.pathfinder)

        @On(self.bot, 'login')
        def login(*args):
            logger.info('[%s] Login successful', self.name)

        workers_started = False

        @On(self.bot, 'spawn')
        def spawn(*args):
            nonlocal workers_started
            if workers_started:
                return
            with self.control_lock:
                self.navigation = Navigation(self.bot, pathfinder, retreat_hop)
            workers_started = True
            logger.info('[%s] Spawned; starting workers', self.name)
            threading.Thread(target=self.chasing, daemon=True).start()
            threading.Thread(target=self.inventory, daemon=True).start()

        @On(self.bot, 'error')
        def error(*args):
            logger.error('[%s] Mineflayer error: %s', self.name, args[-1] if args else 'unknown')

        @On(self.bot, 'kicked')
        def kicked(*args):
            logger.warning('[%s] Kicked: %s', self.name, args[-1] if args else 'unknown')

        @On(self.bot, 'end')
        def end(*args):
            logger.warning('[%s] Connection ended', self.name)
            self.running = False

    def inventory(self):
        logger.info('[%s] Inventory worker started', self.name)
        while self.running:
            try:
                inventory = self.bot.inventory
                items = list(inventory.items())
                healed = self.combat.consume_healing_apple(
                    self.bot, items, self.navigation, combat_mode=self.mode == MODE_COMBAT
                )
                if not healed and self.bot.food <= 14:
                    food = next((item for item in items if self.is_food(getattr(item, 'name', ''))), None)
                    if food is not None:
                        with self.combat.hand_lock:
                            self.bot.equip(food, 'hand')
                            self.combat.last_weapon_swap = time.monotonic()
                            self.bot.consume()

                armor = {
                    destination: self.bot.inventory.slots[self.bot.getEquipmentDestSlot(destination)]
                    for destination in ('head', 'torso', 'legs', 'feet')
                }
                weapons = []
                for item in items:
                    name = getattr(item, 'name', '')
                    if 'helmet' in name:
                        destination = 'head'
                    elif 'chestplate' in name:
                        destination = 'torso'
                    elif 'leggings' in name:
                        destination = 'legs'
                    elif 'boots' in name:
                        destination = 'feet'
                    elif self.is_weapon(name):
                        weapons.append(item)
                        continue
                    else:
                        continue
                    if not armor[destination]:
                        self.bot.equip(item, destination)
                        armor[destination] = item

                if weapons:
                    self.combat.equip_weapon(self.bot, weapons, prefer_sword=self.mode == MODE_COMBAT)

                if self.mode == MODE_LOOT:
                    self.loot_nearby_containers()
            except Exception:
                logger.exception('[%s] Error in inventory worker', self.name)
            time.sleep(2)

    @staticmethod
    def is_food(name):
        return name in FOOD_ITEMS

    def find_container_positions(self, block_ids):
        return list(self.bot.findBlocks({
            'matching': block_ids,
            'maxDistance': SEARCH_DISTANCE,
            'count': 16,
        }))

    def loot_nearby_containers(self):
        block_ids = []
        for name in CONTAINER_BLOCKS:
            block = self.bot.registry.blocksByName[name]
            if block is not None:
                block_ids.append(block.id)

        pending = []
        for position in self.find_container_positions(block_ids) if block_ids else ():
            if self.mode != MODE_LOOT:
                return
            block = self.bot.blockAt(position)
            if not block:
                continue
            key = self.container_key(block)
            if key in self.checked_containers:
                continue
            if (distance_between(self.bot.entity.position, block.position) > INTERACTION_DISTANCE
                    or not self.bot.canSeeBlock(block)):
                pending.append(block.position)
                continue

            with self.control_lock:
                if self.mode != MODE_LOOT:
                    return
                self.navigation.stop()
            valuable = self.loot_container(self.bot.openContainer(block))
            self.checked_containers.add(key)
            if valuable:
                return

        with self.control_lock:
            if self.mode != MODE_LOOT:
                return
            if pending:
                self.navigation.approach_containers(pending, INTERACTION_DISTANCE)
            else:
                self.navigation.stop()

    @staticmethod
    def container_key(block):
        position = block.position
        return int(position.x), int(position.y), int(position.z)

    @classmethod
    def has_valuable_items(cls, items):
        return any(cls.is_loot(getattr(item, 'name', '')) for item in items)

    def loot_container(self, container):
        try:
            items = list(container.containerItems())
            if not self.has_valuable_items(items):
                return False
            for item in items:
                if self.is_loot(getattr(item, 'name', '')):
                    try:
                        container.withdraw(item.type, getattr(item, 'metadata', None), item.count)
                    except Exception as error:
                        logger.warning('[%s] Could not withdraw %s: %s', self.name, getattr(item, 'name', 'item'), error)
            return True
        finally:
            container.close()

    @staticmethod
    def is_weapon(name):
        return any(part in name for part in WEAPON_KEYWORDS)

    @classmethod
    def is_loot(cls, name):
        return name in HEALING_APPLES or cls.is_food(name) or any(part in name for part in LOOT_KEYWORDS)

    def chasing(self):
        logger.info('[%s] Chase worker started', self.name)
        while self.running:
            try:
                if self.mode == MODE_COMBAT:
                    target, distance = self.combat.find_target(self.bot)
                    with self.control_lock:
                        if self.running and self.mode == MODE_COMBAT:
                            self.combat.tick(self.bot, target, distance, time.monotonic(), self.navigation)
            except Exception:
                logger.exception('[%s] Error in chase worker', self.name)
            time.sleep(0.05)


def handle_command(roster, command):
    mode = {'1': MODE_LOOT, '2': MODE_COMBAT}.get(command)
    if mode:
        for current in roster:
            current.set_mode(mode)


def read_commands():
    logger.info('Press 1 for loot mode or 2 for combat mode for all bots.')
    try:
        if sys.platform == 'win32':
            import msvcrt
            while True:
                command = msvcrt.getwch()
                if command == '\x03':
                    for bot in bots:
                        bot.running = False
                    return
                handle_command(bots, command)
        else:
            while True:
                command = sys.stdin.read(1)
                if not command:
                    return
                handle_command(bots, command)
    except (EOFError, KeyboardInterrupt):
        return


def main():
    try:
        for name in BOT_NAMES:
            bot = Bot(name, host, port)
            bots.append(bot)
            bot.start()
            time.sleep(1)
        if bots:
            threading.Thread(target=read_commands, daemon=True).start()
        while any(bot.running for bot in bots):
            time.sleep(0.5)
    finally:
        for bot in bots:
            bot.running = False
            if bot.bot is not None:
                bot.bot.quit()
        terminate()


if __name__ == '__main__':
    main()
