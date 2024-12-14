import random
from telethon.tl.types import Message, MessageEntityMentionName, KeyboardButtonCallback
from telethon.errors import RPCError

from ...utils import flatten
from ..lock import pornemby_alert
from ._base import Monitor


class PornembyDragonRainMonitor:
    class PornembyDragonRainClickMonitor(Monitor):
        name = "Pornemby 红包雨"
        chat_user = ["PronembyTGBot2_bot", "PronembyTGBot3_bot", "PornembyBot"]
        chat_name = "Pornemby"
        chat_keyword = [None]
        additional_auth = ["pornemby_pack"]
        allow_edit = True
        debug_no_log = True

        async def on_trigger(self, message: Message, key, reply):
            if pornemby_alert.get(self.client.me.id, False):
                self.log.info(f"由于风险急停不抢红包.")
                return
            buttons = await message.get_buttons()
            if buttons:
                buttons = flatten(buttons)
                for button in buttons:
                    if isinstance(button, KeyboardButtonCallback) and "红包奖励" in button.text:
                        if random.random() > self.config.get("possibility", 1.0):
                            self.log.info(f"由于概率设置不抢红包.")
                            return
                        try:
                            await button.click()
                        except TimeoutError:
                            self.log.info("检测到 Pornemby 抢红包雨, 已点击抢红包, 等待结果.")
                        except RPCError:
                            self.log.info("检测到 Pornemby 抢红包雨, 但没有抢到红包.")
                        else:
                            self.log.info("检测到 Pornemby 抢红包雨, 已点击抢红包, 等待结果.")
                        return

    class PornembyDragonRainStatusMonitor(Monitor):
        name = "Pornemby 红包雨结果"
        chat_user = ["PronembyTGBot2_bot", "PronembyTGBot3_bot", "PornembyBot"]
        chat_name = "Pornemby"
        chat_keyword = r"恭喜\s+(.*):本次获得(\d+)豆"
        allow_edit = True

        async def on_trigger(self, message: Message, key, reply):
            for me in message.entities:
                if isinstance(me, MessageEntityMentionName):
                    if me.user_id == self.client.me.id:
                        self.log.info(f"红包雨结果: 恭喜获得 {key[1]} 豆.")
