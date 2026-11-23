import threading
import logging
from javascript import require, On
import time
mineflayer = require('./node_modules/mineflayer/index.js')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

host = '26.95.53.170'
port = 11111
bots = []
NAME_PREFIX = "MrloCot"
IGNORE_PLAYERS = ["NhatdzKhoaito"]
class Bot:
    def __init__(self, name,host,port):
        self.name = NAME_PREFIX + str(name)
        self.host = host
        self.port = port
        self.bot = None
        self.last_attack = 0
    def start(self):
        logger.info('[%s] Đang tạo bot và kết nối tới %s:%s', self.name, self.host, self.port)
        self.bot = mineflayer.createBot({
            'host': self.host,
            'port': self.port,
            'username': self.name,
            'version': '1.21.11',
            'auth': 'offline',
            'hideErrors': False}
            )
        logger.info('[%s] Đã tạo bot, đăng ký event handlers', self.name)
        @On(self.bot, 'login')
        def login(*args):
            logger.info('[%s] Login thành công', self.name)
        @On(self.bot, 'spawn')
        def spawn(*args):
            logger.info('[%s] Đã spawn, khởi động các worker', self.name)
            threading.Thread(target=self.sikibidi, daemon=True).start()
            threading.Thread(target=self.jumping, daemon=True).start()
            threading.Thread(target=self.chasing, daemon=True).start()
            threading.Thread(target=self.inventory, daemon=True).start()
        @On(self.bot, 'error')
        def error(*args):
            logger.error('[%s] Mineflayer error: %s', self.name, args[-1] if args else 'unknown')
        @On(self.bot, 'kicked')
        def kicked(*args):
            logger.warning('[%s] Bị kick: %s', self.name, args[-1] if args else 'unknown')
        @On(self.bot, 'end')
        def end(*args):
            logger.warning('[%s] Kết nối đã kết thúc', self.name)
    def sikibidi(self):
        logger.info('[%s] Chat worker bắt đầu', self.name)
        while self.bot is not None:
            self.bot.chat("Some time i dream of saving a world...")
            time.sleep(1)
            self.bot.chat("Saving people from the invisible hand...")
            time.sleep(10)
    def jumping(self):
        logger.info('[%s] Jump worker bắt đầu', self.name)
        while self.bot is not None:
            self.bot.setControlState('jump', True)
            time.sleep(0.1)
            self.bot.setControlState('jump', False)
            time.sleep(0.1)

    def inventory(self):
        logger.info('[%s] Inventory worker started', self.name)
        while self.bot is not None:
            try:
                items = list(self.bot.inventory.items())
                apple = None
                if self.bot.health < 16:
                    apple = next((item for item in items if getattr(item, 'name', '') == 'golden_apple'), None)
                    if apple:
                        self.bot.equip(apple, 'hand')
                        self.bot.consume()
                if self.bot.food <= 14 and not apple:
                    food = next((item for item in items if self.is_food(getattr(item, 'name', ''))), None)
                    if food:
                        self.bot.equip(food, 'hand')
                        self.bot.consume()

                armor = {
                    destination: self.bot.inventory.slots[self.bot.getEquipmentDestSlot(destination)]
                    for destination in ('head', 'torso', 'legs', 'feet')
                }
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
                        self.bot.equip(item, 'hand')
                        continue
                    else:
                        continue
                    if not armor[destination]:
                        self.bot.equip(item, destination)
                        armor[destination] = item

                block_ids = [
                    self.bot.registry.blocksByName['chest'].id,
                    self.bot.registry.blocksByName['trapped_chest'].id,
                    self.bot.registry.blocksByName['shulker_box'].id
                ]
                block = self.bot.findBlock({'matching': block_ids, 'maxDistance': 4})
                if block:
                    self.loot_container(self.bot.openContainer(block))
            except Exception:
                logger.exception('[%s] Error in inventory worker', self.name)
            time.sleep(2)

    @staticmethod
    def is_food(name):
        return name in {
            'bread', 'apple', 'carrot', 'golden_carrot', 'potato', 'baked_potato',
            'beetroot', 'melon_slice', 'sweet_berries', 'glow_berries', 'cookie',
            'cooked_beef', 'cooked_porkchop', 'cooked_chicken', 'cooked_mutton',
            'cooked_rabbit', 'cooked_cod', 'cooked_salmon', 'dried_kelp', 'pumpkin_pie',
            'beef', 'porkchop', 'chicken', 'mutton', 'rabbit', 'cod', 'salmon',
            'tropical_fish', 'pufferfish', 'mushroom_stew', 'beetroot_soup',
            'rabbit_stew', 'suspicious_stew', 'honey_bottle', 'chorus_fruit'
        }

    def loot_container(self, container):
        try:
            for item in list(container.containerItems()):
                if self.is_loot(getattr(item, 'name', '')):
                    container.withdraw(item.type, getattr(item, 'metadata', None), item.count)
        finally:
            container.close()

    @staticmethod
    def is_weapon(name):
        return any(part in name for part in ('sword', 'axe', 'bow', 'crossbow', 'trident'))

    @classmethod
    def is_loot(cls, name):
        return name == 'golden_apple' or cls.is_food(name) or any(
            part in name for part in ('helmet', 'chestplate', 'leggings', 'boots', 'sword', 'axe', 'bow', 'crossbow', 'trident')
        )

    def chasing(self):
        logger.info('[%s] Chase worker bắt đầu', self.name)
        while self.bot is not None:
            try:
                target = None
                best = None
                for eid in self.bot.entities:
                    e = self.bot.entities[eid]
                    name = getattr(e, 'username', None)
                    if not name or name in IGNORE_PLAYERS or name == self.name or name.startswith(NAME_PREFIX):
                        continue
                    d = self.bot.entity.position.distanceTo(e.position)
                    if best is None or d < best:
                        best = d
                        target = e
                if target is not None:
                    self.bot.lookAt(target.position.offset(0, target.height, 0))
                    self.bot.setControlState('forward', True)
                    held_name = getattr(getattr(self.bot, 'heldItem', None), 'name', '')
                    cooldown = 0.65 if 'sword' in held_name else 1.0
                    now = time.monotonic()
                    if best < 5 and now - self.last_attack >= cooldown:
                        self.bot.attack(target)
                        self.last_attack = now
                else:
                    self.bot.setControlState('forward', False)
            except Exception:
                logger.exception('[%s] Lỗi trong chase worker', self.name)
            time.sleep(0.1)
        

for i in range(0, 1):
    logger.info('Đang khởi động bot thứ %s/%s', i, 1)
    bot = Bot(i, host, port)
    bot.start()
    bots.append(bot)
    time.sleep(1)

threading.Event().wait()
