from telethon.tl.types import Message
from telethon import Button

from ._base import AnswerBotCheckin

__ignore__ = True


class TembyCheckin(AnswerBotCheckin):
    name = "Temby"
    bot_username = "HiEmbyBot"
    bot_checkin_cmd = "/hi"
    bot_success_keywords = ["Checkin successfully"]
    bot_checked_keywords = ["you have checked in already today"]

    async def on_answer(self, message: Message):
        await super().on_answer(message)
        if message.buttons:
            buttons = [b for row in message.buttons for b in row]
            if len(buttons) == 1:
                await message.click(data=buttons[0].data)
            else:
                for button in buttons:
                    if isinstance(button, Button.Inline) and "签到" in button.text:
                        await message.click(data=button.data)
                        return
                else:
                    self.log.warning(f"签到失败: 账户错误.")
                    return await self.fail()
