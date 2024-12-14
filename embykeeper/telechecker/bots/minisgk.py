from ._base import BotCheckin
from telethon.tl.custom import Message
from telethon import Button, types

__ignore__ = True


class MiniSGKCheckin(BotCheckin):
    name = "迷你世界社工库"
    bot_username = "mnsjsgkbot"
    bot_checkin_cmd = "/sign"
    additional_auth = ["prime"]

    async def message_handler(self, client, message: Message):
        if message.buttons:
            buttons = [b for row in message.buttons for b in row]
            for button in buttons:
                if isinstance(button, Button.Inline) and "签到" in button.text:
                    result = await message.click(data=button.data)
                    if isinstance(result, types.UpdateBotCallbackQuery):
                        await self.on_text(Message(id=0), result.message)
                    return
            else:
                self.log.warning(f"签到失败: 账户错误.")
                return await self.fail()
