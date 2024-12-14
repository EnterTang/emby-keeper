import asyncio
import random

from telethon.tl.custom import Message
from telethon.errors import BadRequestError

from ._base import AnswerBotCheckin


class PornembyCheckin(AnswerBotCheckin):
    name = "Pornemby"
    bot_username = "Porn_Emby_Bot"
    bot_success_pat = r".*?(\d+)$"

    async def start(self):
        if not self.client.me.username:
            self.log.warning(f"签到失败: 需要设置用户名才可生效.")
            return None
        return await super().start()

    async def on_photo(self, message: Message):
        await asyncio.sleep(random.uniform(2, 4))
        async with self.client.catch_reply(self.bot_username) as f:
            try:
                await message.click(text="点击签到")
            except TimeoutError:
                pass
            except BadRequestError:
                self.log.warning(f"签到失败: 账户错误.")
                await self.fail()
            try:
                m = await asyncio.wait_for(f, 10)
            except asyncio.TimeoutError:
                self.log.warning(f"签到失败: 签到无回应, 您可能还没有注册 {self.name} Emby 账号.")
                await self.fail()
            else:
                return await self.on_text(m, m.text)
