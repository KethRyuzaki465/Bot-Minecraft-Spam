import json
import logging
import sys
import threading
import time
from pathlib import Path

from javascript import On, require, terminate

from combat import Combat, distance_between

MODE_LOOT = 'loot'
MODE_COMBAT = 'combat'


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
NAME_PREFIX = CONFIG['name_prefix']
IGNORE_PLAYERS = CONFIG['ignore_players']
FOOD_ITEMS = set(CONFIG['food_items'])
WEAPON_KEYWORDS = tuple(CONFIG['weapon_keywords'])
LOOT_KEYWORDS = tuple(CONFIG['loot_keywords'] + CONFIG['weapon_keywords'])
CONTAINER_BLOCKS = CONFIG['container_blocks']
SEARCH_DISTANCE = CONFIG['search_distance']
INTERACTION_DISTANCE = CONFIG['interaction_distance']


class Bot:
    def __init__(self, name, host, port):
        self.name = NAME_PREFIX + str(name)
        self.host = host
        self.port = port
        self.bot = None
        self.combat = Combat(self.name, IGNORE_PLAYERS, NAME_PREFIX)
        self.control_lock = threading.Lock()
        self.mode = MODE_LOOT
        self.running = False
        self.checked_containers = set()

    def set_mode(self, mode):
        with self.control_lock:
            if mode in (MODE_LOOT, MODE_COMBAT) and mode != self.mode:
                self.mode = mode
                if self.bot is not None:
                    self.bot.clearControlStates()
                logger.info('[%s] Mode: %s', self.name, mode)
            return self.mode

    def start(self):
        mineflayer = require('./node_modules/mineflayer/index.js')
        self.running = True
        logger.info('[%s] Connecting to %s:%s', self.name, self.host, self.port)
        self.bot = mineflayer.createBot({
            'host': self.host,
            'port': self.port,
            'username': self.name,
            'version': '1.21.11',
            'auth': 'offline',
            'hideErrors': False,
        })

        @On(self.bot, 'login')
        def login(*args):
            logger.info('[%s] Login successful', self.name)

        workers_started = False

        @On(self.bot, 'spawn')
        def spawn(*args):
            nonlocal workers_started
            if workers_started:
                return
            workers_started = True
            logger.info('[%s] Spawned; starting workers', self.name)
            threading.Thread(target=self.jumping, daemon=True).start()
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

    def jumping(self):
        logger.info('[%s] Jump worker started', self.name)
        while self.running:
            with self.control_lock:
                if self.mode == MODE_LOOT:
                    self.bot.setControlState('jump', True)
            time.sleep(0.1)
            with self.control_lock:
                if self.mode == MODE_LOOT:
                    self.bot.setControlState('jump', False)
            time.sleep(0.1)

    def inventory(self):
        logger.info('[%s] Inventory worker started', self.name)
        while self.running:
            try:
                items = list(self.bot.inventory.items())
                food = None
                if self.bot.health < 16:
                    food = next((item for item in items if getattr(item, 'name', '') == 'golden_apple'), None)
                if food is None and self.bot.food <= 14:
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
                    block_ids = []
                    for name in CONTAINER_BLOCKS:
                        try:
                            block_ids.append(self.bot.registry.blocksByName[name].id)
                        except (KeyError, TypeError):
                            continue
                    if block_ids:
                        positions = self.find_container_positions(block_ids)
                        with self.control_lock:
                            if self.mode == MODE_LOOT:
                                self.bot.setControlState('forward', False)
                        for position in positions:
                            if self.mode != MODE_LOOT:
                                break
                            block = self.bot.blockAt(position)
                            if not block:
                                continue
                            key = self.container_key(block)
                            if key in self.checked_containers:
                                continue
                            if distance_between(self.bot.entity.position, block.position) > INTERACTION_DISTANCE:
                                with self.control_lock:
                                    if self.mode == MODE_LOOT:
                                        self.bot.lookAt(block.position.offset(0, 0.5, 0), True)
                                        self.bot.setControlState('forward', True)
                                break
                            valuable = self.loot_container(self.bot.openContainer(block))
                            self.checked_containers.add(key)
                            if valuable:
                                break
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
        return name == 'golden_apple' or cls.is_food(name) or any(part in name for part in LOOT_KEYWORDS)

    def chasing(self):
        logger.info('[%s] Chase worker started', self.name)
        while self.running:
            try:
                if self.mode == MODE_COMBAT:
                    target, distance = self.combat.find_target(self.bot)
                    with self.control_lock:
                        if self.running and self.mode == MODE_COMBAT:
                            self.combat.tick(self.bot, target, distance, time.monotonic())
            except Exception:
                logger.exception('[%s] Error in chase worker', self.name)
            time.sleep(0.05)


def handle_command(bot, command):
    mode = {'1': MODE_LOOT, '2': MODE_COMBAT}.get(command)
    return bot.set_mode(mode) if mode else bot.mode


def read_commands():
    logger.info('Press 1 for loot mode, 2 for combat mode.')
    try:
        if sys.platform == 'win32':
            import msvcrt
            while True:
                command = msvcrt.getwch()
                if command == '\x03':
                    for bot in bots:
                        bot.running = False
                    return
                handle_command(bots[0], command)
        else:
            while True:
                command = sys.stdin.read(1)
                if not command:
                    return
                handle_command(bots[0], command)
    except (EOFError, KeyboardInterrupt):
        return


def main():
    for i in range(1):
        bot = Bot(i, host, port)
        bot.start()
        bots.append(bot)
        time.sleep(1)
    threading.Thread(target=read_commands, daemon=True).start()
    try:
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
