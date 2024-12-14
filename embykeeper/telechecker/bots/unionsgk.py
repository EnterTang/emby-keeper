from ._base import BotCheckin
from telethon.tl.custom import Message
from telethon import Button

__ignore__ = True


class UnionSGKCheckin(BotCheckin):
    name = "银联社工库"
    bot_username = "unionpaysgkbot"
    bot_checkin_cmd = "/start"
    additional_auth = ["prime"]
    bot_checked_keywords = ["今日已签到"]

    async def message_handler(self, client, message: Message):
        if message.text and "欢迎使用" in message.text and message.buttons:
            buttons = [b for row in message.buttons for b in row]
            for button in buttons:
                if isinstance(button, Button.Inline) and "签到" in button.text:
                    try:
                        await message.click(data=button.data)
                    except TimeoutError:
                        pass
                    return
            else:
                self.log.warning(f"签到失败: 账户错误.")
                return await self.fail()
        await super().message_handler(client, message)
