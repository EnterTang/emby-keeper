from __future__ import annotations

import binascii
from collections import OrderedDict
from contextlib import asynccontextmanager
import uuid
from datetime import datetime, timezone
import asyncio
import inspect
from pathlib import Path
import pickle
import random
import sys
from typing import AsyncGenerator, Optional, Union
from sqlite3 import OperationalError
import logging

from rich.prompt import Prompt
from appdirs import user_data_dir
from loguru import logger
from telethon import TelegramClient, events, types, Button
from telethon.tl import functions, types as tl_types
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyUnregisteredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneNumberInvalidError,
    PhoneNumberBannedError,
    UnauthorizedError,
    RPCError,
)
from telethon.errors.rpcerrorlist import (
    ApiIdPublishedFloodError,
)
from telethon.sessions import StringSession
from aiocache import Cache
import aiohttp
from aiohttp_socks import ProxyConnector, ProxyType, ProxyConnectionError, ProxyTimeoutError

from embykeeper import var, __name__ as __product__, __version__
from embykeeper.utils import async_partial, show_exception, to_iterable

logger = logger.bind(scheme="telegram")

_id = b"\x80\x04\x95\x15\x00\x00\x00\x00\x00\x00\x00]\x94(K2K3K7K8K5K8K4K6e."
_hash = b"\x80\x04\x95E\x00\x00\x00\x00\x00\x00\x00]\x94(KbKdK7K4K0KeKaK2KaKcKeKeK7K3K9K0KeK0KbK3K5K4KeKcK8K0K9KcK8K7K0Kfe."
_decode = lambda x: "".join(map(chr, to_iterable(pickle.loads(x))))

API_KEY = {
    "_": {"api_id": _decode(_id), "api_hash": _decode(_hash)}
}

def _name(self: Union[types.User, types.Chat]):
    return " ".join([n for n in (self.first_name, self.last_name) if n])

def _chat_name(self: types.Chat):
    if self.title:
        return self.title
    else:
        return _name(self)

# Add name properties to Telethon types
setattr(types.User, "name", property(_name))
setattr(types.Chat, "name", property(_chat_name))

class LogRedirector(logging.StreamHandler):
    def emit(self, record):
        try:
            if record.levelno >= logging.WARNING:
                logger.debug(f"Telethon log: {record.getMessage()}")
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            self.handleError(record)

telethon_logger = logging.getLogger("telethon")
for h in telethon_logger.handlers[:]:
    telethon_logger.removeHandler(h)
telethon_logger.addHandler(LogRedirector())

class Dispatcher:
    def __init__(self, client: TelegramClient):
        self.client = client
        self.groups = {}
        self.updates_queue = asyncio.Queue()
        self.loop = asyncio.get_event_loop()
        self.mutex = asyncio.Lock()
        self.handler_worker_tasks = []

    async def start(self):
        logger.debug("Telegram 更新分配器启动.")
        if not getattr(self.client, 'no_updates', False):
            self.handler_worker_tasks = [
                self.loop.create_task(self.handler_worker()) 
                for _ in range(getattr(self.client, 'workers', 1))
            ]

    def add_handler(self, handler, group: int):
        async def fn():
            async with self.mutex:
                if group not in self.groups:
                    self.groups[group] = []
                    self.groups = OrderedDict(sorted(self.groups.items()))
                self.groups[group].append(handler)

        return self.loop.create_task(fn())

    def remove_handler(self, handler, group: int):
        async def fn():
            async with self.mutex:
                if group not in self.groups:
                    raise ValueError(f"Group {group} does not exist. Handler was not removed.")
                self.groups[group].remove(handler)

        return self.loop.create_task(fn())

    async def handler_worker(self):
        while True:
            packet = await self.updates_queue.get()
            
            if packet is None:
                break

            try:
                update, users, chats = packet
                
                async with self.mutex:
                    groups = {i: g[:] for i, g in self.groups.items()}

                for group in groups.values():
                    for handler in group:
                        args = None

                        if isinstance(handler, events.NewMessage.Event):
                            try:
                                if await handler.filter(update):
                                    args = (update,)
                            except Exception as e:
                                logger.warning(f"Telegram 错误: {e}")
                                continue

                        elif isinstance(handler, events.Raw):
                            args = (update, users, chats)

                        if args is None:
                            continue

                        try:
                            if inspect.iscoroutinefunction(handler.callback):
                                await handler.callback(self.client, *args)
                            else:
                                await self.loop.run_in_executor(
                                    getattr(self.client, 'executor', None), 
                                    handler.callback,
                                    self.client,
                                    *args
                                )
                        except events.StopPropagation:
                            raise
                        except Exception as e:
                            logger.error(f"更新回调函数内发生错误.")
                            show_exception(e, regular=False)
                        break
                    else:
                        continue
                    break
            except events.StopPropagation:
                pass
            except TimeoutError:
                logger.info("网络不稳定, 可能遗漏消息.")
            except Exception as e:
                logger.error("更新控制器错误.")
                show_exception(e, regular=False)


class Client(TelegramClient):
    def __init__(self, *args, **kw):
        self.phone_number = kw.pop('phone_number', None)
        self.phone_code = kw.pop('phone_code', None) 
        self.password = kw.pop('password', None)
        self.bot_token = kw.pop('bot_token', None)
        self.session_string = kw.pop('session_string', None)
        self.in_memory = kw.pop('in_memory', True)
        self.workdir = kw.pop('workdir', None)
        
        # Remove system info parameters that aren't needed
        name = kw.pop('name', None)
        kw.pop('device_model', None)
        kw.pop('system_version', None)
        kw.pop('app_version', None)
        kw.pop('lang_code', None)
        kw.pop('sleep_threshold', None)
        
        # Session handling
        if self.session_string and len(self.session_string.strip()) > 0:
            try:
                session = StringSession(self.session_string.strip())
            except ValueError:
                logger.warning(f'账号 "{self.phone_number}" 的 session 字符串无效, 将尝试重新登录.')
                session = StringSession()
        else:
            if self.in_memory:
                session = StringSession()
            else:
                session = str(Path(self.workdir) / name) if name else StringSession()
                
        # Initialize TelegramClient first
        super().__init__(
            session,
            kw.pop('api_id'),
            kw.pop('api_hash'),
            **kw
        )
        
        # Initialize additional attributes
        self.cache = Cache()
        self.lock = asyncio.Lock()
        self.dispatcher = Dispatcher(self)
        self._last_special_invoke = {}
        self._special_invoke_lock = asyncio.Lock()
        self._last_invoke = {}
        self._invoke_lock = asyncio.Lock()
        self._login_time: datetime = None
        self.me = None
        self._client = self

    async def start(self, *args, **kwargs):
        """Override start method to get user info"""
        await TelegramClient.start(self, *args, **kwargs)
        self.me = await self.get_me()
        return self

    async def authorize(self):
        if self.bot_token:
            await self.sign_in(bot_token=self.bot_token)
            return await self.get_me()
            
        retry = False
        while True:
            try:
                if not self.phone_code:
                    result = await self.send_code_request(self.phone_number)
                    code_type = {
                        "app": "Telegram 客户端",
                        "sms": "短信",
                        "call": "来电",
                        "flash_call": "闪存呼叫",
                        "fragment_sms": "Fragment 信",
                        "email": "邮件"
                    }.get(result.type, "未知")
                    
                    if retry:
                        msg = f'验证码错误, 请重新输入 "{self.phone_number}" 的登录验证码 (按回车确认)'
                    else:
                        msg = f'请从{code_type}接收 "{self.phone_number}" 的登录验证码 (按回车确认)'
                    try:
                        self.phone_code = Prompt.ask(" " * 23 + msg, console=var.console)
                    except EOFError:
                        raise ApiIdInvalidError(
                            f'登录 "{self.phone_number}" 时出现异常: 您正在使用非交互式终端, 无法输入验证码.'
                        )
                
                try:
                    signed_in = await self.sign_in(phone=self.phone_number, code=self.phone_code)
                except SessionPasswordNeededError:
                    retry = False
                    while True:
                        if not self.password:
                            if retry:
                                msg = f'密码错误, 请重新输入 "{self.phone_number}" 的两步验证密码 (不显示, 按回车确认)'
                            else:
                                msg = f'需要输入 "{self.phone_number}" 的两步验证密码 (不显示, 按回车确认)'
                            self.password = Prompt.ask(" " * 23 + msg, password=True, console=var.console)
                        try:
                            return await self.sign_in(password=self.password)
                        except ApiIdInvalidError:
                            self.password = None
                            retry = True
                else:
                    return signed_in
                    
            except PhoneCodeInvalidError:
                self.phone_code = None
                retry = True
            except FloodWaitError:
                raise ApiIdInvalidError(f'登录 "{self.phone_number}" 时出现异常: 登录过于频繁.')
            except PhoneNumberInvalidError:
                raise ApiIdInvalidError(
                    f'登录 "{self.phone_number}" 时出现异常: 您使用了错误手机号 (格式错误或没有册).'
                )
            except PhoneNumberBannedError:
                raise ApiIdInvalidError(f'登录 "{self.phone_number}" 时出现异常: 您的账户已被封禁.')
            except Exception as e:
                logger.error(f"登录时出现异常错误!")
                show_exception(e, regular=False)
                retry = True

    def add_handler(self, handler, group: int = 0):
        return self.dispatcher.add_handler(handler, group)

    def remove_handler(self, handler, group: int = 0):
        return self.dispatcher.remove_handler(handler, group)

    async def get_dialogs(self, limit: int = 0, exclude_pinned=None, folder_id=None) -> Optional[AsyncGenerator["types.Dialog", None]]:
        async with self.lock:
            cache_id = f"dialogs_{self.phone_number}_{folder_id}_{1 if exclude_pinned else 0}"
            offset_date = 0
            offset_id = 0
            offset_peer = types.InputPeerEmpty()
            cache = []
            
            try:
                (offset_id, offset_date, offset_peer), cache = await self.cache.get(cache_id)
            except:
                pass

            current = 0
            total = limit or (1 << 31) - 1
            limit = min(100, total)

            for c in cache:
                yield c
                current += 1
                if current >= total:
                    return

            while True:
                result = await self(functions.messages.GetDialogs(
                    offset_date=offset_date,
                    offset_id=offset_id,
                    offset_peer=offset_peer,
                    limit=limit,
                    hash=0,
                    exclude_pinned=exclude_pinned,
                    folder_id=folder_id
                ))

                dialogs = result.dialogs
                messages = {m.id: m for m in result.messages}
                users = {u.id: u for u in result.users}
                chats = {c.id: c for c in result.chats}

                for dialog in dialogs:
                    yield dialog

                    current += 1
                    if current >= total:
                        return

                if not dialogs:
                    return

                last = dialogs[-1]
                offset_date = messages[last.top_message].date
                offset_peer = dialog.peer
                offset_id = last.top_message

                await self.cache.set(
                    cache_id,
                    ((offset_id, offset_date, offset_peer), cache + dialogs),
                    ttl=120
                )

    @asynccontextmanager
    async def catch_reply(self, chat_id: Union[int, str], outgoing=False, filter=None):
        future = asyncio.Future()
        
        async def handler(event):
            try:
                future.set_result(event.message)
            except asyncio.InvalidStateError:
                pass
                
        builder = events.NewMessage(chats=chat_id)
        if not outgoing:
            builder.outgoing = False
        if filter:
            original_filter = builder.filter
            
            async def combined_filter(event):
                return await original_filter(event) and await filter(event)
                
            builder.filter = combined_filter
            
        self.add_event_handler(handler, builder)
        try:
            yield future
        finally:
            self.remove_event_handler(handler)

    @asynccontextmanager
    async def catch_edit(self, message: types.Message, filter=None):
        future = asyncio.Future()
        
        async def handler(event):
            if event.message.id == message.id:
                try:
                    future.set_result(event.message)
                except asyncio.InvalidStateError:
                    pass
                    
        builder = events.MessageEdited()
        if filter:
            original_filter = builder.filter
            
            async def combined_filter(event):
                return await original_filter(event) and await filter(event)
                
            builder.filter = combined_filter
            
        self.add_event_handler(handler, builder)
        try:
            yield future
        finally:
            self.remove_event_handler(handler)

    async def wait_reply(
        self, chat_id: Union[int, str], send: str = None, timeout: float = 10, outgoing=False, filter=None
    ):
        async with self.catch_reply(chat_id=chat_id, filter=filter, outgoing=outgoing) as f:
            if send:
                await self.send_message(chat_id, send)
            msg = await asyncio.wait_for(f, timeout)
            return msg

    async def wait_edit(
        self,
        message: types.Message,
        click: Union[str, int] = None,
        timeout: float = 10,
        noanswer=True,
        filter=None,
    ):
        async with self.catch_edit(message, filter=filter) as f:
            if click:
                try:
                    if isinstance(click, str):
                        await message.click(text=click)
                    else:
                        await message.click(data=click)
                except TimeoutError:
                    if noanswer:
                        pass
                    else:
                        raise
            msg = await asyncio.wait_for(f, timeout)
            return msg

    async def mute_chat(self, chat_id: Union[int, str], until: Union[int, datetime]):
        if isinstance(until, datetime):
            until = int(until.timestamp())

        return await self(
            functions.account.UpdateNotifySettings(
                peer=types.InputNotifyPeer(
                    peer=await self.get_input_entity(chat_id)
                ),
                settings=types.InputPeerNotifySettings(
                    show_previews=False,
                    mute_until=until,
                ),
            )
        )

    async def handle_updates(self, updates):
        self.last_update_time = datetime.now()

        if isinstance(updates, (types.Updates, types.UpdatesCombined)):
            users = {u.id: u for u in updates.users}
            chats = {c.id: c for c in updates.chats}

            for update in updates.updates:
                channel_id = getattr(
                    getattr(getattr(update, "message", None), "peer_id", None), "channel_id", None
                ) or getattr(update, "channel_id", None)

                pts = getattr(update, "pts", None)
                pts_count = getattr(update, "pts_count", None)

                if isinstance(update, types.UpdateNewChannelMessage) and pts and pts_count:
                    message = update.message

                    if not isinstance(message, types.MessageEmpty):
                        try:
                            diff = await self(
                                functions.updates.GetChannelDifference(
                                    channel=await self.get_input_entity(channel_id),
                                    filter=types.ChannelMessagesFilter(
                                        ranges=[
                                            types.MessageRange(
                                                min_id=update.message.id,
                                                max_id=update.message.id
                                            )
                                        ]
                                    ),
                                    pts=pts - pts_count,
                                    limit=pts,
                                )
                            )
                        except Exception:
                            pass
                        else:
                            if not isinstance(diff, types.updates.ChannelDifferenceEmpty):
                                users.update({u.id: u for u in diff.users})
                                chats.update({c.id: c for c in diff.chats})

                self.dispatcher.updates_queue.put_nowait((update, users, chats))
        elif isinstance(updates, (types.UpdateShortMessage, types.UpdateShortChatMessage)):
            diff = await self(
                functions.updates.GetDifference(
                    pts=updates.pts - updates.pts_count,
                    date=updates.date,
                    qts=-1
                )
            )

            if diff.new_messages:
                self.dispatcher.updates_queue.put_nowait(
                    (
                        types.UpdateNewMessage(
                            message=diff.new_messages[0],
                            pts=updates.pts,
                            pts_count=updates.pts_count
                        ),
                        {u.id: u for u in diff.users},
                        {c.id: c for c in diff.chats},
                    )
                )
            else:
                if diff.other_updates:
                    self.dispatcher.updates_queue.put_nowait((diff.other_updates[0], {}, {}))
        elif isinstance(updates, types.UpdateShort):
            self.dispatcher.updates_queue.put_nowait((updates.update, {}, {}))
        elif isinstance(updates, types.UpdatesTooLong):
            await self(functions.updates.GetState())
            logger.warning(f"发生超长更新, 已尝试处理该更新, 部分消息可能遗漏.")


class ClientsSession:
    pool = {}
    lock = asyncio.Lock()
    watch = None

    @classmethod
    def from_config(cls, config, in_memory=True, quiet=False, **kw):
        accounts = config.get("telegram", [])
        for k, (v, d) in kw.items():
            accounts = [a for a in accounts if a.get(k, d) in to_iterable(v)]
        return cls(
            accounts=accounts,
            proxy=config.get("proxy", None),
            basedir=config.get("basedir", None),
            in_memory=in_memory,
            quiet=quiet,
        )

    @classmethod
    async def watchdog(cls, timeout=120):
        logger.debug("Telegram 账号池看门狗启动.")
        try:
            counter = {}
            while True:
                await asyncio.sleep(10)
                for p in list(cls.pool):
                    try:
                        if cls.pool[p][1] <= 0:
                            if p in counter:
                                counter[p] += 1
                                if counter[p] >= timeout / 10:
                                    counter[p] = 0
                                    await cls.clean(p)
                            else:
                                counter[p] = 1
                        else:
                            counter.pop(p, None)
                    except (TypeError, KeyError):
                        pass
        except asyncio.CancelledError:
            await cls.shutdown()

    @classmethod
    async def clean(cls, phone):
        async with cls.lock:
            entry = cls.pool.get(phone, None)
            if not entry:
                return
            try:
                client, ref = entry
            except TypeError:
                return
            if not ref:
                logger.debug(f'出账号 "{client.phone_number}".')
                await client.stop()
                cls.pool.pop(phone, None)

    @classmethod
    async def clean_all(cls):
        for phone in list(cls.pool):
            await cls.clean(phone)

    @classmethod
    async def shutdown(cls):
        print("\r正在停止...\r", end="", flush=True, file=sys.stderr)
        for v in cls.pool.values():
            if isinstance(v, asyncio.Task):
                v.cancel()
            else:
                client, ref = v
                client.dispatcher.updates_queue.put_nowait(None)
                for t in client.dispatcher.handler_worker_tasks:
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
        while len(asyncio.all_tasks()) > 1:
            await asyncio.sleep(0.1)
        print(f"Telegram 账号池停止.\r", end="", file=sys.stderr)
        for v in cls.pool.values():
            if isinstance(v, tuple):
                client: Client = v[0]
                await client.storage.save()
                await client.storage.close()
                logger.debug(f'登出账号 "{client.phone_number}".')

    def __init__(self, accounts, proxy=None, basedir=None, in_memory=True, quiet=False):
        self.accounts = accounts
        self.proxy = proxy
        self.basedir = basedir or user_data_dir(__product__)
        self.phones = []
        self.done = asyncio.Queue()
        self.in_memory = in_memory
        self.quiet = quiet
        if not self.watch:
            self.__class__.watch = asyncio.create_task(self.watchdog())

    def get_connector(self, proxy=None, **kw):
        if proxy:
            connector = ProxyConnector(
                proxy_type=ProxyType[proxy["scheme"].upper()],
                host=proxy["hostname"],
                port=proxy["port"],
                username=proxy.get("username", None),
                password=proxy.get("password", None),
                **kw,
            )
        else:
            connector = aiohttp.TCPConnector(**kw)
        return connector

    async def test_network(self, proxy=None):
        url = "https://www.gstatic.com/generate_204"
        connector = self.get_connector(proxy=proxy)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                async with session.get(url) as resp:
                    if resp.status == 204:
                        return True
                    else:
                        logger.warning(f"检测网络状态发生错误, 网络检测将被跳过.")
                        return False
            except (ProxyConnectionError, ProxyTimeoutError) as e:
                un = connector._proxy_username
                pw = connector._proxy_password
                auth = f"{un}:{pw}@" if un or pw else ""
                proxy_url = f"{connector._proxy_type.name.lower()}://{auth}{connector._proxy_host}:{connector._proxy_port}"
                logger.warning(
                    f"无法连接到您的代理 ({proxy_url}), 您的网络状态可能不好, 敬请注意. 程序将继续运行."
                )
            except OSError as e:
                logger.warning(f"无法连接到网络 (Google), 您的网络状态可能不好, 敬请注意. 程序将继续运行.")
                return False
            except Exception as e:
                logger.warning(f"检测网络状态时发生错误, 网络检测将被跳过.")
                show_exception(e)
                return False

    async def test_time(self, proxy=None):
        url = "https://timeapi.io/api/Time/current/zone?timeZone=UTC"
        connector = self.get_connector(proxy=proxy)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        resp_dict: dict = await resp.json()
                    else:
                        logger.warning(f"世界时间接口异常, 系统时间检测将跳过, 敬请注意. 程序将继续运行.")

                api_time_str = resp_dict["dateTime"]
                api_time = datetime.strptime(api_time_str.split(".")[0], "%Y-%m-%dT%H:%M:%S")
                api_time = api_time.replace(tzinfo=timezone.utc)
                api_timestamp = api_time.timestamp()

                nowtime = datetime.now(timezone.utc).timestamp()
                if abs(nowtime - api_timestamp) > 30:
                    logger.warning(
                        f"您的系统时间设置不正确, 与世界时间差距过大, 可能会导致连接失败, 敬注意. 程序将继续运行."
                    )
            except Exception as e:
                logger.warning(f"检测世界时间发生错误, 时间检测将被跳过.")
                show_exception(e)
                return False

    async def login(self, account, proxy):
        try:
            account["phone"] = "".join(account["phone"].split())
            Path(self.basedir).mkdir(parents=True, exist_ok=True)
            session_file = Path(self.basedir) / f'{account["phone"]}.session'
            session_string_file = Path(self.basedir) / f'{account["phone"]}.login'
            if not self.quiet:
                logger.info(f'登录至账号 "{account["phone"]}".')
            for _ in range(3):
                if account.get("api_id", None) is None or account.get("api_hash", None) is None:
                    account.update(random.choice(list(API_KEY.values())))
                config_session_string = session_string = account.get("session", None)
                file_session_string = None
                if not session_string:
                    if session_string_file.is_file():
                        with open(session_string_file, encoding="utf-8") as f:
                            file_session_string = session_string = f.read().strip()
                if self.in_memory is None:
                    in_memory = True
                    if not session_string:
                        if session_file.is_file():
                            in_memory = False
                elif session_string:
                    in_memory = True
                else:
                    in_memory = self.in_memory
                if session_string or session_file.is_file():
                    logger.debug(
                        f'账号 "{account["phone"]}" 登录凭据存在, 仅内存模式{"启用" if in_memory else "禁用"}.'
                    )
                else:
                    logger.debug(
                        f'账号 "{account["phone"]}" 登录凭据不存在, 即进入登录流程, 仅内存模式{"启用" if in_memory else "禁用"}.'
                    )
                try:
                    client = Client(
                        app_version=__version__,
                        device_model="Server",
                        name=account["phone"],
                        system_version="Windows 11 x64",
                        lang_code="zh-CN",
                        api_id=account["api_id"],
                        api_hash=account["api_hash"],
                        phone_number=account["phone"],
                        session_string=session_string,
                        in_memory=in_memory,
                        proxy=proxy,
                        workdir=self.basedir,
                    )
                    try:
                        await asyncio.wait_for(client.start(), 120)
                    except asyncio.TimeoutError:
                        if proxy:
                            logger.error(f"无法连接到 Telegram 服务器, 请检查您代理的可用性.")
                            continue
                        else:
                            logger.error(f"无法连接到 Telegram 服务器, 请检查您的网络.")
                            continue
                except OperationalError as e:
                    logger.warning(f"内部数据库误, 正在重置, 您可能需要重新登录.")
                    show_exception(e)
                    session_file.unlink(missing_ok=True)
                except ApiIdPublishedFloodError:
                    logger.warning(f'登录账号 "{account["phone"]}" 时发生 API key 限制, 将被跳过.')
                    break
                except UnauthorizedError:
                    if config_session_string:
                        logger.error(
                            f'账号 "{account["phone"]}" 由于配置中提供的 session 被销, 将被跳过.'
                        )
                        show_exception(e)
                        break
                    elif file_session_string:
                        logger.error(f'账号 "{account["phone"]}" 已被��销, 将在 3 秒后重新登录.')
                        show_exception(e)
                        session_string_file.unlink(missing_ok=True)
                        continue
                    elif client.in_memory:
                        logger.error(f'账号 "{account["phone"]}" 被注销, 将在 3 秒后重新登录.')
                        show_exception(e)
                        continue
                    else:
                        logger.error(f'账号 "{account["phone"]}" 已被注销, 将在 3 秒后重新登录.')
                        show_exception(e)
                        await client.storage.delete()
                except KeyError as e:
                    logger.warning(
                        f'登录账号 "{account["phone"]}" 时发生异常, 可能是由于网络错误, 将在 3 秒后重试.'
                    )
                    show_exception(e)
                    await asyncio.sleep(3)
                else:
                    break
            else:
                logger.error(f'登录账号 "{account["phone"]}" 失败次数超限, 将被跳过.')
                return None
        except asyncio.CancelledError:
            raise
        except binascii.Error:
            logger.error(
                f'登录账号 "{account["phone"]}" 失败, 由于您在��置文件中提供的 session 无效, 将被跳过.'
            )
        except RPCError as e:
            logger.error(f'登录账号 "{account["phone"]}" 失败 ({e.MESSAGE.format(value=e.value)}), 将被跳过.')
            return None
        except Exception as e:
            if "bad message" in str(e).lower() and "synchronized" in str(e).lower():
                logger.error(
                    f'登录账号 "{account["phone"]}" 时发生异常, 可能是因为您的系统时间与世界时间差距过大, 将被跳过.'
                )
                return None
            else:
                logger.error(f'登录账号 "{account["phone"]}" 时发生异常, 将被跳过.')
                show_exception(e, regular=False)
                return None
        else:
            if not session_string_file.exists():
                with open(session_string_file, "w+", encoding="utf-8") as f:
                    f.write(await client.export_session_string())
            logger.debug(f'登录账号 "{client.phone_number}" 成功.')
            client._login_time = datetime.now()
            return client

    async def loginer(self, account):
        client = await self.login(account, proxy=self.proxy)
        if isinstance(client, Client):
            async with self.lock:
                phone = account["phone"]
                self.pool[phone] = (client, 1)
                self.phones.append(phone)
                await self.done.put(client)
                logger.debug(f"Telegram 账号池计数增加: {phone} => 1")
        else:
            await self.done.put(None)

    async def __aenter__(self):
        await self.test_network(self.proxy)
        asyncio.create_task(self.test_time(self.proxy))
        for a in self.accounts:
            phone = a["phone"]
            try:
                await self.lock.acquire()
                if phone in self.pool:
                    if isinstance(self.pool[phone], asyncio.Task):
                        self.lock.release()
                        await self.pool[phone]
                        await self.lock.acquire()
                    if isinstance(self.pool[phone], asyncio.Task):
                        continue
                    client, ref = self.pool[phone]
                    ref += 1
                    self.pool[phone] = (client, ref)
                    self.phones.append(phone)
                    await self.done.put(client)
                    logger.debug(f"Telegram 账号池计数增加: {phone} => {ref}")
                else:
                    self.pool[phone] = asyncio.create_task(self.loginer(a))
            finally:
                try:
                    self.lock.release()
                except RuntimeError:
                    pass
        return self

    def __aiter__(self):
        async def aiter():
            for _ in range(len(self.accounts)):
                client: Client = await self.done.get()
                if client:
                    yield client

        return aiter()

    async def __aexit__(self, type, value, tb):
        async with self.lock:
            for phone in self.phones:
                entry = self.pool.get(phone, None)
                if entry:
                    client, ref = entry
                    ref -= 1
                    self.pool[phone] = (client, ref)
                    logger.debug(f"Telegram 账号池计数降低: {phone} => {ref}")
