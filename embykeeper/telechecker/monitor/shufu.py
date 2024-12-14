import asyncio
from telethon.tl.types import Message
from telethon import Button

from embykeeper.utils import async_partial

from ._base import Monitor


class ShufuMonitor(Monitor):
    name = "叔服"
    chat_name = -1001890341413
    chat_keyword = r"SHUFU-\d+-Register_[\w]+"
    bot_username = "dashu660_bot"
    notify_create_name = True
    additional_auth = ["prime"]

    async def on_trigger(self, message: Message, key, reply):
        wr = async_partial(self.client.wait_reply, self.bot_username)
        for _ in range(3):
            try:
                msg = await wr("/start")
                if "请确认好重试" in (msg.text or msg.raw_text):
                    continue
                elif "欢迎进入用户面板" in (msg.text or msg.raw_text) and msg.buttons:
                    keys = [k.text for row in msg.buttons for k in row]
                    for k in keys:
                        if "使用注册码" in k:
                            async with self.client.conversation(self.bot_username) as conv:
                                try:
                                    await msg.click(text=k)
                                    response = await conv.wait_event(pattern=r".*对我发送.*", timeout=10)
                                    if response:
                                        break
                                except asyncio.TimeoutError:
                                    continue
                    else:
                        continue
                    msg = await wr(key)
                    if "注册码已被使用" in (msg.text or msg.raw_text):
                        self.log.info(f'已向 Bot @{self.bot_username} 发送了邀请码: "{key}", 但是已被抢注了.')
                    else:
                        self.log.bind(msg=True).info(
                            f'已向 Bot @{self.bot_username} 发送了邀请码: "{key}", 请查看.'
                        )
                    break
                else:
                    continue
            except asyncio.TimeoutError:
                pass
        else:
            self.log.bind(msg=True).warning(f"已监控到{self.name}的邀请码, 但自动使用失败, 请自行查看.")
