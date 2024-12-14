from datetime import datetime, timedelta
import random
import re

import asyncio
from typing import List
from cachetools import TTLCache
from telethon.tl.types import Message, User, Chat, ChatBannedRights
from telethon.tl.types import ChannelParticipantsAdmins, ChannelParticipantAdmin, ChannelParticipantCreator
from telethon.errors import BadRequestError
from telethon import events

from ..lock import pornemby_alert, pornemby_messager_mids
from ._base import Monitor


class PornembyAlertMonitor(Monitor):
    name = "Pornemby 风险急停监控"
    chat_name = "Pornemby"
    additional_auth = ["pornemby_pack"]
    allow_edit = True
    debug_no_log = True

    user_alert_keywords = ["脚本", "真人", "admin", "全是", "举报", "每次", "机器人", "report"]
    admin_alert_keywords = ["不要", "封", "ban", "warn", "踢", "抓"]
    alert_reply_keywords = ["真人", "脚本", "每次", "在吗", "机器", "封", "warn", "ban", "回", "说"]
    alert_reply_except_keywords = ["不要回复", "别回复", "勿回复"]
    reply_words = ["?" * (i + 1) for i in range(3)] + ["嗯?", "欸?", "🤔"]
    reply_interval = 7200

    async def init(self):
        self.lock = asyncio.Lock()
        self.last_reply = None
        self.alert_remaining = 0.0
        self.member_status_cache = TTLCache(maxsize=128, ttl=86400)
        self.member_status_cache_lock = asyncio.Lock()
        self.monitor_task = asyncio.create_task(self.monitor())
        self.pin_checked = False
        return True

    async def check_admin(self, chat: Chat, user: User):
        if not user:
            return True
        async with self.member_status_cache_lock:
            if not user.id in self.member_status_cache:
                try:
                    participants = await self.client(events.ChatAction(chat.id))
                    admins = await self.client.get_participants(chat.id, filter=ChannelParticipantsAdmins)
                    is_admin = any(admin.id == user.id for admin in admins)
                    self.member_status_cache[user.id] = is_admin
                except BadRequestError:
                    return False
        if self.member_status_cache[user.id]:
            if getattr(user, 'bot', False):
                return False
            else:
                return True

    def check_keyword(self, message: Message, keywords: List[str]):
        content = message.text or getattr(message, 'caption', None)
        if content:
            for k in keywords:
                match = re.search(k, content)
                if match:
                    return match.group(0)

    async def monitor(self):
        while True:
            await self.lock.acquire()
            while self.alert_remaining > 0:
                pornemby_alert[self.client.me.id] = True
                t = datetime.now()
                self.lock.release()
                await asyncio.sleep(1)
                await self.lock.acquire()
                self.alert_remaining -= (datetime.now() - t).total_seconds()
            else:
                pornemby_alert[self.client.me.id] = False
            self.lock.release()
            await asyncio.sleep(1)

    async def set_alert(self, time: float = None, reason: str = None):
        if time:
            async with self.lock:
                if self.alert_remaining > time:
                    return
                else:
                    msg = f"Pornemby 风险急停被触发, 停止操作 {time} 秒"
                    if reason:
                        msg += f" (原因: {reason})"
                    msg += "."
                    self.log.warning(msg)
                    self.alert_remaining = time
        else:
            msg = "Pornemby 风险急停被触发, 所有操作永久停止"
            if reason:
                msg += f" (原因: {reason})"
            msg += "."
            self.log.bind(log=True).error(msg)
            async with self.lock:
                self.alert_remaining = float("inf")

    async def check_pinned(self, message: Message):
        if getattr(message, 'action', None) and message.action.__class__.__name__ == 'MessageActionPinMessage':
            pinned_msg = await message.get_reply_message()
            return pinned_msg
        elif not any([message.text, message.media, getattr(message, 'action', None)]):
            messages = await self.client.get_messages(message.chat_id, filter=lambda m: m.pinned)
            if messages:
                return messages[0]
        return None

    async def on_trigger(self, message: Message, key, reply):
        # 管理员回复水群消息: 永久停止, 若存在关键词即回复
        # 用户回复水群消息, 停止 3600 秒, 若存在关键词即回复
        if message.reply_to_msg_id in pornemby_messager_mids.get(self.client.me.id, []):
            if await self.check_admin(message.chat, message.sender):
                await self.set_alert(reason="管理员回复了水群消息")
            else:
                await self.set_alert(3600, reason="非管理员回复了水群消息")
            if self.check_keyword(message, self.alert_reply_keywords):
                if not self.check_keyword(message, self.alert_reply_except_keywords):
                    if (not self.last_reply) or (
                        self.last_reply < datetime.now() - timedelta(seconds=self.reply_interval)
                    ):
                        await asyncio.sleep(random.uniform(5, 15))
                        await message.reply(random.choice(self.reply_words))
                        self.last_reply = datetime.now()
            return

        # 置顶消息, 若不在列表中停止 3600 秒, 否则停止 86400 秒
        pinned = await self.check_pinned(message)
        if pinned:
            self.pin_checked = True
            keyword = self.check_keyword(pinned, self.user_alert_keywords + self.admin_alert_keywords)
            if keyword:
                await self.set_alert(86400, reason=f'有新消息被置顶, 且包含风险关键词: "{keyword}"')
            else:
                await self.set_alert(3600, reason="有新消息被置顶")
            return

        if not self.pin_checked:
            messages = await self.client.get_messages(message.chat_id, filter=lambda m: m.pinned)
            if messages:
                self.pin_checked = True
                for pinned in messages:
                    keyword = self.check_keyword(pinned, self.user_alert_keywords + self.admin_alert_keywords)
                    if keyword:
                        await self.set_alert(86400, reason=f'检查到现有置顶消息中包含风险关键词: "{keyword}"')
                        break

        # 管理员发送消息, 若不在列表中停止 3600 秒, 否则停止 86400 秒
        # 用户发送列表中消息, 停止 1800 秒
        if await self.check_admin(message.chat, message.sender):
            keyword = self.check_keyword(message, self.user_alert_keywords + self.admin_alert_keywords)
            if keyword:
                await self.set_alert(86400, reason=f'管理员发送了消息, 且包含风险关键词: "{keyword}"')
            else:
                await self.set_alert(3600, reason="管理员发送了消息")
        else:
            keyword = self.check_keyword(message, self.user_alert_keywords)
            if keyword:
                await self.set_alert(1800, reason=f'非管理员发送了消息, 且包含风险关键词: "{keyword}"')
