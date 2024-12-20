import asyncio

from telethon import TelegramClient
from telethon.tl.types import Message, Chat, User
from telethon.errors import RPCError
from cachetools import TTLCache

from ._base import Monitor

__ignore__ = True


class FollowMonitor(Monitor):
    name = "全部群组从众"
    lock = asyncio.Lock()
    cache = TTLCache(maxsize=2048, ttl=300)
    chat_follow_user = 5

    async def start(self):
        async with self.listener():
            self.log.info(f"开始监视: {self.name}.")
            await self.failed.wait()
            self.log.error(f"发生错误, 不再监视: {self.name}.")
            return False

    async def message_handler(self, client: TelegramClient, message: Message):
        if not message.text:
            return
        if not isinstance(message.chat, (Chat)):
            return
        if len(message.text) > 50:
            return
        if message.text.startswith("/"):
            return
        if not isinstance(message.sender, User):
            return
        if message.sender.bot:
            return
        ident = (message.chat.id, message.text)
        async with self.lock:
            if ident in self.cache:
                self.cache[ident] += 1
                if self.cache[ident] == self.chat_follow_user:
                    try:
                        chat_id, text = ident
                        await self.client.send_message(chat_id, text)
                    except RPCError as e:
                        self.log.warning(f"发送从众信息到群组 {message.chat.title} 失败: {e}.")
                    else:
                        self.log.info(f"已发送从众信息到群组 {message.chat.title}.")
            else:
                self.cache[ident] = 1
