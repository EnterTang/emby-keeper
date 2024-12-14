from __future__ import annotations

import asyncio
import random
import re
from contextlib import asynccontextmanager
import string
from typing import Awaitable, Callable, Iterable, List, Optional, Sized, Union, Dict, Any
from datetime import datetime

from loguru import logger
from appdirs import user_data_dir
from telethon import events, types
from telethon.errors import UsernameNotOccupiedError, ChatAdminRequiredError, FloodWaitError
from telethon.tl.types import Channel, User, Message

from embykeeper import __name__ as __product__
from embykeeper.utils import show_exception, to_iterable, truncate_str, AsyncCountPool

from ..tele import Client
from ..link import Link

__ignore__ = True


class Session:
    """回复检测会话, 用于检测跟随回复."""

    def __init__(self, reply, follows=None, delays=None):
        self.reply = reply
        self.follows = follows
        self.delays = delays
        self.lock = asyncio.Lock()
        self.delayed = asyncio.Event()
        self.followed = asyncio.Event()
        self.canceled = asyncio.Event()
        if not self.follows:
            return self.followed.set()

    async def delay(self):
        if not self.delayed:
            return self.delayed.set()
        if isinstance(self.delays, Sized) and len(self.delays) == 2:
            time = random.uniform(*self.delays)
        else:
            time = self.delays
        await asyncio.sleep(time)
        self.delayed.set()

    async def follow(self):
        async with self.lock:
            self.follows -= 1
            if self.follows <= 0:
                self.followed.set()
            return self.follows

    async def wait(self, timeout=240):
        task = asyncio.create_task(self.delay())
        try:
            await asyncio.wait_for(asyncio.gather(self.delayed.wait(), self.followed.wait()), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        else:
            return not self.canceled.is_set()

    async def cancel(self):
        self.canceled.set()
        self.delayed.set()
        self.followed.set()


class Monitor:
    """监控器类, 可以检测某个人在某个群中发送了某种模式的信息, 并触发特定的动作 (回复/向机器人注册) 等, 用于答题/抢注等."""

    group_pool = AsyncCountPool(base=1000)
    _message_cache: Dict[int, Dict[str, Any]] = {}  # 消息缓存
    _last_trigger_time: Dict[int, datetime] = {}  # 上次触发时间缓存

    name: str = None  # 监控器名称
    chat_name: str = None  # 监控的群聊名称
    chat_allow_outgoing: bool = False  # 是否支持自己发言触发
    chat_user: Union[str, List[str]] = []  # 仅被列表中用户的发言触发 (支持 username / userid)
    chat_keyword: Union[str, List[str]] = []  # 仅当消息含有列表中的关键词时触发, 支持 regex
    chat_probability: float = 1.0  # 发信概率 (0最低, 1最高)
    chat_delay: int = 0  # 发信延迟 (s)
    chat_follow_user: int = 0  # 需要等待 N 个用户发送 {chat_reply} 方可回复
    chat_reply: Union[
        str, Callable[[Message, Optional[Union[str, List[str]]]], Union[str, Awaitable[str]]]
    ] = None  # 回复的内容
    notify_create_name: bool = False  # 启动时生成 unique name 并提示, 用于抢注
    allow_edit: bool = False  # 编辑消息内容后也触发
    trigger_interval: float = 2  # 每次触发的最低时间间隔
    additional_auth: List[str] = []  # 额外认证要求
    debug_no_log = False  # 调试模式不显示冗余日志

    def __init__(self, client: Client, nofail=True, basedir=None, proxy=None, config: dict = {}):
        self.client = client
        self.nofail = nofail
        self.basedir = basedir or user_data_dir(__product__)
        self.proxy = proxy
        self.config = config
        self.log = logger.bind(scheme="telemonitor", name=self.name, username=client.me.name)
        self.session = None
        self.failed = asyncio.Event()
        self.lock = asyncio.Lock()
        
        # 初始化消息缓存
        self._message_cache[self.client.me.id] = {}
        self._last_trigger_time[self.client.me.id] = datetime.now()

    def get_event_handler(self):
        """设定要监控的事件处理器"""
        async def event_handler(event):
            if isinstance(event, (events.NewMessage.Event, events.MessageEdited.Event)):
                await self._message_handler(self.client, event.message)
        
        return event_handler

    @asynccontextmanager
    async def listener(self):
        """执行监控上下文"""
        group = await self.group_pool.append(self)
        handler = self.get_event_handler()
        
        # 注册事件处理器
        self.client._client.add_event_handler(
            handler,
            events.NewMessage(chats=self.chat_name, outgoing=self.chat_allow_outgoing)
        )
        if self.allow_edit:
            self.client._client.add_event_handler(
                handler,
                events.MessageEdited(chats=self.chat_name, outgoing=self.chat_allow_outgoing)
            )
        
        try:
            yield
        finally:
            # 清理事件处理器
            self.client._client.remove_event_handler(handler)
            # 清理缓存
            if self.client.me.id in self._message_cache:
                self._message_cache[self.client.me.id].clear()

    async def start(self):
        """监控器的入口函数"""
        try:
            chat = await self.client._client.get_entity(self.chat_name)
            
            if isinstance(chat, Channel):
                try:
                    await self.client._client.get_permissions(chat)
                except ChatAdminRequiredError:
                    self.log.info(f'跳过监控: 尚未加入群组 "{chat.title}".')
                    return False
                    
            if self.additional_auth:
                for auth in self.additional_auth:
                    if not await Link(self.client).auth(auth, log_func=self.log.info):
                        return False
                        
            if not await self.init():
                self.log.bind(log=True).warning(f"机器人状态初始化失败, 监控将停止.")
                return False
                
            if self.notify_create_name:
                self.unique_name = self.get_unique_name()
                
            spec = f"[green]{chat.title}[/] [gray50](@{chat.username})[/]" if hasattr(chat, 'title') else f"@{chat.username}"
            self.log.info(f"开始监视: {spec}.")
            
            async with self.listener():
                await self.failed.wait()
                self.log.error(f"发生错误, 不再监视: {spec}.")
                return False
                
        except UsernameNotOccupiedError:
            self.log.warning(f'初始化错误: 群组 "{self.chat_name}" 不存在.')
            return False
        except FloodWaitError as e:
            self.log.info(f"初始化信息: Telegram 要求等待 {e.seconds} 秒.")
            if e.seconds < 360:
                await asyncio.sleep(e.seconds)
            else:
                self.log.info(f"初始化信息: Telegram 要求等待 {e.seconds} 秒, 您可能操作过于频繁, 监控器将停止.")
                return False
        except Exception as e:
            if self.nofail:
                self.log.warning(f"发生初始化错误, 监控停止.")
                show_exception(e, regular=False)
                return False
            raise

    async def message_handler(self, client: Client, message: Message):
        """消息处理入口函数, 控制是否回复以及等待回复"""
        # 检查消息缓存，避免重复处理
        msg_id = str(message.id)
        if msg_id in self._message_cache.get(client.me.id, {}):
            return
            
        # 检查触发间隔
        now = datetime.now()
        last_trigger = self._last_trigger_time.get(client.me.id)
        if last_trigger and (now - last_trigger).total_seconds() < self.trigger_interval:
            return
            
        # 处理消息
        for key in self.keys(message):
            spec = self.get_spec(key)
            if not self.debug_no_log:
                self.log.info(f"监听到关键信息: {truncate_str(spec, 30)}.")
                
            if random.random() >= self.chat_probability:
                self.log.info(f"由于概率设置, 不予回应: {spec}.")
                return False
                
            reply = await self.get_reply(message, key)
            if self.session:
                await self.session.cancel()
                
            if self.chat_follow_user:
                self.log.info(f"将等待{self.chat_follow_user}个人回复: {reply}")
                
            self.session = Session(reply, follows=self.chat_follow_user, delays=self.chat_delay)
            
            if await self.session.wait():
                self.session = None
                async with self.lock:
                    await self.on_trigger(message, key, reply)
                    self._last_trigger_time[client.me.id] = now
                    # 缓存已处理的消息
                    self._message_cache.setdefault(client.me.id, {})[msg_id] = {
                        'time': now,
                        'key': key,
                        'reply': reply
                    }
        else:
            if self.session and not self.session.followed.is_set():
                text = message.text or message.caption
                if self.session.reply == text:
                    now = await self.session.follow()
                    self.log.info(
                        f'从众计数 ({self.chat_follow_user - now}/{self.chat_follow_user}): "{message.sender.first_name}"'
                    )

    async def on_trigger(self, message: Message, key: Optional[Union[List[str], str]], reply: str):
        """触发回调函数"""
        if reply:
            return await self.client.send_message(message.chat.id, reply)

    def get_unique_name(self):
        """获取唯一性用户名, 用于注册."""
        unique_name = self.config.get("unique_name", None)
        if unique_name:
            self.log.info(f'根据您的设置, 当监控到开注时, 该站点将以用户名 "{unique_name}" 注册.')
            if not re.search("^\w+$", unique_name):
                self.log.warning(f"用户名含有除 a-z, A-Z, 0-9, 以及下划线之外的字符, 可能导致注册失败.")
            return unique_name
        else:
            return Monitor.unique_cache[self.client.me]
