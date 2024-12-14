from telethon.tl.types import Message
from telethon.errors import RPCError
from thefuzz import fuzz

from ._base import AnswerBotCheckin

__ignore__ = True


class LJYYCheckin(AnswerBotCheckin):
    ocr = "uchars4@v1"

    name = "垃圾影音"
    bot_username = "zckllflbot"
    bot_captcha_len = 4
    bot_use_history = 20
    bot_text_ignore = "下列选项"

    async def retry(self):
        if self.message:
            try:
                await self.message.click()
            except (RPCError, TimeoutError):
                pass
        await super().retry()

    async def on_captcha(self, message: Message, captcha: str):
        async with self.operable:
            if not self.message:
                await self.operable.wait()
            if message.buttons:
                buttons = [b for row in message.buttons for b in row]
                match = [(b.text, fuzz.ratio(b.text, captcha)) for b in buttons]
                max_button, max_ratio = max(match, key=lambda x: x[1])
                for button in buttons:
                    if button.text == max_button:
                        await message.click(data=button.data)
                        break
