import emoji
from telethon.tl.custom import Message
from telethon import Button
from telethon.errors import RPCError

from ..link import Link
from ._base import AnswerBotCheckin


class TerminusCheckin(AnswerBotCheckin):
    name = "终点站"
    bot_username = "EmbyPublicBot"
    bot_checkin_cmd = ["/cancel", "/checkin"]
    bot_text_ignore = ["会话已取消", "没有活跃的会话"]
    bot_checked_keywords = ["今天已签到"]
    additional_auth = ["visual"]
    max_retries = 1
    bot_use_history = 3

    async def on_photo(self, message: Message):
        """分析分析传入的验证码图片并返回验证码."""
        if message.buttons:
            clean = lambda o: emoji.replace_emoji(o, "").replace(" ", "")
            buttons = [b for row in message.buttons for b in row]
            options = [b.text for b in buttons if isinstance(b, Button.Inline)]
            options_cleaned = [clean(o) for o in options]
            if len(options) < 2:
                return
            for i in range(3):
                result, by = await Link(self.client).visual(message.photo.id, options_cleaned)
                if result:
                    self.log.debug(f"已通过远端 ({by}) 解析答案: {result}.")
                    break
                else:
                    self.log.warning(f"远端解析失败, 正在重试解析 ({i + 1}/3).")
            else:
                self.log.warning(f"签到失败: 验证码识别错误.")
                return await self.fail()
            result = options[options_cleaned.index(result)]
            try:
                await message.click(text=result)
            except RPCError:
                self.log.warning("按钮点击失败.")
