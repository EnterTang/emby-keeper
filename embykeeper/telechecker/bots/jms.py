import aiohttp
from aiohttp_socks import ProxyConnector, ProxyType
from telethon.tl.custom import Message
from telethon import functions, types, Button
from faker import Faker

from ._base import BotCheckin


class JMSCheckin(BotCheckin):
    name = "卷毛鼠"
    bot_username = "jmsembybot"
    bot_checked_keywords = "请明天再来签到"

    async def message_handler(self, client, message: Message):
        if message.buttons:
            buttons = [b for row in message.buttons for b in row]
            for button in buttons:
                if isinstance(button, Button.Url) and "点我签到" in button.text:
                    try:
                        result = await self.client._client(
                            functions.messages.AcceptUrlAuthRequest(
                                peer=await self.client.resolve_peer(message.chat_id),
                                msg_id=message.id,
                                button_id=button.button.button_id,
                                url=button.url
                            )
                        )
                        if isinstance(result, types.UrlAuthResultAccepted):
                            url = result.url
                            if self.proxy:
                                connector = ProxyConnector(
                                    proxy_type=ProxyType[self.proxy["scheme"].upper()],
                                    host=self.proxy["hostname"],
                                    port=self.proxy["port"],
                                    username=self.proxy.get("username", None),
                                    password=self.proxy.get("password", None),
                                )
                            else:
                                connector = aiohttp.TCPConnector()
                            for _ in range(1, 3):
                                async with aiohttp.ClientSession(connector=connector) as session:
                                    async with session.get(url, headers={"User-Agent": Faker().safari()}) as resp:
                                        if resp.status == 200:
                                            return
                    except Exception as e:
                        self.log.warning(f"处理签到按钮时出错: {e}")
                        continue
        return await super().message_handler(client, message)
