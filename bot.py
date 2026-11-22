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

host = 'localhost'
port = 1234
bots = []
NAME_PREFIX = "GAYJIN.Sex"
IGNORE_PLAYERS = ["Alex"]
class Bot:
    def __init__(self, name,host,port):
        self.name = NAME_PREFIX + str(name)
        self.host = host
        self.port = port
        self.bot = None
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
            self.bot.chat("DIT ME MAY THANG BETA")
            time.sleep(2)
    def jumping(self):
        logger.info('[%s] Jump worker bắt đầu', self.name)
        while self.bot is not None:
            self.bot.setControlState('jump', True)
            time.sleep(0.1)
            self.bot.setControlState('jump', False)
            time.sleep(0.1)
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
                    if best < 5:
                        self.bot.attack(target)
                else:
                    self.bot.setControlState('forward', False)
            except Exception:
                logger.exception('[%s] Lỗi trong chase worker', self.name)
            time.sleep(0.1)
        

for i in range(1, 4):
    logger.info('Đang khởi động bot thứ %s/%s', i, 4)
    bot = Bot(i, host, port)
    bot.start()
    bots.append(bot)
    time.sleep(1)

threading.Event().wait()
