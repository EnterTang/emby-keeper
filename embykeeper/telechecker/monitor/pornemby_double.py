import asyncio
from telethon.tl.types import Message, MessageEntityMentionName
from telethon.errors import RPCError

from ..lock import pornemby_messager_enabled, pornemby_alert
from ._base import Monitor


class PornembyDoubleMonitor(Monitor):
    name = "Pornemby 怪兽自动翻倍"
    chat_user = "PronembyTGBot2_bot"
    chat_name = "Pornemby"
    chat_keyword = r"击杀者\s+(.*)\s+是否要奖励翻倍"
    additional_auth = ["pornemby_pack"]
    allow_edit = True

    async def on_trigger(self, message: Message, key, reply):
        if pornemby_alert.get(self.client.me.id, False):
            self.log.info(f"由于风险急停不翻倍.")
            return
        for me in message.entities:
            if isinstance(me, MessageEntityMentionName):
                if me.user_id == self.client.me.id:
                    if message.buttons:
                        try:
                            await message.click(text="🎲开始翻倍游戏")
                        except RPCError:
                            pass
                        else:
                            self.log.info("检测到 Pornemby 怪兽击败, 已点击翻倍.")
                            return

    async def init(self):
        interval = 1
        while True:
            if pornemby_messager_enabled.get(self.client.me.id, False):
                return True
            await asyncio.sleep(interval)
            interval += 1
